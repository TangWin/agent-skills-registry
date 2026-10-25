"""隔离临时仓库中的回归测试：不读取凭据、不联网、不修改用户 skills。"""
from contextlib import contextmanager, ExitStack
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_skills as audit
import skill_ops as ops
import web_server as web


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.root = self.base / "registry"
        self.upstream = self.base / "upstream"
        for repo in (self.root, self.upstream):
            repo.mkdir()
            ops.git(repo, "init", "-b", "main")
            ops.git(repo, "config", "user.name", "Fixture")
            ops.git(repo, "config", "user.email", "fixture@example.invalid")
            ops.git(repo, "config", "commit.gpgsign", "false")
        for name in ("alpha", "beta"):
            path = self.upstream / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: fixture\n---\nold\n")
            shutil.copytree(path, self.root / name)
        ops.git(self.upstream, "add", ".")
        ops.git(self.upstream, "commit", "-m", "initial")
        self.base_commit = ops.git(self.upstream, "rev-parse", "HEAD")
        self.manifest = self.root / "SKILLS.md"
        columns = "名称 | 唯一维护目录 | GitHub或官网 | 简介 | 当前版本、commit 或锁定哈希 | 安装或更新说明 | 适用 Agent | 最后检查"
        text = "## 已纳管\n| " + columns + " |\n|" + "---|" * 8 + "\n"
        for name in ("alpha", "beta"):
            text += f"| {name} | `{self.root / name}` | https://github.com/example/skills | fixture | `{self.base_commit}` | 仓库子目录 `skills/{name}` | Codex、Grok | 2026-01-01 |\n"
        text += "\n## 待确认纳管\n保留文字\n"
        self.manifest.write_text(text)
        self.stack.enter_context(patch.multiple(audit, CANONICAL_ROOT=self.root, MANIFEST_PATH=self.manifest, LINK_ROOTS={}, LEGACY_ROOTS={}))
        self.stack.enter_context(patch.multiple(web, CANONICAL_ROOT=self.root, MANIFEST_PATH=self.manifest))
        ops.git(self.root, "add", ".")
        ops.git(self.root, "commit", "-m", "initial registry")
        self.head = ops.git(self.root, "rev-parse", "HEAD")
        self.remote = self.base / "backup.git"
        ops.git(self.base, "clone", "--bare", str(self.root), str(self.remote))
        ops.git(self.root, "remote", "add", "origin", str(self.remote))
        self.stack.enter_context(patch.object(ops, "BACKUP_REMOTE", str(self.remote)))
        self.stack.enter_context(patch.object(ops, "checkout", self.local_checkout))

    @contextmanager
    def local_checkout(self, repo, revision=None):
        with tempfile.TemporaryDirectory(dir=self.base) as temp:
            clone = Path(temp) / "clone"
            ops.git(Path(temp), "clone", "--local", str(self.upstream), str(clone))
            if revision:
                ops.git(clone, "checkout", "--detach", revision)
            yield clone, ops.git(clone, "rev-parse", "HEAD")

    def upstream_change(self, names=("alpha",)):
        for name in names:
            file = self.upstream / "skills" / name / "SKILL.md"
            file.write_text(file.read_text() + "new\n")
        ops.git(self.upstream, "add", ".")
        ops.git(self.upstream, "commit", "-m", "update")
        return ops.git(self.upstream, "rev-parse", "HEAD")

    def assert_original(self):
        self.assertEqual(ops.git(self.root, "rev-parse", "HEAD"), self.head)
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")
        self.assertTrue((self.root / "alpha" / "SKILL.md").read_text().endswith("old\n"))
        self.assertFalse((ops.git_dir(self.root) / "skill-manager-transaction").exists())

    def test_strict_manifest_errors(self):
        original = self.manifest.read_text()
        for text in (original.replace("Codex、Grok", "Codxe、Grok"),
                     original.replace("| beta |", "| alpha |"),
                     original.replace("fixture | fixture", "fixture | extra | fixture"),
                     original.replace(str(self.root / "alpha"), "/tmp/alpha")):
            if text == original:
                continue
            self.manifest.write_text(text)
            with self.assertRaises(ValueError):
                audit.parse_manifest(self.manifest)
        self.manifest.write_text(original.replace("| alpha |", "| alpha | extra |"))
        self.assertEqual(audit.inspect()["findings"][0]["status"], "invalid-manifest")

    def test_no_update_when_other_subdirectory_changes(self):
        self.upstream_change(("beta",))
        result = ops.prepare(self.root, "alpha")
        self.assertEqual(result["status"], "latest")
        self.assertFalse(result["has_update"])
        self.assert_original()

    def test_offline_does_not_invent_success(self):
        with patch.object(ops, "checkout", side_effect=OSError("offline")):
            result = web.check_skill_update("alpha")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("diff_summary", result)

    def test_dirty_workspace_blocks_before_checkout(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        dirty = self.root / "unrelated.txt"
        dirty.write_text("keep")
        with patch.object(ops, "checkout") as clone:
            with self.assertRaisesRegex(ValueError, "未提交"):
                ops.apply(self.root, plan)
            clone.assert_not_called()
        self.assertEqual(dirty.read_text(), "keep")

    def test_local_content_drift_is_not_overwritten(self):
        (self.root / "alpha" / "SKILL.md").write_text("custom")
        with self.assertRaisesRegex(ValueError, "本地内容"):
            ops.prepare(self.root, "alpha")

    def test_pinned_commit_installed_after_upstream_moves(self):
        expected = self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        self.upstream_change()
        result = ops.apply(self.root, plan)
        self.assertTrue(result["ok"])
        record = json.loads((self.root / ".skill-locks/alpha.json").read_text())
        self.assertEqual(record["commit"], expected)
        self.assertEqual(record["content_hash"], audit.folder_hash(self.root / "alpha"))
        self.assertEqual(record["status"], "candidate")
        files = ops.git(self.root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
        self.assertEqual(set(files), {"alpha/SKILL.md", "SKILLS.md", ".skill-locks/alpha.json"})
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(ops.git(self.root, "ls-remote", "origin", "refs/heads/main").split()[0], self.head)

    def test_ignored_upstream_file_survives_candidate_checkout(self):
        source = self.upstream / "skills/alpha"
        (source / ".gitignore").write_text("helper.py\n")
        helper = source / "helper.py"
        helper.write_text("print('fixture')\n")
        helper.chmod(0o755)
        ops.git(self.upstream, "add", ".")
        ops.git(self.upstream, "add", "-f", "skills/alpha/helper.py")
        ops.git(self.upstream, "commit", "-m", "tracked ignored helper")
        plan = ops.prepare(self.root, "alpha")
        ops.apply(self.root, plan)
        restored = self.base / "restored"
        ops.git(self.base, "clone", "--local", str(self.root), str(restored))
        self.assertEqual((restored / "alpha/helper.py").read_bytes(), helper.read_bytes())
        self.assertTrue((restored / "alpha/helper.py").stat().st_mode & 0o111)
        self.assertEqual(audit.folder_hash(restored / "alpha"), plan["target_hash"])

    def test_incomplete_index_rejects_update_and_restores_original(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        real_git = ops.git
        def skip_add(root, *args):
            if args[0] == "add":
                return ""
            return real_git(root, *args)
        with patch.object(ops, "git", side_effect=skip_add):
            with self.assertRaisesRegex(ValueError, "暂存快照"):
                ops.apply(self.root, plan)
        self.assert_original()

    def test_copy_failure_preserves_original(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        with patch.object(ops.shutil, "copytree", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                ops.apply(self.root, plan)
        self.assert_original()

    def test_validation_failure_restores_content_manifest_and_index(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        with patch.object(ops, "assert_target_audit", side_effect=ValueError("validation failed")):
            with self.assertRaises(ValueError):
                ops.apply(self.root, plan)
        self.assert_original()

    def test_commit_failure_restores_content_manifest_and_index(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        real_git = ops.git
        def fail_commit(root, *args):
            if "commit" in args:
                raise ValueError("commit failed")
            return real_git(root, *args)
        with patch.object(ops, "git", side_effect=fail_commit):
            with self.assertRaises(ValueError):
                ops.apply(self.root, plan)
        self.assert_original()

    def test_batch_generates_separate_commits(self):
        self.upstream_change(("alpha", "beta"))
        plans = [ops.prepare(self.root, name) for name in ("alpha", "beta")]
        result = ops.apply_many(self.root, plans)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["updated_skills"], ["alpha", "beta"])
        self.assertEqual(ops.git(self.root, "rev-list", "--count", f"{self.head}..HEAD"), "2")
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")

    def test_unbacked_baseline_blocks(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        (self.root / "note.md").write_text("not backed up")
        ops.git(self.root, "add", ".")
        ops.git(self.root, "commit", "-m", "new local")
        with self.assertRaisesRegex(ValueError, "尚未备份"):
            ops.apply(self.root, plan)

    def test_scope_cannot_disable_direct_reader(self):
        with self.assertRaisesRegex(ValueError, "目录布局"):
            ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex"]}])
        self.assert_original()

    def test_agent_keys_cannot_remove_links_when_only_metadata_is_sent(self):
        with self.assertRaises(ValueError):
            ops.change_agents(self.root, [{"name": "alpha", "description": "new"}])
        self.assert_original()

    def test_wrong_target_link_not_deleted(self):
        link_root = self.base / "claude"
        link_root.mkdir()
        link = link_root / "alpha"
        link.symlink_to(self.root / "beta")
        with patch.object(audit, "LINK_ROOTS", {"claude-code": link_root}):
            with self.assertRaises(ValueError):
                ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok"]}])
        self.assertEqual(link.resolve(), self.root / "beta")

    def test_external_link_is_not_updatable(self):
        shutil.rmtree(self.root / "alpha")
        (self.root / "alpha").symlink_to(self.upstream / "skills/alpha")
        with self.assertRaisesRegex(ValueError, "外部自管"):
            ops.prepare(self.root, "alpha")

    def test_upstream_symlinks_and_secrets_are_rejected(self):
        (self.upstream / "skills/alpha/leak").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "软链接"):
            ops.validate_tree(self.upstream / "skills/alpha")
        (self.upstream / "skills/alpha/leak").unlink()
        (self.upstream / "skills/alpha/.env").write_text("dummy")
        with self.assertRaisesRegex(ValueError, "敏感文件名"):
            ops.validate_tree(self.upstream / "skills/alpha")

    def test_hash_detects_executable_permission_change(self):
        file = self.root / "alpha/SKILL.md"
        before = audit.folder_hash(file.parent)
        file.chmod(0o755)
        self.assertNotEqual(before, audit.folder_hash(file.parent))

    def test_scoped_path_is_used(self):
        target = self.root / ".scoped/codex/alpha"
        target.parent.mkdir(parents=True)
        shutil.move(self.root / "alpha", target)
        self.manifest.write_text(self.manifest.read_text().replace(str(self.root / "alpha"), str(target)).replace("| Codex、Grok |", "| Codex |", 1))
        item = next(s for s in web.parse_skills_full() if s["name"] == "alpha")
        self.assertTrue(item["is_scoped"])
        self.assertEqual(item["path"], str(target))
        self.assertIn("old", item["skill_content"])

    def test_interrupted_transaction_can_be_recovered(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        with patch.object(ops, "assert_target_audit", side_effect=ValueError("failed")), patch.object(ops, "restore_transaction", side_effect=SystemExit("simulated crash")):
            with self.assertRaises(SystemExit):
                ops.apply(self.root, plan)
        with self.assertRaisesRegex(ValueError, "未完成事务"):
            ops.preflight(self.root)
        ops.restore_transaction(self.root)
        self.assert_original()

    def test_recovery_after_commit_does_not_undo_commit(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        transaction = ops.git_dir(self.root) / "skill-manager-transaction"
        real_remove = shutil.rmtree
        def stop_cleanup(path, *args, **kwargs):
            if Path(path) == transaction:
                raise OSError("simulated cleanup crash")
            return real_remove(path, *args, **kwargs)
        with patch.object(ops.shutil, "rmtree", side_effect=stop_cleanup):
            with self.assertRaises(OSError):
                ops.apply(self.root, plan)
        new_head = ops.git(self.root, "rev-parse", "HEAD")
        ops.restore_transaction(self.root)
        self.assertEqual(ops.git(self.root, "rev-parse", "HEAD"), new_head)
        self.assertNotEqual(new_head, self.head)
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")

    def test_partial_group_reports_completed_and_restores_failed_item(self):
        self.upstream_change(("alpha", "beta"))
        plans = [ops.prepare(self.root, name) for name in ("alpha", "beta")]
        real_audit = ops.assert_target_audit
        def validate(name):
            if name == "beta":
                raise ValueError("beta validation failed")
            real_audit(name)
        with patch.object(ops, "assert_target_audit", side_effect=validate):
            result = ops.apply_many(self.root, plans)
        self.assertFalse(result["ok"])
        self.assertEqual(result["completed"], ["alpha"])
        self.assertEqual(ops.git(self.root, "rev-list", "--count", f"{self.head}..HEAD"), "1")
        self.assertTrue((self.root / "beta/SKILL.md").read_text().endswith("old\n"))
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")

    def test_agent_link_failure_restores_manifest_and_links(self):
        link_root = self.base / "claude"
        link_root.mkdir()
        with patch.object(audit, "LINK_ROOTS", {"claude-code": link_root}), patch.object(ops, "commit_paths", side_effect=OSError("commit failed")):
            result = ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Claude Code"]}])
        self.assertFalse(result["ok"])
        self.assertFalse((link_root / "alpha").is_symlink())
        self.assert_original()

    def test_agent_keyboard_interrupt_restores_manifest_and_links(self):
        link_root = self.base / "claude"
        with patch.object(audit, "LINK_ROOTS", {"claude-code": link_root}), patch.object(ops, "commit_paths", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Claude Code"]}])
        self.assertFalse((link_root / "alpha").is_symlink())
        self.assert_original()

    def test_agent_crash_recovers_added_and_removed_links(self):
        claude = self.base / "claude"
        cursor = self.base / "cursor"
        links = {"claude-code": claude, "cursor": cursor}
        with patch.object(audit, "LINK_ROOTS", links):
            ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Claude Code"]}])
            old_manifest = self.manifest.read_bytes()
            old_link = (claude / "alpha").readlink()
            head = ops.git(self.root, "rev-parse", "HEAD")
            with patch.object(ops, "commit_paths", side_effect=KeyboardInterrupt), patch.object(ops, "restore_transaction", side_effect=SystemExit("crash")):
                with self.assertRaises(SystemExit):
                    ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Cursor"]}])
            self.assertFalse((claude / "alpha").is_symlink())
            self.assertTrue((cursor / "alpha").is_symlink())
            with self.assertRaisesRegex(ValueError, "未完成事务"):
                ops.preflight(self.root)
            ops.restore_transaction(self.root)
            self.assertEqual(self.manifest.read_bytes(), old_manifest)
            self.assertEqual((claude / "alpha").readlink(), old_link)
            self.assertFalse((cursor / "alpha").is_symlink())
            self.assertEqual(ops.git(self.root, "rev-parse", "HEAD"), head)
            self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")

    def test_agent_recovery_preserves_later_link_changes(self):
        link_root = self.base / "claude"
        with patch.object(audit, "LINK_ROOTS", {"claude-code": link_root}), patch.object(ops, "commit_paths", side_effect=KeyboardInterrupt), patch.object(ops, "restore_transaction", side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Claude Code"]}])
        link = link_root / "alpha"
        link.unlink()
        link.symlink_to(self.root / "beta")
        with self.assertRaisesRegex(ValueError, "链接已变化"):
            ops.restore_transaction(self.root)
        self.assertEqual(link.resolve(), self.root / "beta")
        self.assertTrue((ops.git_dir(self.root) / "skill-manager-transaction").exists())

    def test_agent_recovery_after_commit_preserves_new_links(self):
        link_root = self.base / "claude"
        transaction = ops.git_dir(self.root) / "skill-manager-transaction"
        real_remove = shutil.rmtree
        def stop_cleanup(path, *args, **kwargs):
            if Path(path) == transaction:
                raise OSError("cleanup crash")
            return real_remove(path, *args, **kwargs)
        with patch.object(audit, "LINK_ROOTS", {"claude-code": link_root}), patch.object(ops.shutil, "rmtree", side_effect=stop_cleanup):
            with self.assertRaises(OSError):
                ops.change_agents(self.root, [{"name": "alpha", "agents": ["Codex", "Grok", "Claude Code"]}])
        head = ops.git(self.root, "rev-parse", "HEAD")
        ops.restore_transaction(self.root)
        self.assertNotEqual(head, self.head)
        self.assertEqual(ops.git(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual((link_root / "alpha").resolve(), self.root / "alpha")
        self.assertEqual(ops.git(self.root, "status", "--porcelain"), "")

    def test_lock_prevents_second_writer(self):
        with ops.exclusive(self.root):
            with self.assertRaisesRegex(ValueError, "已有写操作"):
                with ops.exclusive(self.root):
                    self.fail("second writer acquired lock")

    def test_stable_status_requires_matching_tag(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        ops.apply(self.root, plan)
        record = ops.lock_record(self.root, "alpha")
        self.assertEqual(ops.version_state(self.root, "alpha", record), "candidate")
        ops.git(self.root, "tag", "stable/alpha/test")
        self.assertEqual(ops.version_state(self.root, "alpha", record), "stable")
        self.assertEqual(ops.version_state(self.root, "alpha", {**record, "content_hash": "0" * 64}), "candidate")

    def test_push_scans_deleted_secrets_in_outgoing_history(self):
        secret = self.root / "secret.key"
        secret.write_text("dummy fixture")
        ops.git(self.root, "add", "secret.key")
        ops.git(self.root, "commit", "-m", "accidental file")
        secret.unlink()
        ops.git(self.root, "add", "secret.key")
        ops.git(self.root, "commit", "-m", "delete file")
        with self.assertRaisesRegex(ValueError, "敏感文件名"):
            ops.push_backup(self.root)
        self.assertEqual(ops.git(self.root, "ls-remote", "origin", "refs/heads/main").split()[0], self.head)

    def test_duplicate_scoped_name_is_reported(self):
        target = self.root / ".scoped/codex/alpha"
        target.parent.mkdir(parents=True)
        shutil.copytree(self.root / "alpha", target)
        self.assertIn("duplicate-name", {f["status"] for f in audit.inspect()["findings"]})

    def test_metadata_symlink_blocks_writes(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / ".skill-locks").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "不得是软链接"):
            ops.preflight(self.root)


    def test_multiple_push_destinations_are_rejected(self):
        ops.git(self.root, "remote", "set-url", "--add", "--push", "origin", str(self.remote))
        ops.git(self.root, "remote", "set-url", "--add", "--push", "origin", str(self.base / "unexpected.git"))
        with self.assertRaisesRegex(ValueError, "推送地址"):
            ops.push_backup(self.root)

    def test_backup_push_does_not_mark_candidate_stable(self):
        self.upstream_change()
        plan = ops.prepare(self.root, "alpha")
        ops.apply(self.root, plan)
        result = ops.push_backup(self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(ops.git(self.root, "ls-remote", "origin", "refs/heads/main").split()[0], ops.git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(ops.git(self.root, "tag", "--list", "stable/*"), "")



class HTTPTest(unittest.TestCase):
    def handler(self, path="/api/git-push", origin=None, token="session", host="127.0.0.1:8765", write=False, body=b"{}"):
        handler = object.__new__(web.SkillsAPIHandler)
        handler.path = path
        handler.headers = Message()
        for key, value in {"Host": host, "X-Skill-Token": token, "Content-Type": "application/json", "Content-Length": str(len(body))}.items():
            handler.headers[key] = value
        if origin:
            handler.headers["Origin"] = origin
        handler.server = SimpleNamespace(server_port=8765, api_token="session", allow_write=write, operation_lock=threading.Lock(), plans={})
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        handler.send_error = Mock()
        return handler

    def test_foreign_origin_and_host_and_missing_token_rejected(self):
        for options in ({"origin": "https://evil.invalid"}, {"host": "evil.invalid:8765"}, {"token": ""}):
            h = self.handler(**options)
            with patch.object(ops, "push_backup") as push:
                h.do_POST()
                push.assert_not_called()
            self.assertEqual(h.send_error.call_args.args[0], 403)

    def test_read_only_rejects_all_write_routes(self):
        for path in ("/api/git-push", "/api/batch-save", "/api/upgrade-skill", "/api/upgrade-group"):
            h = self.handler(path)
            h.do_POST()
            self.assertEqual(h.send_json.call_args.args[1], 403)

    def test_bad_json_rejected(self):
        for body in (b"[]", b"{", b"null"):
            h = self.handler("/api/check-update", body=body)
            h.do_POST()
            self.assertEqual(h.send_json.call_args.args[1], 400)

    def test_expired_preview_not_applied(self):
        h = self.handler("/api/upgrade-skill", write=True, body=b'{"plan_id":"old"}')
        h.server.plans["old"] = (time.monotonic() - 901, [{"name": "alpha"}])
        with patch.object(ops, "apply_many") as apply:
            h.do_POST()
            apply.assert_not_called()
        self.assertEqual(h.send_json.call_args.args[1], 409)

    def test_preview_is_one_use(self):
        h = self.handler("/api/upgrade-skill", write=True, body=b'{"plan_id":"valid"}')
        plans = [{"name": "alpha"}]
        h.server.plans["valid"] = (time.monotonic(), plans)
        with patch.object(ops, "apply_many", return_value={"ok": True}) as apply:
            h.do_POST()
            apply.assert_called_once_with(web.CANONICAL_ROOT, plans)
        self.assertNotIn("valid", h.server.plans)


if __name__ == "__main__":
    unittest.main()
