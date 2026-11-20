#!/usr/bin/env python3
"""Resolve upstream logicog/RTLPlayground main/ref state, compatibility, and release status."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = "S1100W-8GT-1SX-CI/2.0"
REQUIRED_UPSTREAM_FILES = (
    "machine/HASIVO_S1100WP_8GT_1SX_SE.h",
    "installer/Makefile",
)
VERSION_RE = re.compile(r"^VERSION\s*=\s*(\S+)", re.MULTILINE)


def github_headers(token: str = "") -> dict[str, str]:
    """Build standard GitHub REST API headers."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_url(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    retries: int = 3,
    timeout: int = 15,
) -> tuple[int, bytes]:
    """Fetch a URL with exponential backoff on transient failures."""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return 404, b""
            last_err = err
            if err.code not in (429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError as err:
            last_err = err

        if attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))

    raise RuntimeError(f"Failed to {method} {url} after {retries} attempts: {last_err}")


def resolve_upstream_commit(upstream: str, ref: str, token: str) -> tuple[str, str, str]:
    """Resolve `ref` on `upstream` to (full_sha, commit_date, first_subject_line)."""
    encoded_ref = urllib.parse.quote(ref, safe="")
    api_url = f"https://api.github.com/repos/{upstream}/commits/{encoded_ref}"
    status, payload = fetch_url(api_url, headers=github_headers(token))
    if status != 200:
        raise RuntimeError(f"Failed to resolve ref '{ref}' in {upstream} (HTTP {status}).")

    data = json.loads(payload.decode("utf-8"))
    full_sha = str(data.get("sha", "")).strip()
    if not full_sha:
        raise RuntimeError(f"Missing commit SHA when resolving '{ref}' in {upstream}.")

    commit_info = data.get("commit", {})
    commit_date = (
        commit_info.get("committer", {}).get("date")
        or commit_info.get("author", {}).get("date")
        or ""
    ).strip()
    message = str(commit_info.get("message", "")).strip()
    subject = message.splitlines()[0].strip() if message else ""
    return full_sha, commit_date, subject


def is_upstream_tag(upstream: str, ref: str, token: str) -> bool:
    """Return True if `ref` resolves to a git tag in `upstream`."""
    if ref in ("main", "master"):
        return False
    encoded_ref = urllib.parse.quote(ref, safe="")
    api_url = f"https://api.github.com/repos/{upstream}/git/ref/tags/{encoded_ref}"
    status, _ = fetch_url(api_url, headers=github_headers(token))
    return status == 200


def get_makefile_version(upstream: str, commit_sha: str) -> str:
    """Fetch top-level Makefile from `upstream@commit_sha` and extract `VERSION`."""
    encoded_sha = urllib.parse.quote(commit_sha, safe="")
    raw_url = f"https://raw.githubusercontent.com/{upstream}/{encoded_sha}/Makefile"
    status, payload = fetch_url(raw_url)
    if status != 200:
        raise RuntimeError(
            f"Failed to fetch Makefile at {upstream}@{commit_sha[:7]} (HTTP {status})."
        )
    match = VERSION_RE.search(payload.decode("utf-8", errors="replace"))
    if not match:
        raise RuntimeError(
            f"Could not parse VERSION from {upstream}@{commit_sha[:7]}/Makefile."
        )
    return match.group(1).strip()


def is_ref_compatible(upstream: str, commit_sha: str) -> tuple[bool, str]:
    """Verify the upstream commit includes S1100W machine support and OEM installer."""
    encoded_sha = urllib.parse.quote(commit_sha, safe="")
    missing: list[str] = []
    for rel_path in REQUIRED_UPSTREAM_FILES:
        raw_url = f"https://raw.githubusercontent.com/{upstream}/{encoded_sha}/{rel_path}"
        status, _ = fetch_url(raw_url, method="HEAD")
        if status != 200:
            missing.append(rel_path)

    if missing:
        return False, f"missing {', '.join(missing)}"
    return True, "compatible"


def release_exists(repo: str, tag: str, token: str) -> bool:
    """Return True if a GitHub release for `tag` already exists in `repo`."""
    if not repo:
        return False
    encoded_tag = urllib.parse.quote(tag, safe="")
    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{encoded_tag}"
    status, _ = fetch_url(api_url, headers=github_headers(token))
    return status == 200


