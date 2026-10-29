"""使用临时真实仓库验证发布后收尾，不连接网络或操作项目仓库。"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup.py"
DOC = Path("ai_work/docs/商品/独立 功能/需求.md")


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-cleanup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "主 仓库"
        self.repo.mkdir()
        self.state = self.root / "收尾 状态"
        self.env = os.environ.copy()
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Cleanup Test",
            "GIT_AUTHOR_EMAIL": "cleanup@test.invalid",
            "GIT_COMMITTER_NAME": "Cleanup Test",
            "GIT_COMMITTER_EMAIL": "cleanup@test.invalid",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git(self.repo, "init", "-b", "master")
        self.git(self.repo, "config", "core.hooksPath", os.devnull)
        self.git(self.repo, "config", "commit.gpgsign", "false")
        self.write(self.repo / ".gitignore", "ai_work/\nprivate/\ntarget/\n")
        self.write(self.repo / "code.txt", "initial\n")
        self.commit(self.repo, "initial")
        self.initial = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @contextmanager
    def isolated_scenario(self):
        scenario = type(self)("runTest")
        scenario.setUp()
        try:
            yield scenario
        finally:
            scenario.doCleanups()

    def git(self, repo, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, env=self.env, check=check,
        )

    def commit(self, repo, message):
        self.git(repo, "add", "--all")
        self.git(repo, "commit", "-m", message)

    def feature(self, name="feature/done", merged=True):
        path = self.root / ("功能 worktree " + name.replace("/", "-"))
        self.git(self.repo, "worktree", "add", "-b", name, str(path), "master")
        self.write(path / "code.txt", name + "\n")
        self.commit(path, name)
        if merged:
            self.git(self.repo, "merge", "--ff-only", name)
        return path

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, args)],
            text=True, capture_output=True, env=self.env,
        )

    def plan(self, *branches, all_features=False):
        args = ["plan", "--repo", self.repo, "--state", self.state]
        if all_features:
            args.append("--all-features")
        for branch in branches:
            args.extend(["--branch", branch])
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.state / "plan.json").is_file())
        json.loads((self.state / "plan.json").read_text(encoding="utf-8"))
        return result

    def apply(self):
        return self.cli("apply", "--state", self.state, "--released", "用户确认已发布生产")

    def assert_gone(self, path=None, branch="feature/done"):
        if path is not None:
            self.assertFalse(path.exists())
        self.assertNotEqual(
            self.git(self.repo, "show-ref", "--verify", "refs/heads/" + branch,
                     check=False).returncode, 0,
        )

    def assert_kept(self, path=None, branch="feature/done"):
        if path is not None:
            self.assertTrue(path.exists())
        self.assertEqual(
            self.git(self.repo, "show-ref", "--verify", "refs/heads/" + branch,
                     check=False).returncode, 0,
        )

    def assert_backup(self, relative_path, expected):
        found = False
        for archive in self.state.rglob("*.tar.gz"):
            with tarfile.open(archive, "r:gz") as handle:
                for member in handle.getmembers():
                    if member.isfile() and (
                        member.name == relative_path
                        or member.name.endswith("/" + relative_path)
                    ):
                        self.assertEqual(handle.extractfile(member).read(), expected)
                        found = True
        self.assertTrue(found, "归档未保留 " + relative_path)

    def test_moves_chinese_documents_and_preserves_main_untracked_file(self):
        tree = self.feature()
        self.write(tree / DOC, "需求：已上线\n")
        resource = Path("ai_work/resources/商品/独立 功能/模板 文件.txt")
        self.write(tree / resource, "resource\n")
        self.write(self.repo / "CONTEXT.md", "主目录原有内容\n")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assertEqual((self.repo / DOC).read_text(encoding="utf-8"), "需求：已上线\n")
        self.assertEqual((self.repo / resource).read_text(encoding="utf-8"), "resource\n")
        self.assertEqual((self.repo / "CONTEXT.md").read_text(encoding="utf-8"), "主目录原有内容\n")
        self.assert_backup(str(DOC), "需求：已上线\n".encode())

    def test_deletes_merged_feature_when_main_checkout_is_old_pre(self):
        tree = self.feature()
        self.git(self.repo, "branch", "pre", self.initial)
        self.git(self.repo, "switch", "pre")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assertEqual(self.git(self.repo, "branch", "--show-current").stdout.strip(), "pre")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), self.initial)

    def test_preserves_unmerged_feature(self):
        tree = self.feature(merged=False)
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / "code.txt").read_text(), "feature/done\n")

    def test_preserves_tracked_modification(self):
        tree = self.feature()
        self.write(tree / "code.txt", "未完成源码\n")
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / "code.txt").read_text(encoding="utf-8"), "未完成源码\n")

    def test_archives_unique_ignored_files_without_copying_into_main_source(self):
        tree = self.feature()
        self.write(tree / "private/ignored_test.py", "unique local test\n")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assert_backup("private/ignored_test.py", b"unique local test\n")
        self.assertFalse((self.repo / "private/ignored_test.py").exists())

    def test_document_conflict_blocks_only_that_tree_and_can_resume(self):
        first = self.feature("feature/conflict")
        second = self.feature("feature/ready")
        self.write(first / DOC, "功能文档\n")
        self.write(self.repo / DOC, "主目录文档\n")
        self.plan("feature/conflict", "feature/ready")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(first, "feature/conflict")
        self.assert_gone(second, "feature/ready")
        self.assertEqual((self.repo / DOC).read_text(encoding="utf-8"), "主目录文档\n")
        self.write(self.repo / DOC, "功能文档\n")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(first, "feature/conflict")

    def test_repeat_apply_is_safe_and_does_not_add_archives(self):
        tree = self.feature()
        self.write(tree / DOC, "document\n")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = {str(p.relative_to(self.state)): p.read_bytes()
                    for p in self.state.rglob("*.tar.gz")}
        self.assertTrue(archives)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assertEqual(archives, {str(p.relative_to(self.state)): p.read_bytes()
                                    for p in self.state.rglob("*.tar.gz")})

    def test_requires_release_confirmation(self):
        tree = self.feature()
        self.plan("feature/done")
        for args in [[], ["--released", ""]]:
            result = self.cli("apply", "--state", self.state, *args)
            self.assertNotEqual(result.returncode, 0)
            self.assert_kept(tree)

    def test_ignored_file_change_after_plan_blocks_deletion(self):
        tree = self.feature()
        self.write(tree / "private/evidence.txt", "before\n")
        self.plan("feature/done")
        self.write(tree / "private/evidence.txt", "after\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / "private/evidence.txt").read_text(), "after\n")

    def test_new_untracked_file_after_plan_blocks_deletion(self):
        tree = self.feature()
        self.plan("feature/done")
        self.write(tree / "new.txt", "created concurrently\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / "new.txt").read_text(), "created concurrently\n")

    def test_feature_head_change_after_plan_blocks_deletion(self):
        tree = self.feature()
        self.plan("feature/done")
        self.write(tree / "code.txt", "another change\n")
        self.commit(tree, "new feature commit")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)

    def test_master_movement_after_plan_blocks_deletion(self):
        tree = self.feature()
        self.plan("feature/done")
        self.write(self.repo / "other.txt", "master moved\n")
        self.commit(self.repo, "master advanced")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)

    def test_locked_worktree_is_kept(self):
        tree = self.feature()
        self.plan("feature/done")
        self.git(self.repo, "worktree", "lock", "--reason", "still in use", str(tree))
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)

    def test_deletes_merged_branch_without_worktree(self):
        self.git(self.repo, "branch", "feature/done", "master")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone()

    def test_protects_long_lived_branches_and_main_checkout(self):
        for branch in ["main", "dev", "pre", "test"]:
            self.git(self.repo, "branch", branch, "master")
        for branch in ["master", "main", "dev", "pre", "test"]:
            state = self.root / ("protected-" + branch)
            result = self.cli("plan", "--repo", self.repo, "--branch", branch, "--state", state)
            if (state / "plan.json").is_file():
                self.cli("apply", "--state", state, "--released", "已发布生产")
            self.assert_kept(branch=branch)
        self.assertTrue(self.repo.exists())
        self.assertEqual((self.repo / "code.txt").read_text(), "initial\n")

    def test_all_features_does_not_delete_environment_branches(self):
        tree = self.feature()
        self.git(self.repo, "branch", "dev", "master")
        self.plan(all_features=True)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assert_kept(branch="dev")

    def test_excludes_nested_maven_target_but_preserves_unknown_target_directory(self):
        self.write(self.repo / "modules/sub/pom.xml", "<project/>\n")
        self.commit(self.repo, "nested module")
        tree = self.feature()
        self.write(tree / "modules/sub/target/result.bin", "rebuildable\n")
        self.write(tree / "private/target/important.txt", "unique evidence\n")
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assert_backup("private/target/important.txt", b"unique evidence\n")
        for archive in self.state.rglob("*.tar.gz"):
            with tarfile.open(archive, "r:gz") as handle:
                self.assertFalse(any(member.name.endswith("modules/sub/target/result.bin")
                                     for member in handle.getmembers()))

    def test_ignored_symlink_blocks_cleanup_without_touching_external_target(self):
        tree = self.feature()
        external = self.root / "external.txt"
        self.write(external, "outside must remain\n")
        link = tree / "private/link.txt"
        link.parent.mkdir(parents=True)
        link.symlink_to(external)
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertTrue(link.is_symlink())
        self.assertEqual(external.read_text(), "outside must remain\n")

    def test_staged_and_working_changes_that_cancel_still_block_cleanup(self):
        tree = self.feature()
        original = (tree / "code.txt").read_text()
        self.write(tree / "code.txt", "staged unfinished work\n")
        self.git(tree, "add", "code.txt")
        self.write(tree / "code.txt", original)
        self.assertEqual(self.git(tree, "diff", "HEAD", "--quiet", check=False).returncode, 0)
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertNotEqual(self.git(tree, "diff", "--cached", "--quiet", check=False).returncode, 0)

    def test_new_worktree_occupancy_after_plan_blocks_branch_deletion(self):
        self.git(self.repo, "branch", "feature/done", "master")
        self.plan("feature/done")
        tree = self.root / "newly occupied"
        self.git(self.repo, "worktree", "add", str(tree), "feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)

    def test_tracked_change_after_plan_blocks_cleanup(self):
        tree = self.feature()
        self.plan("feature/done")
        self.write(tree / "code.txt", "concurrent unfinished edit\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / "code.txt").read_text(), "concurrent unfinished edit\n")

    def test_new_destination_conflict_after_plan_is_not_overwritten(self):
        tree = self.feature()
        self.write(tree / DOC, "source document\n")
        self.plan("feature/done")
        self.write(self.repo / DOC, "new destination document\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((self.repo / DOC).read_text(), "new destination document\n")
        self.assertEqual((tree / DOC).read_text(), "source document\n")

    def test_resolve_accepts_reviewed_merged_document_and_keeps_backup(self):
        tree = self.feature()
        self.write(tree / DOC, "source addition\n")
        self.write(self.repo / DOC, "existing master knowledge\n")
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        merged = "existing master knowledge\nsource addition\n"
        self.write(self.repo / DOC, merged)
        result = self.cli("resolve", "--state", self.state, "--branch", "feature/done",
                          "--file", str(DOC))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assertEqual((self.repo / DOC).read_text(), merged)
        self.assert_backup("source/" + str(DOC), b"source addition\n")
        self.assert_backup("destination/" + str(DOC), b"existing master knowledge\n")

    def test_corrupted_backup_prevents_resume_deletion(self):
        tree = self.feature()
        self.write(tree / DOC, "source document\n")
        self.write(self.repo / DOC, "conflict\n")
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        archives = list(self.state.rglob("*.tar.gz"))
        self.assertTrue(archives)
        archives[0].write_bytes(b"corrupted archive")
        self.write(self.repo / DOC, "source document\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((tree / DOC).read_text(), "source document\n")

    def test_resumes_after_worktree_removed_but_branch_deletion_failed(self):
        tree = self.feature()
        self.write(tree / DOC, "important document\n")
        self.plan("feature/done")
        hooks = self.repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "reference-transaction"
        self.write(hook, """#!/bin/sh
