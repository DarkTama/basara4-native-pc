#!/usr/bin/env python3
"""Generates release notes with highlights, grouped commit list, and download footer."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import List, Optional

SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
CONVENTIONAL_RE = re.compile(r"^([a-zA-Z]+)(?:\([^)]+\))?!?:")


def run_git(args: List[str], cwd: Path | str = ".") -> str:
    """Executes a git command and returns standard output."""
    res = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return res.stdout


def detect_repo(repo_dir: Path | str = ".") -> str:
    """Infers the GitHub 'owner/repo' identifier from env or git remote."""
    env_repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if env_repo:
        return env_repo.strip()
    try:
        url = run_git(["config", "--get", "remote.origin.url"], cwd=repo_dir).strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "DarkTama/basara4-native-pc"


def parse_semver(tag: str) -> tuple[int, ...]:
    """Parses a semver tag like 'v1.2.3' into a comparable integer tuple (1, 2, 3)."""
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def get_previous_tag(
    target_ref: str,
    current_tag: str,
    repo_dir: Path | str = ".",
) -> Optional[str]:
    """Finds the latest v* semver tag reachable from target_ref, excluding current_tag."""
    norm_tag = current_tag.split("/")[-1]
    try:
        out = run_git(["tag", "--merged", target_ref], cwd=repo_dir)
    except subprocess.CalledProcessError:
        return None

    candidates: List[tuple[tuple[int, ...], str]] = []
    for line in out.splitlines():
        t = line.strip().split("/")[-1]
        if not t or t == norm_tag:
            continue
        if SEMVER_TAG_RE.match(t):
            try:
                candidates.append((parse_semver(t), t))
            except ValueError:
                continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def get_commits(
    target_ref: str,
    prev_tag: Optional[str],
    repo_dir: Path | str = ".",
) -> List[dict]:
    """Retrieves non-merge commits between prev_tag and target_ref."""
    if prev_tag:
        rev_range = f"{prev_tag}..{target_ref}"
        args = ["log", "--no-merges", "--pretty=format:%H%x09%h%x09%s", rev_range]
    else:
        args = ["log", "--no-merges", "--pretty=format:%H%x09%h%x09%s", target_ref]

    try:
        out = run_git(args, cwd=repo_dir)
    except subprocess.CalledProcessError:
        return []

    commits: List[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "short_sha": parts[1], "subject": parts[2]})
    return commits


def classify_commit(subject: str) -> str:
    """Classifies commit subject into Features, Fixes, Documentation, or Other."""
    m = CONVENTIONAL_RE.match(subject.strip())
    if m:
        c_type = m.group(1).lower()
        if c_type == "feat":
            return "Features"
        if c_type == "fix":
            return "Fixes"
        if c_type == "docs":
            return "Documentation"
    return "Other"


def get_highlights(tag: str, repo_dir: Path | str = ".") -> str:
    """Reads docs/release-notes/<tag>.md if it exists."""
    norm_tag = tag.split("/")[-1]
    notes_file = Path(repo_dir) / "docs" / "release-notes" / f"{norm_tag}.md"
    if notes_file.is_file():
        try:
            return notes_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def generate_release_notes(
    tag: str,
    repo: str,
    head: Optional[str] = None,
    repo_dir: Path | str = ".",
) -> str:
    """Constructs the complete release notes Markdown document."""
    norm_tag = tag.split("/")[-1]

    # Resolve target_ref
    target_ref = head
    if not target_ref:
        try:
            run_git(["rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"], cwd=repo_dir)
            target_ref = tag
        except subprocess.CalledProcessError:
            target_ref = "HEAD"

    prev_tag = get_previous_tag(target_ref, norm_tag, repo_dir=repo_dir)
    commits = get_commits(target_ref, prev_tag, repo_dir=repo_dir)

    features: List[str] = []
    fixes: List[str] = []
    docs: List[str] = []
    other: List[str] = []

    for c in commits:
        line = f"- {c['subject']} ([{c['short_sha']}](https://github.com/{repo}/commit/{c['sha']}))"
        cat = classify_commit(c["subject"])
        if cat == "Features":
            features.append(line)
        elif cat == "Fixes":
            fixes.append(line)
        elif cat == "Documentation":
            docs.append(line)
        else:
            other.append(line)

    sections: List[str] = []

    highlights = get_highlights(norm_tag, repo_dir=repo_dir)
    if highlights:
        sections.append(highlights)

    if features:
        sections.append("### Features\n\n" + "\n".join(features))
    if fixes:
        sections.append("### Fixes\n\n" + "\n".join(fixes))
    if docs:
        sections.append("### Documentation\n\n" + "\n".join(docs))
    if other:
        other_block = f"<details><summary>Other changes ({len(other)})</summary>\n\n" + "\n".join(other) + "\n\n</details>"
        sections.append(other_block)

    footer_lines = [
        "Download `basara4-windows.zip`, extract it, and run `Sengoku Basara 4.exe`. See README.md for requirements and instructions."
    ]
    if prev_tag:
        footer_lines.append(f"**Full changelog**: https://github.com/{repo}/compare/{prev_tag}...{norm_tag}")
    sections.append("\n\n".join(footer_lines))

    return "\n\n".join(sections) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate release notes with categorized commit list.")
    parser.add_argument("--tag", required=True, help="Release tag name (e.g. v1.2.0)")
    parser.add_argument("--repo", default=None, help="GitHub repository (e.g. DarkTama/basara4-native-pc)")
    parser.add_argument("--head", default=None, help="Commit/ref to treat as tag commit (default: TAG or HEAD)")
    parser.add_argument("--repo-dir", default=".", help="Path to git repository root")
    parser.add_argument("--out", default=None, help="Output file path (default: stdout)")
    args = parser.parse_args()

    repo = args.repo or detect_repo(args.repo_dir)
    notes = generate_release_notes(
        tag=args.tag,
        repo=repo,
        head=args.head,
        repo_dir=args.repo_dir,
    )

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if args.out and args.out != "-":
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(notes, encoding="utf-8")
    else:
        try:
            sys.stdout.write(notes)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(notes.encode("utf-8"))


if __name__ == "__main__":
    main()