def write_github_outputs(outputs: dict[str, str]) -> None:
    """Write key=value pairs to GITHUB_OUTPUT when running inside GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            fh.write(f"{key}={value}\n")


def append_step_summary(lines: list[str]) -> None:
    """Append markdown lines to GITHUB_STEP_SUMMARY when running inside Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def decide_build(
    *,
    event: str,
    target_ref: str,
    upstream_branch: str,
    publish_release: bool,
    force_build: bool,
    repo: str,
    upstream: str,
    token: str,
) -> tuple[dict[str, str], list[str]]:
    """Determine build outputs and summary lines for the triggering GitHub event."""
    if event in ("push", "pull_request"):
        effective_ref = upstream_branch
    elif event == "schedule":
        effective_ref = upstream_branch
    elif event == "workflow_dispatch":
        effective_ref = target_ref or upstream_branch
    else:
        effective_ref = target_ref or upstream_branch

    commit_sha, commit_date, commit_subject = resolve_upstream_commit(
        upstream, effective_ref, token
    )
    short_sha = commit_sha[:7]

    compatible, compat_reason = is_ref_compatible(upstream, commit_sha)
    if not compatible:
        raise RuntimeError(
            f"Upstream ref '{effective_ref}' ({short_sha}) in {upstream} is incompatible ({compat_reason})."
        )

    fw_version = get_makefile_version(upstream, commit_sha)
    if event == "workflow_dispatch" and is_upstream_tag(upstream, effective_ref, token):
        release_tag = effective_ref
    else:
        release_tag = f"v{fw_version}-{short_sha}"

    if event in ("push", "pull_request"):
        outputs = {
            "should_build": "true",
            "target_ref": effective_ref,
            "commit_sha": commit_sha,
            "short_sha": short_sha,
            "fw_version": fw_version,
            "release_tag": release_tag,
            "publish_release": "false",
            "force_build": "false",
        }
        return outputs, [
            "### CI Build Verification",
            f"- Trigger event: `{event}`",
            f"- Upstream ref: `{upstream}@{effective_ref}` (`{short_sha}`, {commit_date or 'unknown date'})",
            f"- Head commit: {commit_subject}",
            "- Release publication: disabled (verification & artifact build only)",
        ]

    if event == "schedule":
        if not force_build and release_exists(repo, release_tag, token):
            outputs = {
                "should_build": "false",
                "target_ref": effective_ref,
                "commit_sha": commit_sha,
                "short_sha": short_sha,
                "fw_version": fw_version,
                "release_tag": release_tag,
                "publish_release": "false",
                "force_build": "false",
            }
            return outputs, [
                "### Weekly Upstream Main Check",
                f"- Upstream `{upstream}@{effective_ref}` is at `{short_sha}` (`{commit_subject}`).",
                f"- Release `{release_tag}` is already published in `{repo}`; no new upstream commits since last release. Skipping redundant build.",
            ]

        outputs = {
            "should_build": "true",
            "target_ref": effective_ref,
            "commit_sha": commit_sha,
            "short_sha": short_sha,
            "fw_version": fw_version,
            "release_tag": release_tag,
            "publish_release": "true",
            "force_build": "true" if force_build else "false",
        }
        return outputs, [
            "### Weekly Upstream Main Release Build",
            f"- Upstream ref: `{upstream}@{effective_ref}` (`{short_sha}`, {commit_date or 'unknown date'})",
            f"- Head commit: {commit_subject}",
            f"- Target release tag: `{release_tag}` (Makefile `v{fw_version}`)",
            "- Proceeding to compile, verify checksums, generate release notes, and publish release.",
        ]

    # workflow_dispatch (or other manual/custom triggers)
    if publish_release and not force_build and release_exists(repo, release_tag, token):
        outputs = {
            "should_build": "false",
            "target_ref": effective_ref,
            "commit_sha": commit_sha,
            "short_sha": short_sha,
            "fw_version": fw_version,
            "release_tag": release_tag,
            "publish_release": "false",
            "force_build": "false",
        }
        return outputs, [
            "### Manual Dispatch Skipped",
            f"- Release `{release_tag}` already exists in `{repo}` (upstream `{upstream}@{effective_ref}` -> `{short_sha}`).",
            "- Re-run with `force_build=true` to rebuild and overwrite release assets.",
        ]

    outputs = {
        "should_build": "true",
        "target_ref": effective_ref,
        "commit_sha": commit_sha,
        "short_sha": short_sha,
        "fw_version": fw_version,
        "release_tag": release_tag,
        "publish_release": "true" if publish_release else "false",
        "force_build": "true" if force_build else "false",
    }
    return outputs, [
        "### Manual Firmware Build",
        f"- Upstream ref: `{upstream}@{effective_ref}` (`{short_sha}`, {commit_date or 'unknown date'})",
        f"- Head commit: {commit_subject}",
        f"- Target release tag: `{release_tag}`",
        f"- Publish GitHub Release: `{outputs['publish_release']}`",
        f"- Force rebuild: `{outputs['force_build']}`",
    ]


def parse_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--target-ref", default="")
    parser.add_argument("--upstream-branch", default="main")
    parser.add_argument("--publish-release", default="false")
    parser.add_argument("--force-build", default="false")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--upstream", default="logicog/RTLPlayground")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    outputs, summary = decide_build(
        event=args.event.strip(),
        target_ref=args.target_ref.strip(),
        upstream_branch=args.upstream_branch.strip() or "main",
        publish_release=parse_bool(args.publish_release),
        force_build=parse_bool(args.force_build),
        repo=args.repo.strip(),
        upstream=args.upstream.strip(),
        token=token,
    )

    for key, value in outputs.items():
        print(f"{key}={value}")
    print("\n".join(summary))

    write_github_outputs(outputs)
    append_step_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