if [ "$1" = prepared ]; then
    while read old new ref; do
        if [ "$ref" = refs/heads/feature/done ] && [ "$new" = 0000000000000000000000000000000000000000 ]; then
            exit 1
        fi
    done
fi
exit 0
""")
        hook.chmod(0o700)
        self.git(self.repo, "config", "core.hooksPath", str(hooks))
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(tree.exists())
        self.assert_kept()
        self.assertEqual((self.repo / DOC).read_text(), "important document\n")
        self.git(self.repo, "config", "core.hooksPath", os.devnull)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)

    def test_repeat_apply_keeps_recreated_branch(self):
        tree = self.feature()
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.git(self.repo, "branch", "feature/done", "master")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept()

    def test_does_not_restore_main_tracked_sql_deleted_by_user(self):
        for staged in (False, True):
            with self.subTest(staged=staged), self.isolated_scenario() as case:
                tree = case.feature()
                relative = Path("db/existing.sql")
                case.write(case.repo / relative, "tracked master SQL\n")
                case.commit(case.repo, "master adds SQL after feature merged")
                case.write(tree / relative, "local SQL from feature\n")
                (case.repo / relative).unlink()
                if staged:
                    case.git(case.repo, "add", "--", str(relative))
                before = case.git(case.repo, "status", "--porcelain", "--", str(relative)).stdout
                case.plan("feature/done")
                case.assertNotEqual(case.apply().returncode, 0)
                case.assert_kept(tree)
                case.assertFalse((case.repo / relative).exists(), "不能恢复用户删除的已跟踪文件")
                case.assertEqual(
                    case.git(case.repo, "status", "--porcelain", "--", str(relative)).stdout,
                    before,
                )

    def test_resolved_destination_changed_again_blocks_cleanup(self):
        tree = self.feature()
        self.write(tree / DOC, "source addition\n")
        self.write(self.repo / DOC, "existing knowledge\n")
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.write(self.repo / DOC, "existing knowledge\nsource addition\n")
        resolved = self.cli("resolve", "--state", self.state, "--branch", "feature/done",
                            "--file", str(DOC))
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.write(self.repo / DOC, "a later unreviewed change\n")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((self.repo / DOC).read_text(), "a later unreviewed change\n")
        self.assertEqual((tree / DOC).read_text(), "source addition\n")

    def test_hidden_index_flags_block_cleanup_without_changing_user_flags(self):
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), self.isolated_scenario() as case:
                tree = case.feature()
                case.git(tree, "update-index", flag, "--", "code.txt")
                case.write(tree / "code.txt", "hidden user edit\n")
                case.assertEqual(case.git(tree, "diff", "--quiet", check=False).returncode, 0)
                flags_before = case.git(tree, "ls-files", "-v", "--", "code.txt").stdout
                case.plan("feature/done")
                case.assertNotEqual(case.apply().returncode, 0)
                case.assert_kept(tree)
                case.assertEqual((tree / "code.txt").read_text(), "hidden user edit\n")
                case.assertEqual(case.git(tree, "ls-files", "-v", "--", "code.txt").stdout,
                                 flags_before)

    def test_nested_git_repository_inside_maven_target_blocks_cleanup(self):
        self.write(self.repo / "modules/sub/pom.xml", "<project/>\n")
        self.commit(self.repo, "nested module")
        tree = self.feature()
        nested = tree / "modules/sub/target/local-repository"
        nested.mkdir(parents=True)
        self.git(nested, "init", "-b", "main")
        self.git(nested, "config", "core.hooksPath", os.devnull)
        self.git(nested, "config", "commit.gpgsign", "false")
        self.write(nested / "important.txt", "independent repository data\n")
        self.commit(nested, "independent history")
        head = self.git(nested, "rev-parse", "HEAD").stdout
        commit = self.git(nested, "cat-file", "-p", "HEAD").stdout
        self.plan("feature/done")
        self.assertNotEqual(self.apply().returncode, 0)
        self.assert_kept(tree)
        self.assertEqual((nested / "important.txt").read_text(), "independent repository data\n")
        self.assertEqual(self.git(nested, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(self.git(nested, "cat-file", "-p", "HEAD").stdout, commit)

    def test_ignored_hardlinks_are_archived_as_independently_readable_files(self):
        tree = self.feature()
        first = tree / "private/a.txt"
        second = tree / "private/b.txt"
        self.write(first, "shared hardlink content\n")
        os.link(first, second)
        self.assertEqual(first.stat().st_ino, second.stat().st_ino)
        self.plan("feature/done")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_gone(tree)
        self.assert_backup("private/a.txt", b"shared hardlink content\n")
        self.assert_backup("private/b.txt", b"shared hardlink content\n")


if __name__ == "__main__":
    unittest.main()
