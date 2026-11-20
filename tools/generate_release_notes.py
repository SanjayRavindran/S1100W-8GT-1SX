#!/usr/bin/env python3
"""Generate structured markdown release notes for S1100W-8GT-1SX firmware releases."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


USER_AGENT = "S1100W-8GT-1SX-ReleaseNotes/1.0"
MAX_RENDERED_CHANGES = 60
FALLBACK_COMMIT_COUNT = 15

BODY_COMMIT_RE = re.compile(
    r"commit\s+(?:\[)?`?([0-9a-f]{7,40})`?(?:\])?", re.IGNORECASE
)
TAG_COMMIT_RE = re.compile(r"-([0-9a-f]{7,40})$", re.IGNORECASE)
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+) from \S+")
SQUASH_PR_RE = re.compile(r"^(.*?)\s*\(#(\d+)\)$")

S1100W_RE = re.compile(r"(?<![a-z0-9])(s1100wp?|hasivo_s1100\w*)(?![a-z0-9])", re.IGNORECASE)
OTHER_BOARD_RE = re.compile(
    r"^(machine/|board:|pcb[:/])|"
    r"(?<![a-z0-9])(keeplink|sodola|hg0402xg|s604gtp|pb-2131|goalake|steamemo|"
    r"2g5f_20g|swtg\d+|kp-9000|2m-pcb\d+|sgt0108s)(?![a-z0-9-])|"
    r"\badd\s+.+\s+support\b",
    re.IGNORECASE,
)
DOCS_TOOLS_RE = re.compile(
    r"^(doc|docs|test|tests|ci|httpd_sim)\b|"
    r"(document fix|doc/machine|check doc|httpd_sim|internal ram)",
    re.IGNORECASE,
)

CATEGORY_ORDER = (
    "S1100W-Specific Changes",
    "Firmware, Networking & Web UI",
    "Other Board & Hardware Support",
    "Documentation, Tests & Build Tooling",
)


@dataclass(frozen=True)
class ChangeEntry:
    """Single top-level upstream change (PR merge or direct commit)."""

    full_sha: str
    short_sha: str
    date: str
    title: str
    pr_number: str | None


def github_headers(token: str = "") -> dict[str, str]:
    """Build standard GitHub REST API headers."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json_with_retry(url: str, token: str = "", retries: int = 3) -> object:
    """Fetch JSON from GitHub REST API with retries for transient failures."""
    headers = {"User-Agent": USER_AGENT, **github_headers(token)}
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            last_err = err
            if err.code not in (429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError as err:
            last_err = err
        if attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Failed GET {url}: {last_err}")


def find_previous_release(
    repo: str, current_tag: str, token: str
) -> tuple[str | None, str | None]:
    """Return (prev_tag, prev_upstream_sha) for the most recent non-draft prior release."""
    if not repo:
        return None, None

    api_url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    try:
        releases = fetch_json_with_retry(api_url, token=token)
    except Exception as err:  # noqa: BLE001 - never fail release publishing on notes API lookup
        print(
            f"Warning: could not fetch prior releases from {repo}: {err}",
            file=sys.stderr,
        )
        return None, None

    if not isinstance(releases, list):
        return None, None

    for rel in releases:
        if rel.get("draft"):
            continue
        tag = str(rel.get("tag_name", "")).strip()
        if not tag or tag == current_tag:
            continue

        body = str(rel.get("body") or "")
        match = BODY_COMMIT_RE.search(body)
        if match:
            return tag, match.group(1)

        tag_match = TAG_COMMIT_RE.search(tag)
        return tag, (tag_match.group(1) if tag_match else None)

    return None, None


def run_git(upstream_dir: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command inside `upstream_dir`."""
    return subprocess.run(
        ["git", "-C", upstream_dir, *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def parse_git_log_entries(raw_output: str) -> list[ChangeEntry]:
    """Parse `%H%x1f%h%x1f%cs%x1f%s%x1f%b%x1e` records into ChangeEntry objects."""
    entries: list[ChangeEntry] = []
    for record in raw_output.split("\x1e"):
        record = record.strip("\r\n")
        if not record:
            continue
        parts = record.split("\x1f", 4)
        if len(parts) < 4:
            continue
        full_sha = parts[0].strip()
        short_sha = parts[1].strip()
        date_str = parts[2].strip()
        subject = parts[3].strip()
        body = parts[4] if len(parts) > 4 else ""

        pr_number: str | None = None
        title = subject

        merge_match = MERGE_PR_RE.match(subject)
        if merge_match:
            pr_number = merge_match.group(1)
            for line in body.splitlines():
                stripped = line.strip()
                if stripped:
                    title = stripped
                    break
        else:
            squash_match = SQUASH_PR_RE.match(subject)
            if squash_match:
                title = squash_match.group(1).strip()
                pr_number = squash_match.group(2)

        entries.append(
            ChangeEntry(
                full_sha=full_sha,
                short_sha=short_sha,
                date=date_str,
                title=title,
                pr_number=pr_number,
            )
        )
    return entries


def collect_upstream_changes(
    upstream_dir: str, prev_sha: str | None, curr_sha: str
) -> tuple[str, str, int | None, str | None, list[ChangeEntry], bool]:
    """Return (curr_full, curr_date, total_commits, prev_full, entries, is_ancestor_diff)."""
    curr_full = run_git(upstream_dir, "rev-parse", f"{curr_sha}^{{commit}}").stdout.strip()
    curr_date = run_git(upstream_dir, "show", "-s", "--format=%cI", curr_full).stdout.strip()

    prev_full: str | None = None
    is_ancestor = False
    if prev_sha:
        verify = run_git(upstream_dir, "rev-parse", "--verify", f"{prev_sha}^{{commit}}", check=False)
        if verify.returncode == 0:
            prev_full = verify.stdout.strip()
            anc = run_git(
                upstream_dir, "merge-base", "--is-ancestor", prev_full, curr_full, check=False
            )
            is_ancestor = anc.returncode == 0

    log_fmt = "--format=%H%x1f%h%x1f%cs%x1f%s%x1f%b%x1e"

    if prev_full and is_ancestor:
        if prev_full == curr_full:
            return curr_full, curr_date, 0, prev_full, [], True

        rev_range = f"{prev_full}..{curr_full}"
        total_commits = int(
            run_git(upstream_dir, "rev-list", "--count", rev_range).stdout.strip()
        )
        raw_log = run_git(
            upstream_dir, "log", "--first-parent", log_fmt, rev_range
        ).stdout
        return (
            curr_full,
            curr_date,
            total_commits,
            prev_full,
            parse_git_log_entries(raw_log),
            True,
        )

    raw_log = run_git(
        upstream_dir,
        "log",
        "--first-parent",
        f"-n{FALLBACK_COMMIT_COUNT}",
        log_fmt,
        curr_full,
    ).stdout
    return (
        curr_full,
        curr_date,
        None,
        prev_full or prev_sha,
        parse_git_log_entries(raw_log),
        False,
    )


def categorize_change(title: str) -> str:
    """Assign an upstream change title to a release-notes category."""
    if S1100W_RE.search(title):
        return "S1100W-Specific Changes"
    if OTHER_BOARD_RE.search(title):
        return "Other Board & Hardware Support"
    if DOCS_TOOLS_RE.search(title):
        return "Documentation, Tests & Build Tooling"
    return "Firmware, Networking & Web UI"


def escape_markdown_text(text: str) -> str:
    """Escape characters that could break single-line markdown list items."""
    return " ".join(text.split()).replace("[", "\\[").replace("]", "\\]")


def format_change_bullet(entry: ChangeEntry, upstream: str) -> str:
    """Format a ChangeEntry as a markdown bullet with PR and commit links."""
    title = escape_markdown_text(entry.title)
    commit_link = f"[`{entry.short_sha}`](https://github.com/{upstream}/commit/{entry.full_sha})"
    if entry.pr_number:
        pr_link = f"[#{entry.pr_number}](https://github.com/{upstream}/pull/{entry.pr_number})"
        return f"- {title} ({pr_link} in {commit_link}, {entry.date})"
    return f"- {title} ({commit_link}, {entry.date})"


def read_text_if_exists(path: str) -> str:
    """Read UTF-8 text from `path` if set and present on disk."""
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().strip()


def build_release_notes(args: argparse.Namespace) -> str:
    """Assemble full markdown release notes for the built firmware artifacts."""
    token = os.environ.get("GITHUB_TOKEN", "")
    prev_tag, prev_sha_hint = find_previous_release(args.repo, args.release_tag, token)

    curr_full, curr_date, total_commits, prev_full, entries, is_ancestor_diff = (
        collect_upstream_changes(args.upstream_dir, prev_sha_hint, args.commit_sha)
    )
    curr_short = curr_full[:7]
    prev_short = prev_full[:7] if prev_full else None

    build_date_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    trigger_label = (
        "Automated weekly build"
        if args.trigger_event == "schedule"
        else ("Manual build" if args.trigger_event == "workflow_dispatch" else "CI verification build")
    )

    oem_name = os.path.basename(args.oem_bin)
    full_name = os.path.basename(args.full_bin)
    oem_size = os.path.getsize(args.oem_bin)
    full_size = os.path.getsize(args.full_bin)
    sha256sums = read_text_if_exists(args.sha256sums)
    mem_layout = read_text_if_exists(args.mem_file)
    sdcc_ver = args.sdcc_version.strip() or "Patched SDCC 4.6.0 (MCS-51 Large Banked)"

    upstream_url = f"https://github.com/{args.upstream}"
    ref_url = f"{upstream_url}/tree/{args.target_ref}"
    commit_url = f"{upstream_url}/commit/{curr_full}"

    lines: list[str] = [
        f"## Hasivo S1100W-8GT-1SX Open-Source Firmware (`{args.release_tag}`)",
        "",
        (
            f"{trigger_label} from [{args.upstream}]({upstream_url}) "
            f"([`{args.target_ref}`]({ref_url}) at commit [`{curr_short}`]({commit_url}))."
        ),
        "",
        "### Build Metadata",
        "",
        "| Field | Value |",
        "| :--- | :--- |",
        f"| **Release Tag** | `{args.release_tag}` |",
        (
            f"| **Upstream Source** | [{args.upstream}@{args.target_ref}]({ref_url}) "
            f"(commit [`{curr_short}`]({commit_url}), Makefile `v{args.fw_version}`) |"
        ),
        f"| **Upstream Commit Date** | `{curr_date}` |",
    ]

    if prev_tag and args.repo:
        prev_rel_url = f"https://github.com/{args.repo}/releases/tag/{prev_tag}"
        if prev_short and prev_full:
            prev_commit_url = f"{upstream_url}/commit/{prev_full}"
            prev_cell = f"[`{prev_tag}`]({prev_rel_url}) ([`{prev_short}`]({prev_commit_url}))"
        elif prev_short:
            prev_cell = f"[`{prev_tag}`]({prev_rel_url}) (`{prev_short}`)"
        else:
            prev_cell = f"[`{prev_tag}`]({prev_rel_url})"
        lines.append(f"| **Previous Release** | {prev_cell} |")

    lines.extend(
        [
            f"| **Build Date (UTC)** | `{build_date_utc}` |",
            "| **Target Hardware Profile** | Hasivo S1100W-8GT-1SX (`HASIVO_S1100WP_8GT_1SX_SE`, 8x 2.5GbE RJ45 + 1x 10GbE SFP+, non-PoE) |",
            f"| **Compiler** | `{sdcc_ver}` |",
            "| **Network Defaults** | DHCP Client w/ Option 12 (`hostname S1100W`), fallback `192.168.1.28/24`, gw `192.168.1.1`, password `admin` |",
            "",
        ]
    )

    compare_url = (
        f"{upstream_url}/compare/{prev_short}...{curr_short}" if prev_short else None
    )

    if prev_tag:
        lines.append(f"### What's Changed Since `{prev_tag}`")
    else:
        lines.append("### What's Changed")
    lines.append("")

    if is_ancestor_diff and total_commits == 0:
        lines.append(
            f"Rebuild of the same upstream commit [`{curr_short}`]({commit_url}) "
            f"(no new upstream commits since `{prev_tag}`)."
        )
        lines.append("")
    elif is_ancestor_diff and total_commits is not None:
        summary_parts = [
            f"Includes **{total_commits} commit{'s' if total_commits != 1 else ''}**",
            f"across **{len(entries)} merged PR / top-level change{'s' if len(entries) != 1 else ''}**",
            f"on `{args.upstream}`",
        ]
        if prev_tag and args.repo:
            summary_parts.append(
                f"since [`{prev_tag}`](https://github.com/{args.repo}/releases/tag/{prev_tag})"
            )
        if compare_url:
            summary_parts.append(f"([full diff]({compare_url}))")
        lines.append(" ".join(summary_parts) + ".")
        lines.append("")
    else:
        intro = f"Recent upstream changes leading up to [`{curr_short}`]({commit_url})"
        if compare_url:
            intro += f" ([compare `{prev_short}...{curr_short}`]({compare_url}))"
        lines.append(intro + ":")
        lines.append("")

    if entries:
        buckets: dict[str, list[ChangeEntry]] = {cat: [] for cat in CATEGORY_ORDER}
        for entry in entries:
            buckets[categorize_change(entry.title)].append(entry)

        rendered = 0
        truncated = False
        for category in CATEGORY_ORDER:
            cat_entries = buckets[category]
            if not cat_entries:
                continue
            remaining = MAX_RENDERED_CHANGES - rendered
            if remaining <= 0:
                truncated = True
                break
            lines.append(f"#### {category}")
            lines.append("")
            slice_entries = cat_entries[:remaining]
            for entry in slice_entries:
                lines.append(format_change_bullet(entry, args.upstream))
            rendered += len(slice_entries)
            if len(slice_entries) < len(cat_entries):
                truncated = True
            lines.append("")

        if truncated or rendered < len(entries):
            overflow_msg = (
                f"*Showing {rendered} of {len(entries)} top-level changes.*"
            )
            if compare_url:
                overflow_msg += f" View the complete commit log in the [full diff]({compare_url})."
            lines.append(overflow_msg)
            lines.append("")

    lines.extend(
        [
            "### Artifacts & Verification",
            "",
            "| Artifact | Size | Purpose | Verification |",
            "| :--- | ---: | :--- | :--- |",
            f"| `{oem_name}` | {oem_size:,} B | Web UI (`/fwug.cgi`) & failsafe bootloader upgrade | `tools/calcsum.py` PASS |",
            f"| `{full_name}` | {full_size:,} B | Raw 512 KB SPI NOR flash image (external SOIC-8 flasher) | `crc_calculator -v` PASS |",
            "",
        ]
    )

    if sha256sums:
        lines.extend(
            [
                "#### SHA-256 Checksums (`SHA256SUMS.txt`)",
                "",
                "```text",
                sha256sums,
                "```",
                "",
            ]
        )

    if mem_layout:
        lines.extend(
            [
                "<details>",
                "<summary>8051 Memory Layout (<code>rtlplayground.mem</code>)</summary>",
                "",
                "```text",
                mem_layout,
                "```",
                "",
                "</details>",
                "",
            ]
        )

    lines.extend(
        [
            "### Flashing & Recovery Notes",
            "",
            f"* **Web UI Upgrade:** Log in to the switch Web Console, navigate to **Tools -> Firmware Upgrade** (`/fwug.cgi`), and upload `{oem_name}`.",
            "* **Failsafe Bootloader Recovery:** Power-cycle while holding `ESC` on UART (`57600` baud, 8-N-1), connect to Port 1 (`192.168.1.2/24`), and upload:",
            "  ```bash",
            f'  curl -F "file=@{oem_name}" "http://192.168.1.1/httpupg.cgi?cmd=fw_upgrade"',
            "  ```",
            "* **Serial CLI:** Runs at **`115200` baud** (8-N-1) once RTLPlayground boots (bootloader remains `57600` baud). Persist CLI changes with `flash save`.",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--upstream", default="logicog/RTLPlayground")
    parser.add_argument("--upstream-dir", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--fw-version", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--trigger-event", default="")
    parser.add_argument("--oem-bin", required=True)
    parser.add_argument("--full-bin", required=True)
    parser.add_argument("--sha256sums", required=True)
    parser.add_argument("--mem-file", default="")
    parser.add_argument("--sdcc-version", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    notes = build_release_notes(args)
    output_dir = os.path.dirname(os.path.abspath(args.output))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(notes)

    print(f"Generated release notes at {args.output} ({len(notes)} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
