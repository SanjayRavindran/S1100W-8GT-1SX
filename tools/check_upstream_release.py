#!/usr/bin/env python3
"""Check upstream logicog/RTLPlayground release availability and compatibility."""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


USER_AGENT = "S1100W-8GT-1SX-CI/1.0"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
REQUIRED_UPSTREAM_FILES = (
    "machine/HASIVO_S1100WP_8GT_1SX_SE.h",
    "installer/Makefile",
)


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


def is_ref_compatible(upstream: str, ref: str) -> tuple[bool, str]:
    """Verify the upstream ref includes S1100W machine support and OEM installer."""
    encoded_ref = urllib.parse.quote(ref, safe="")
    missing: list[str] = []
    for rel_path in REQUIRED_UPSTREAM_FILES:
        raw_url = f"https://raw.githubusercontent.com/{upstream}/{encoded_ref}/{rel_path}"
        status, _ = fetch_url(raw_url, method="HEAD")
        if status != 200:
            missing.append(rel_path)

    if missing:
        return False, f"missing {', '.join(missing)}"
    return True, "compatible"


def get_upstream_releases(upstream: str) -> list[tuple[str, str, str]]:
    """Return upstream (tag, title, updated) entries from releases.atom, newest first."""
    atom_url = f"https://github.com/{upstream}/releases.atom"
    status, payload = fetch_url(atom_url)
    if status != 200:
        raise RuntimeError(f"Unexpected HTTP {status} fetching {atom_url}")

    root = ET.fromstring(payload.decode("utf-8"))
    releases: list[tuple[str, str, str]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = entry.findtext("atom:title", "", ATOM_NS).strip()
        updated = entry.findtext("atom:updated", "", ATOM_NS).strip()
        link = entry.find("atom:link", ATOM_NS)
        href = link.attrib.get("href", "") if link is not None else ""
        if "/releases/tag/" in href:
            tag = urllib.parse.unquote(href.rstrip("/").split("/")[-1])
            releases.append((tag, title, updated))
    return releases


def release_exists(repo: str, tag: str, token: str) -> bool:
    """Return True if a GitHub release for `tag` already exists in `repo`."""
    encoded_tag = urllib.parse.quote(tag, safe="")
    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{encoded_tag}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, _ = fetch_url(api_url, headers=headers)
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
    publish_release: bool,
    force_build: bool,
    repo: str,
    upstream: str,
    token: str,
) -> tuple[dict[str, str], list[str]]:
    """Determine build outputs and summary lines for the triggering GitHub event."""
    if event in ("push", "pull_request"):
        outputs = {
            "should_build": "true",
            "target_ref": "main",
            "publish_release": "false",
            "release_tag": "",
            "force_build": "false",
        }
        return outputs, [
            "### CI Build Verification",
            f"- Trigger event: `{event}`",
            f"- Upstream ref: `logicog/RTLPlayground@main`",
            "- Release publication: disabled (verification & artifact build only)",
        ]

    if event == "workflow_dispatch" and target_ref and target_ref != "latest-release":
        compatible, compat_reason = is_ref_compatible(upstream, target_ref)
        if not compatible:
            raise RuntimeError(
                f"Upstream ref '{target_ref}' in {upstream} is incompatible ({compat_reason})."
            )

        is_branch = target_ref in ("main", "master")
        release_tag = "" if is_branch else target_ref
        if publish_release and release_tag and not force_build and release_exists(repo, release_tag, token):
            outputs = {
                "should_build": "false",
                "target_ref": target_ref,
                "publish_release": "false",
                "release_tag": release_tag,
                "force_build": "false",
            }
            return outputs, [
                "### Manual Dispatch Skipped",
                f"- Release `{release_tag}` already exists in `{repo}`.",
                "- Re-run with `force_build=true` to rebuild and overwrite release assets.",
            ]

        outputs = {
            "should_build": "true",
            "target_ref": target_ref,
            "publish_release": "true" if publish_release else "false",
            "release_tag": release_tag,
            "force_build": "true" if force_build else "false",
        }
        return outputs, [
            "### Manual Firmware Build",
            f"- Upstream ref: `{upstream}@{target_ref}`",
            f"- Publish GitHub Release: `{outputs['publish_release']}`",
            f"- Force rebuild: `{outputs['force_build']}`",
        ]

    # Automatic discovery mode (schedule or workflow_dispatch with blank/latest-release)
    releases = get_upstream_releases(upstream)
    checked_notes: list[str] = []
    chosen_tag: str | None = None
    chosen_title = ""

    for tag, title, updated in releases:
        compatible, compat_reason = is_ref_compatible(upstream, tag)
        checked_notes.append(f"`{tag}` ({updated or 'unknown date'}): {compat_reason}")
        if compatible:
            chosen_tag = tag
            chosen_title = title
            break

    if not chosen_tag:
        outputs = {
            "should_build": "false",
            "target_ref": "",
            "publish_release": "false",
            "release_tag": "",
            "force_build": "false",
        }
        notes_md = "\n".join(f"  - {note}" for note in checked_notes) or "  - None found"
        return outputs, [
            "### Upstream Release Check",
            f"- No compatible release found in `{upstream}` yet. Skipping scheduled build cleanly.",
            "- Releases evaluated:\n" + notes_md,
        ]

    if not force_build and release_exists(repo, chosen_tag, token):
        outputs = {
            "should_build": "false",
            "target_ref": chosen_tag,
            "publish_release": "false",
            "release_tag": chosen_tag,
            "force_build": "false",
        }
        return outputs, [
            "### Upstream Release Check",
            f"- Latest compatible upstream release `{chosen_tag}` (`{chosen_title}`) is already published in `{repo}`.",
            "- Skipping redundant build.",
        ]

    outputs = {
        "should_build": "true",
        "target_ref": chosen_tag,
        "publish_release": "true",
        "release_tag": chosen_tag,
        "force_build": "true" if force_build else "false",
    }
    return outputs, [
        "### New Compatible Upstream Release Detected",
        f"- Upstream repository: `{upstream}`",
        f"- Release tag: `{chosen_tag}` (`{chosen_title}`)",
        "- Proceeding to compile, verify checksums, and publish release assets.",
    ]


def parse_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--target-ref", default="")
    parser.add_argument("--publish-release", default="false")
    parser.add_argument("--force-build", default="false")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--upstream", default="logicog/RTLPlayground")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    outputs, summary = decide_build(
        event=args.event.strip(),
        target_ref=args.target_ref.strip(),
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
