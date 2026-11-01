# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Real Git release lifecycle tests, runnable with only Python and Git."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import release
from tools.check_release_policy import check


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("remote", "add", "origin", str(self.remote))
        for relative in release.VERSION_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith(".toml"):
                path.write_text('[project]\nname = "release-test"\nversion = "1.0.0"\n')
            else:
                path.write_text('{\n  "name": "release-test",\n  "version": "1.0.0"\n}\n')
        (self.root / "CHANGELOG.md").write_text("# Changelog\n\n" + release.CHANGELOG_ANCHOR)
        (self.root / "app.txt").write_text("initial\n")
        self.commit("feat: initial")
        self.git("tag", "v1.0.0")
        self.git("push", "origin", "main", "--tags")
        self.git("checkout", "-b", "release/1.1")
        self.git("push", "origin", "release/1.1")
        self.patcher = patch.object(release, "ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def prepare(self, channel, version=None, push=True):
        branch = self.git("branch", "--show-current")
        tags = release.version_tags()
        version = version or release.next_version(branch, channel, tags)
        previous = release.latest_tag("HEAD")
        source = self.git("rev-parse", "HEAD")
        for relative in release.VERSION_FILES:
            release.set_version(self.root / relative, version)
        path = self.root / "CHANGELOG.md"
        path.write_text(release.prepend_changelog(path.read_text(), f"## [{version}]", version))
        notes = self.root / ".github/release-notes.md"
        notes.parent.mkdir(exist_ok=True)
        notes.write_text(release.render_release_notes(version, previous, source, [], []))
        release.write_manifest(self.root / ".release.toml", version, channel, previous, source, [])
        target = self.commit(f"chore(release): v{version}")
        if push:
            self.git("push", "origin", branch)
        return target, version

    def test_alpha_beta_rc_stable_patch_and_retry(self):
        expected = ["1.1.0-alpha.1", "1.1.0-alpha.2", "1.1.0-beta.1", "1.1.0-rc.1", "1.1.0", "1.1.1-rc.1", "1.1.1"]
        for channel, version in zip(["alpha", "alpha", "beta", "rc", "stable", "rc", "stable"], expected, strict=True):
            before = self.git("rev-parse", "HEAD")
            target, actual = self.prepare(channel)
            self.assertEqual(actual, version)
            self.assertEqual(release.resolve_release_push(before, target, "release/1.1"), target)
            metadata = release.validate_target(target, "release/1.1")
            self.assertEqual(metadata["promote_latest"], str(channel == "stable").lower())
            self.git("tag", f"v{version}")
            self.assertEqual(release.validate_target(target, "release/1.1"), metadata)

    def test_branch_cut_and_normal_push_do_not_publish(self):
        target = self.git("rev-parse", "HEAD")
        self.assertIsNone(release.resolve_release_push("0" * 40, target, "release/1.1"))
        (self.root / "app.txt").write_text("a fix\n")
        after = self.commit("fix: ordinary backport")
        self.assertIsNone(release.resolve_release_push(target, after, "release/1.1"))

    def test_main_and_mismatched_line_are_rejected(self):
        target, _ = self.prepare("beta")
        with self.assertRaises(release.ReleaseError):
            release.validate_target(target, "main")
        with self.assertRaises(release.ReleaseError):
            release.validate_progression("1.2.0", "stable", "release/1.1", [])
        self.git("checkout", "main")
        with self.assertRaises(release.ReleaseError):
            check("main", "release/1.1", target)

    def test_main_rejects_release_history_under_unrelated_branch_name(self):
        self.prepare("stable")
        self.git("checkout", "-b", "sync-fixes")
        (self.root / "app.txt").write_text("ordinary fix after release metadata\n")
        target = self.commit("fix: ordinary branch tip")
        for base in ("main", "refs/heads/main"):
            with self.subTest(base=base), self.assertRaisesRegex(release.ReleaseError, "Release metadata"):
                check(base, "sync-fixes", target)

    def test_main_allows_ordinary_changes_after_existing_release_metadata(self):
        self.git("checkout", "main")
        (self.root / "app.txt").write_text("historical release\n")
        self.commit("chore(release): v1.0.1")
        self.git("push", "origin", "main")
        self.git("checkout", "-b", "fix/ordinary")
        (self.root / "app.txt").write_text("ordinary fix\n")
        target = self.commit("fix: ordinary change")
        check("main", "fix/ordinary", target)

    def test_downgrade_duplicate_and_invalid_versions(self):
        for version, channel in [("1.1.0-alpha.2", "alpha"), ("1.1.0-beta.1", "beta")]:
            with self.assertRaises(release.ReleaseError):
                release.validate_progression(version, channel, "release/1.1", ["v1.1.0-beta.1"])
        for version in ["01.1.0", "1.1.0-rc.0", "1.1.0-rc.01", "1.1.0+metadata"]:
            with self.assertRaises(release.ReleaseError):
                release.version_key(version)

    def test_older_maintenance_cannot_move_latest(self):
        self.git("tag", "v2.0.0")
        target, _ = self.prepare("stable")
        metadata = release.validate_target(target, "release/1.1")
        self.assertEqual(metadata["dist_tag"], "lts-1.1")
        self.assertEqual(metadata["promote_latest"], "false")
        self.assertEqual(release.distribution_tag("1.1.0-rc.1", "rc", []), "next")

    def test_tag_collision_and_old_retry_are_rejected(self):
        target, version = self.prepare("beta")
        self.git("tag", f"v{version}", "HEAD^")
        with self.assertRaisesRegex(release.ReleaseError, "different commit"):
            release.validate_target(target, "release/1.1")
        self.git("tag", "-d", f"v{version}")
        self.git("tag", "v1.1.0-rc.1")
        with self.assertRaisesRegex(release.ReleaseError, "advance"):
            release.validate_target(target, "release/1.1")

    def test_application_changes_cannot_hide_in_metadata(self):
        (self.root / "app.txt").write_text("smuggled code\n")
        target, _ = self.prepare("rc")
        with self.assertRaisesRegex(release.ReleaseError, "forbidden"):
            release.validate_target(target, "release/1.1")

    def test_candidate_stale_base_and_version_mismatch(self):
        target, _ = self.prepare("rc", push=False)
        release.validate_target(target, "release/1.1", candidate=True)
        check("release/1.1", "prepare/v1.1.0-rc.1", target)
        self.git("update-ref", "refs/remotes/origin/release/1.1", target)
        with self.assertRaisesRegex(release.ReleaseError, "stale"):
            release.validate_target(target, "release/1.1", candidate=True)
        path = self.root / "web/package.json"
        path.write_text(json.dumps({"version": "9.9.9"}))
        self.git("add", ".")
        self.git("commit", "--amend", "--no-edit")
        target = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/release/1.1", target)
        with self.assertRaisesRegex(release.ReleaseError, "disagree"):
            release.validate_target(target, "release/1.1")

    def test_main_continues_and_only_selected_fix_is_backported(self):
        self.git("checkout", "main")
        (self.root / "future.txt").write_text("future feature\n")
        self.commit("feat: not for stable")
        (self.root / "app.txt").write_text("fixed\n")
        source = self.commit("fix: stable bug")
        self.git("push", "origin", "main")
        self.git("checkout", "release/1.1")
        self.git("cherry-pick", "-x", source)
        check("release/1.1", "backport/1.1/42", "HEAD")
        self.assertFalse((self.root / "future.txt").exists())
        self.assertIn(f"cherry picked from commit {source}", self.git("log", "-1", "--format=%B"))
        self.git("push", "origin", "release/1.1")
        target, _ = self.prepare("stable")
        release.validate_target(target, "release/1.1")

    def test_missing_backport_provenance_fails(self):
        (self.root / "app.txt").write_text("release only fix\n")
        self.commit("fix: missing upstream")
        with self.assertRaisesRegex(release.ReleaseError, "provenance"):
            check("release/1.1", "fix/bad", "HEAD")


if __name__ == "__main__":
    unittest.main()
