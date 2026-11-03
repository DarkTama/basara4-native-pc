"""Unit tests for packaging/release_notes.py."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "packaging") not in sys.path:
    sys.path.insert(0, str(REPO / "packaging"))

import release_notes as rn


def init_git_repo(repo_dir: Path) -> None:
    """Initializes a minimal git repository with author settings."""
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "ReleaseBot"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "bot@example.com"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )


def make_commit(repo_dir: Path, filename: str, content: str, message: str) -> str:
    """Creates a commit and returns its full SHA."""
    filepath = repo_dir / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


def make_tag(repo_dir: Path, tag_name: str, annotated: bool = False) -> None:
    """Creates a tag at current HEAD."""
    cmd = ["git", "tag"]
    if annotated:
        cmd.extend(["-a", tag_name, "-m", f"Release {tag_name}"])
    else:
        cmd.append(tag_name)
    subprocess.run(cmd, cwd=str(repo_dir), check=True, capture_output=True)


class TestReleaseNotes(unittest.TestCase):
    def test_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            init_git_repo(repo_dir)

            make_commit(repo_dir, "base.txt", "base", "chore: initial commit")
            make_tag(repo_dir, "v1.0.0", annotated=True)

            make_commit(repo_dir, "f1.txt", "1", "feat(launcher): add dark theme")
            make_commit(repo_dir, "f2.txt", "2", "feat: tall canvas support")
            make_commit(repo_dir, "fix1.txt", "3", "fix(presenter): fix blit stretch")
            make_commit(repo_dir, "doc1.txt", "4", "docs(readme): add dual-monitor notes")
            make_commit(repo_dir, "c1.txt", "5", "chore: bump dependency")
            make_commit(repo_dir, "t1.txt", "6", "tests: add test for reporting")
            make_commit(repo_dir, "p1.txt", "7", "packaging: package extra dll")

            notes = rn.generate_release_notes(
                tag="v1.1.0",
                repo="DarkTama/basara4-native-pc",
                head="HEAD",
                repo_dir=repo_dir,
            )

            # Features section
            self.assertIn("### Features", notes)
            self.assertIn("- feat(launcher): add dark theme", notes)
            self.assertIn("- feat: tall canvas support", notes)

            # Fixes section
            self.assertIn("### Fixes", notes)
            self.assertIn("- fix(presenter): fix blit stretch", notes)

            # Documentation section
            self.assertIn("### Documentation", notes)
            self.assertIn("- docs(readme): add dual-monitor notes", notes)

            # Other changes collapsible section
            self.assertIn("<details><summary>Other changes (3)</summary>", notes)
            self.assertIn("- chore: bump dependency", notes)
            self.assertIn("- tests: add test for reporting", notes)
            self.assertIn("- packaging: package extra dll", notes)
            self.assertIn("</details>", notes)

            # Footer
            self.assertIn("Download `basara4-windows.zip`", notes)
            self.assertIn(
                "**Full changelog**: https://github.com/DarkTama/basara4-native-pc/compare/v1.0.0...v1.1.0",
                notes,
            )

    def test_highlights_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            init_git_repo(repo_dir)

            make_commit(repo_dir, "a.txt", "a", "chore: initial")
            make_tag(repo_dir, "v1.0.0")
            make_commit(repo_dir, "b.txt", "b", "feat: new feature")

            # Create hand-written highlights
            hl_file = repo_dir / "docs" / "release-notes" / "v1.1.0.md"
            hl_file.parent.mkdir(parents=True, exist_ok=True)
            highlights_content = "## Highlights for v1.1.0\n\n- Major speedup!\n- Better graphics."
            hl_file.write_text(highlights_content, encoding="utf-8")

            notes = rn.generate_release_notes(
                tag="v1.1.0",
                repo="DarkTama/basara4-native-pc",
                head="HEAD",
                repo_dir=repo_dir,
            )

            self.assertTrue(notes.startswith(highlights_content))
            self.assertIn("### Features", notes)
            self.assertIn("Download `basara4-windows.zip`", notes)

    def test_prev_tag_selection_skipping_non_semver_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            init_git_repo(repo_dir)

            make_commit(repo_dir, "1.txt", "1", "chore: c1")
            make_tag(repo_dir, "v1.0.0", annotated=True)

            make_commit(repo_dir, "2.txt", "2", "chore: c2")
            make_tag(repo_dir, "v1.0.1-rc1")  # non-semver pre-release
            make_tag(repo_dir, "windows-v2.0-test")  # non-semver

            make_commit(repo_dir, "3.txt", "3", "chore: c3")
            make_tag(repo_dir, "v1.1.0", annotated=True)

            make_commit(repo_dir, "4.txt", "4", "chore: c4")
            make_tag(repo_dir, "v1.2.0-beta")  # non-semver

            make_commit(repo_dir, "5.txt", "5", "feat: final feature")
            make_tag(repo_dir, "v1.2.0")

            prev = rn.get_previous_tag("v1.2.0", "v1.2.0", repo_dir=repo_dir)
            self.assertEqual(prev, "v1.1.0")

            notes = rn.generate_release_notes(
                tag="v1.2.0",
                repo="DarkTama/basara4-native-pc",
                head="v1.2.0",
                repo_dir=repo_dir,
            )
            self.assertIn(
                "**Full changelog**: https://github.com/DarkTama/basara4-native-pc/compare/v1.1.0...v1.2.0",
                notes,
            )

    def test_no_prev_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            init_git_repo(repo_dir)

            make_commit(repo_dir, "1.txt", "1", "feat: first commit")
            make_tag(repo_dir, "prerelease-tag")  # non-semver tag
            make_commit(repo_dir, "2.txt", "2", "fix: second commit")

            prev = rn.get_previous_tag("HEAD", "v1.0.0", repo_dir=repo_dir)
            self.assertIsNone(prev)

            notes = rn.generate_release_notes(
                tag="v1.0.0",
                repo="DarkTama/basara4-native-pc",
                head="HEAD",
                repo_dir=repo_dir,
            )

            # Both commits should be included since there is no prev
            self.assertIn("### Features", notes)
            self.assertIn("- feat: first commit", notes)
            self.assertIn("### Fixes", notes)
            self.assertIn("- fix: second commit", notes)

            # Footer must have download sentence but NO full changelog link
            self.assertIn("Download `basara4-windows.zip`", notes)
            self.assertNotIn("**Full changelog**", notes)


if __name__ == "__main__":
    unittest.main()
