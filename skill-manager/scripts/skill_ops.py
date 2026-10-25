#!/usr/bin/env python3
"""更新预览与事务执行；所有入口共用保护，网络只在显式检查/更新时使用。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import audit_skills as audit

BACKUP_REMOTE = "git@github.com:TangWin/agent-skills-registry.git"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True,
                            text=True, timeout=120, check=True)
    return result.stdout.strip()


def git_dir(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--absolute-git-dir"))


@contextmanager
def exclusive(root: Path):
    """进程间互斥，不把锁文件放进技能版本仓库。"""
    with (git_dir(root) / "skill-manager.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有写操作执行中，请稍后重试") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def row_for(root: Path, name: str) -> dict[str, Any]:
    rows = {r["_name"]: r for r in audit.manifest_rows(root / "SKILLS.md")}
    if name not in rows:
        raise ValueError("skill 未登记")
    return rows[name]


def source_for(row: dict[str, Any]) -> tuple[str, str]:
    match = re.search(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", row.get("GitHub或官网", ""))
    if not match:
        raise ValueError("来源没有明确 GitHub 仓库，无法自动判断")
    repo = match.group(1).removesuffix(".git")
    notes = row.get("安装或更新说明", "")
    sub = re.search(r"仓库子目录\s*`([^`]+)`", notes)
    if sub:
        subpath = sub.group(1)
        if subpath.startswith("/") or ".." in Path(subpath).parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", subpath):
            raise ValueError("仓库子目录非法")
    elif "仓库根目录 skill" in notes:
        subpath = ""
    else:
        raise ValueError("未明确登记仓库子目录或仓库根目录 skill")
    return repo, subpath


def checked_target(root: Path, row: dict[str, Any]) -> Path:
    target = row["_path"]
    if target.is_symlink() or target.resolve() != target or not target.is_dir():
        raise ValueError("外部自管链接、父级软链接或不存在的目录不能自动覆盖")
    target.relative_to(root)
    return target


def scan_file(name: str, data: bytes) -> None:
    """阻止明显凭据；只报告路径，不输出可能的秘密内容。"""
    base = Path(name).name.lower()
    if (base in {".env", "config.env", "id_rsa", "id_ed25519", "credentials"}
            or (base.startswith(".env.") and base != ".env.example")
            or base.endswith((".pem", ".key", ".p12", ".pfx"))):
        raise ValueError(f"含敏感文件名，需人工确认：{name}")
    patterns = (rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                rb"gh[pousr]_[A-Za-z0-9]{30,}", rb"AKIA[A-Z0-9]{16}")
    if any(re.search(pattern, data) for pattern in patterns):
        raise ValueError(f"疑似凭据，需人工确认：{name}")


def validate_tree(path: Path) -> None:
    skill = path / "SKILL.md"
    if not skill.is_file():
        raise ValueError("目标缺少 SKILL.md")
    content = skill.read_text(encoding="utf-8")
    front = content.split("---", 2)
    if len(front) != 3 or front[0].strip() or not re.search(r"^name:\s*\S", front[1], re.M) or not re.search(r"^description:", front[1], re.M):
        raise ValueError("SKILL.md 缺少有效的 name/description frontmatter")
    for item in path.rglob("*"):
        if ".git" in item.relative_to(path).parts:
            continue
        if item.is_symlink():
            raise ValueError(f"上游含软链接，需人工检查：{item.relative_to(path)}")
        if item.is_file():
            scan_file(str(item.relative_to(path)), item.read_bytes())
        elif not item.is_dir():
            raise ValueError("上游含非常规文件")


@contextmanager
def checkout(repo: str, revision: str | None = None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("仓库名非法")
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("必须使用完整 commit")
    with tempfile.TemporaryDirectory(prefix="skill-source-") as temp:
        dest = Path(temp) / "repo"
        git(Path(temp), "clone", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git", str(dest))
        commit = git(dest, "rev-parse", revision or "HEAD")
        git(dest, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", commit)
        yield dest, commit


def lock_record(root: Path, name: str) -> dict[str, Any] | None:
    path = root / ".skill-locks" / f"{name}.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("锁定记录不得是软链接")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("锁定记录必须是对象")
    if data.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{64}", data.get("content_hash", "")):
        raise ValueError("锁定记录损坏")
    return data


def version_state(root: Path, name: str, record: dict[str, Any] | None) -> str:
    if not record:
        return "unverified"
    for tag in git(root, "tag", "--list", f"stable/{name}/*").splitlines():
        try:
            tagged = json.loads(git(root, "show", f"{tag}:.skill-locks/{name}.json"))
            if tagged == record:
                return "stable"
        except (ValueError, subprocess.SubprocessError):
            continue
    return "candidate"


def base_revision(row: dict[str, Any], lock: dict[str, Any] | None) -> str:
    if lock and lock.get("commit"):
        return lock["commit"]
    matches = re.findall(r"(?<![0-9a-z])([0-9a-f]{7,40})(?![0-9a-z])", row.get("当前版本、commit 或锁定哈希", ""))
    if len(matches) != 1:
        raise ValueError("没有唯一可验证的上游 commit，无法自动判断")
    return matches[0]


def snapshot_at(repo_dir: Path, commit: str, subpath: str, destination: Path) -> str:
    """独立检出基线，避免比较时改变目标工作树。"""
    git(repo_dir, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", str(destination), commit)
    source = destination / subpath
    validate_tree(source)
    result = audit.folder_hash(source)
    if result is None:
        raise ValueError("无法计算内容摘要")
    return result


def prepare(root: Path, name: str) -> dict[str, Any]:
    row = row_for(root, name)
    target = checked_target(root, row)
    repo, subpath = source_for(row)
    lock = lock_record(root, name)
    baseline = base_revision(row, lock)
    local_hash = audit.folder_hash(target)
    if lock and (lock.get("repo"), lock.get("subpath")) != (repo, subpath):
        raise ValueError("清单来源与锁定记录不一致")
    with checkout(repo) as (clone, commit):
        base = git(clone, "rev-parse", "--verify", f"{baseline}^{{commit}}")
        git(clone, "merge-base", "--is-ancestor", base, commit)
        source = clone / subpath
        validate_tree(source)
        target_hash = audit.folder_hash(source)
        with tempfile.TemporaryDirectory(prefix="skill-baseline-") as temp:
            baseline_hash = snapshot_at(clone, base, subpath, Path(temp) / "baseline")
        if local_hash != baseline_hash or (lock and local_hash != lock["content_hash"]):
            raise ValueError("本地内容与登记基线不一致，必须先处理本地定制或版本漂移")
        args = ["diff", "--name-status", base, commit, "--", subpath or "."]
        changes = git(clone, *args)
        patch = git(clone, "diff", "--no-ext-diff", base, commit, "--", subpath or ".")
        patch_bytes = patch.encode("utf-8")
        result = {
            "ok": True, "name": name, "repo": repo, "subpath": subpath,
            "current_version": base, "latest_version": commit,
            "has_update": local_hash != target_hash,
            "status": "updatable" if local_hash != target_hash else "latest",
            "local_hash": local_hash, "target_hash": target_hash,
            "manifest_hash": hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest(),
            "diff_summary": {"ok": True, "base_version": base, "target_version": commit,
                             "diff_stat": changes or "该 skill 内容没有变化", "commits": [],
                             "summary_bullets": changes.splitlines(),
                             "patch": patch[:200000], "truncated": len(patch) > 200000,
                             "patch_hash": hashlib.sha256(patch_bytes).hexdigest()},
            "review_required": [str(p.relative_to(source)) for p in source.rglob("*")
                                if p.is_file() and (p.suffix in {".py", ".js", ".mjs", ".sh"}
                                                    or "hook" in p.name.lower() or os.access(p, os.X_OK))],
        }
        return result


def preflight(root: Path, require_backup: bool = False, backup_head: str | None = None) -> str:
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise ValueError("维护目录必须是管理仓库根目录")
    for path in (root / "SKILLS.md", root / ".skill-locks"):
        if path.is_symlink():
            raise ValueError("清单和锁定记录目录不得是软链接")
    if (git_dir(root) / "skill-manager-transaction").exists():
        raise ValueError("存在未完成事务，先运行 skill_ops.py recover")
    if git(root, "branch", "--show-current") != "main":
        raise ValueError("必须在 main 分支操作")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("工作区或暂存区有未提交改动，请先处理；不会自动提交或覆盖")
    report = audit.inspect()
    blockers = [f for f in report["findings"] if f["status"] in audit.PROBLEM_STATUSES and f["status"] != "metadata-drift"]
    if blockers:
        raise ValueError("审计未通过：" + ", ".join(f'{f["name"]}:{f["status"]}' for f in blockers))
    head = git(root, "rev-parse", "HEAD")
    if require_backup:
        if git(root, "remote", "get-url", "origin") != BACKUP_REMOTE:
            raise ValueError("origin 不是已登记的私有备份地址")
        remote = git(root, "ls-remote", "origin", "refs/heads/main").split()
        if not remote or remote[0] != (backup_head or head):
            raise ValueError("当前基线尚未备份到 origin/main；先检查并显式推送备份")
        if backup_head:
            git(root, "merge-base", "--is-ancestor", backup_head, head)
    return head


def rewrite_row(root: Path, name: str, changes: dict[str, str]) -> None:
    path = root / "SKILLS.md"
    row = row_for(root, name)
    for value in changes.values():
        if not isinstance(value, str) or any(c in value for c in "|\r\n"):
            raise ValueError("清单值含非法换行或竖线")
    columns = [key for key in row if not key.startswith("_")]
    row.update(changes)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[row["_line"] - 1] = "| " + " | ".join(row[c] for c in columns) + " |\n"
    atomic_write(path, "".join(lines).encode("utf-8"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".skill-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def assert_target_audit(name: str) -> None:
    errors = [f for f in audit.inspect()["findings"] if f["status"] in audit.PROBLEM_STATUSES and (f["name"] == name or f["status"] == "invalid-manifest")]
    if errors:
        raise ValueError("变更后审计未通过：" + ", ".join(f["status"] for f in errors))


def commit_paths(root: Path, paths: list[str], message: str,
                 snapshot: tuple[str, str] | None = None) -> None:
    git(root, "add", "--", *paths)
    if snapshot is not None:
        relative, expected_hash = snapshot
        target = root / relative
        # 新复制的上游文件可能命中忽略规则；仅强制暂存已经验证的目标文件。
        files = [str(p.relative_to(root)) for p in sorted(target.rglob("*"))
                 if p.is_file() and not any(part in audit.IGNORED_PARTS for part in p.relative_to(target).parts)]
        git(root, "add", "-f", "--", *files)
        staged_files = git(root, "ls-files", "-z", "--", relative).split("\0")
        with tempfile.TemporaryDirectory(prefix="skill-index-") as temp:
            git(root, "checkout-index", f"--prefix={temp}/", "--", *filter(None, staged_files))
            if audit.folder_hash(Path(temp) / relative) != expected_hash:
                raise ValueError("暂存快照与已确认的目标摘要不一致，拒绝提交")
    staged = git(root, "diff", "--cached", "--name-only").splitlines()
    if any(not any(p == allowed or p.startswith(allowed.rstrip("/") + "/") for allowed in paths) for p in staged):
        raise ValueError("暂存区存在本次操作之外的文件")
    for name in staged:
        file = root / name
        if file.is_file() and not file.is_symlink():
            scan_file(name, file.read_bytes())
    if staged:
        git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", message)


def restore_transaction(root: Path) -> None:
    transaction = git_dir(root) / "skill-manager-transaction"
    if not (transaction / "state.json").exists():
        if transaction.is_dir() and not (transaction / "old").exists():
            shutil.rmtree(transaction)
            return
        raise ValueError("事务状态缺失，无法自动恢复")
    state = json.loads((transaction / "state.json").read_text(encoding="utf-8"))
    if git(root, "rev-parse", "HEAD") != state["head"]:
        # 提交已成功，崩溃只发生在清理阶段；不得恢复旧文件覆盖成功提交。
        if git(root, "log", "-1", "--format=%s") == state["message"] and git(root, "rev-parse", "HEAD^") == state["head"]:
            shutil.rmtree(transaction)
            return
        raise ValueError("事务后 HEAD 已变化，拒绝自动恢复，请保留事务目录人工检查")
    if state.get("kind") == "agents":
        # 恢复前整批检查，避免覆盖崩溃后人工修复的链接或实体目录。
        for item in state["links"]:
            link = Path(item["path"])
            current = os.readlink(link) if link.is_symlink() else None
            if (link.exists() and not link.is_symlink()) or current not in (item["previous"], item["desired"]):
                raise ValueError(f"事务后链接已变化，保留备份供人工检查：{link}")
        for item in state["links"]:
            link = Path(item["path"])
            previous = item["previous"]
            if link.is_symlink():
                if os.readlink(link) == previous:
                    continue
                link.unlink()
            if previous is not None:
                link.symlink_to(previous)
        atomic_write(root / "SKILLS.md", (transaction / "manifest").read_bytes())
        git(root, "reset", "--quiet", state["head"], "--", "SKILLS.md")
        shutil.rmtree(transaction)
        return
    target = root / state["target"]
    backup = transaction / "old"
    if backup.exists():
        if target.exists():
            shutil.rmtree(target)
        os.replace(backup, target)
    atomic_write(root / "SKILLS.md", (transaction / "manifest").read_bytes())
    lock = root / state["lock"]
    if (transaction / "lock").exists():
        atomic_write(lock, (transaction / "lock").read_bytes())
    else:
        lock.unlink(missing_ok=True)
    git(root, "reset", "--quiet", state["head"], "--", state["target"], "SKILLS.md", state["lock"])
    shutil.rmtree(transaction)


def apply(root: Path, plan: dict[str, Any], *, _locked: bool = False, _backup_head: str | None = None) -> dict[str, Any]:
    with nullcontext() if _locked else exclusive(root):
        head = preflight(root, require_backup=True, backup_head=_backup_head)
        row = row_for(root, plan["name"])
        target = checked_target(root, row)
        if source_for(row) != (plan["repo"], plan["subpath"]):
            raise ValueError("来源已变化，请重新预览")
        if hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest() != plan["manifest_hash"] or audit.folder_hash(target) != plan["local_hash"]:
            raise ValueError("预览已过期，请重新检查")
        if not plan["has_update"] or plan["diff_summary"].get("truncated"):
            raise ValueError("无内容变化或差异过大，需人工检查")
        with checkout(plan["repo"], plan["latest_version"]) as (clone, commit):
            source = clone / plan["subpath"]
            validate_tree(source)
            if commit != plan["latest_version"] or audit.folder_hash(source) != plan["target_hash"]:
                raise ValueError("下载内容与确认的版本不一致")
            # 下载期间可能有其他工具编辑文件，再次检查，避免用旧预览覆盖新内容。
            if preflight(root, require_backup=True, backup_head=_backup_head) != head:
                raise ValueError("下载期间基线发生变化")
            transaction = git_dir(root) / "skill-manager-transaction"
            transaction.mkdir()
            relative = str(target.relative_to(root))
            lock_rel = f'.skill-locks/{plan["name"]}.json'
            lock_path = root / lock_rel
            message = f'update: {plan["name"]} {commit} (candidate)'
            state = {"head": head, "target": relative, "lock": lock_rel, "message": message}
            try:
                shutil.copyfile(root / "SKILLS.md", transaction / "manifest")
                if lock_path.exists():
                    shutil.copyfile(lock_path, transaction / "lock")
                shutil.copytree(source, transaction / "new", ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
                atomic_write(transaction / "state.json", json.dumps(state).encode())
                os.replace(target, transaction / "old")
                os.replace(transaction / "new", target)
                record = {"schema": 1, "repo": plan["repo"], "subpath": plan["subpath"],
                          "commit": commit, "content_hash": plan["target_hash"], "status": "candidate",
                          "management": "git", "installed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
                atomic_write(lock_path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())
                rewrite_row(root, plan["name"], {"当前版本、commit 或锁定哈希": f"`{commit}`",
                            "最后检查": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()})
                assert_target_audit(plan["name"])
                commit_paths(root, [relative, "SKILLS.md", lock_rel], message,
                             snapshot=(relative, plan["target_hash"]))
            except BaseException:
                if (transaction / "state.json").exists():
                    restore_transaction(root)
                else:
                    shutil.rmtree(transaction)
                raise
            shutil.rmtree(transaction)
            return {"ok": True, "message": f'{plan["name"]} 已生成独立候选提交，未推送、未标记稳定', "new_version": commit}


def apply_many(root: Path, plans: list[dict[str, Any]]) -> dict[str, Any]:
    with exclusive(root):
        baseline = preflight(root, require_backup=True)
        expected_head = baseline
        initial_hash = hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest()
        if not plans or len({p["name"] for p in plans}) != len(plans) or any(p["manifest_hash"] != initial_hash for p in plans):
            raise ValueError("批量预览不一致或已过期")
        completed = []
        for original in plans:
            try:
                if git(root, "rev-parse", "HEAD") != expected_head:
                    raise ValueError("批量执行期间出现其他提交")
                plan = dict(original)
                # 前一项仅修改其清单行；下一项使用本批已验证的新清单快照。
                plan["manifest_hash"] = hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest()
                apply(root, plan, _locked=True, _backup_head=baseline)
                expected_head = git(root, "rev-parse", "HEAD")
                completed.append(plan["name"])
            except Exception as exc:
                return {"ok": False, "error": str(exc), "completed": completed,
                        "message": "失败项已停止；已完成项保留独立候选提交"}
        return {"ok": True, "updated_skills": completed, "message": f"已生成 {len(completed)} 个独立候选提交，未推送、未标记稳定"}


def change_agents(root: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """先预检整批，之后逐 skill 提交；失败恢复本项，已完成项明确返回。"""
    with exclusive(root):
        preflight(root)
        prepared = []
        names = set()
        for item in updates:
            name = item.get("name")
            if name in names:
                raise ValueError("重复的 skill")
            names.add(name)
            row = row_for(root, name)
            target = row["_path"]
            agents = item.get("agents")
            if not isinstance(agents, list) or not agents or any(a not in audit.AGENT_NAMES for a in agents) or len(set(agents)) != len(agents):
                raise ValueError("适用 Agent 非法或为空")
            keys = {audit.AGENT_NAMES[a] for a in agents}
            common = target.parent == root
            if (common and not audit.DIRECT_AGENTS.issubset(keys)) or (not common and "grok" in keys):
                raise ValueError(f"{name} 的目录布局无法满足该启用范围；需先单独确认 scoped 迁移")
            changes = []
            for agent, link_root in audit.LINK_ROOTS.items():
                link = link_root / name
                expected = agent in keys and not (agent == "codex" and common)
                if link.is_symlink() and link.resolve(strict=False) != target.resolve(strict=False):
                    raise ValueError(f"拒绝替换或删除不同目标链接：{link}")
                if link.exists() and not link.is_symlink():
                    raise ValueError(f"拒绝替换实体目录：{link}")
                changes.append((link, expected, os.readlink(link) if link.is_symlink() else None))
            prepared.append((name, agents, target, changes))
        completed = []
        for name, agents, target, changes in prepared:
            head = git(root, "rev-parse", "HEAD")
            transaction = git_dir(root) / "skill-manager-transaction"
            transaction.mkdir()
            message = f"config: {name} applicable agents"
            state = {"kind": "agents", "head": head, "message": message,
                     "links": [{"path": str(link), "previous": previous,
                                "desired": (previous or os.path.relpath(target, link.parent)) if expected else None}
                               for link, expected, previous in changes]}
            try:
                shutil.copyfile(root / "SKILLS.md", transaction / "manifest")
                atomic_write(transaction / "state.json", json.dumps(state).encode())
                rewrite_row(root, name, {"适用 Agent": "、".join(agents)})
                for link, expected, previous in changes:
                    if expected and previous is None:
                        link.parent.mkdir(parents=True, exist_ok=True)
                        link.symlink_to(os.path.relpath(target, link.parent))
                    elif not expected and previous is not None:
                        link.unlink()
                assert_target_audit(name)
                commit_paths(root, ["SKILLS.md"], message)
                completed.append(name)
            except BaseException as exc:
                if (transaction / "state.json").exists():
                    restore_transaction(root)
                else:
                    shutil.rmtree(transaction)
                if not isinstance(exc, Exception):
                    raise
                return {"ok": False, "error": str(exc), "completed": completed}
            shutil.rmtree(transaction)
        return {"ok": True, "updated_count": len(completed), "completed": completed, "audit": audit.inspect()}


def push_backup(root: Path) -> dict[str, Any]:
    with exclusive(root):
        preflight(root)
        if git(root, "remote", "get-url", "origin") != BACKUP_REMOTE or git(root, "remote", "get-url", "--push", "--all", "origin") != BACKUP_REMOTE:
            raise ValueError("推送地址与登记的私有备份不一致")
        remote = git(root, "ls-remote", "origin", "refs/heads/main").split()
        if not remote:
            raise ValueError("无法确认远端 main")
        git(root, "merge-base", "--is-ancestor", remote[0], "HEAD")
        revisions = git(root, "rev-list", f"{remote[0]}..HEAD").splitlines()
        if len(revisions) > 100:
            raise ValueError("待推送历史超过 100 个提交，请人工审查")
        seen = set()
        for revision in revisions:
            entries = git(root, "ls-tree", "-r", "-z", revision).split("\0")
            for entry in entries:
                if not entry:
                    continue
                meta, name = entry.split("\t", 1)
                mode, kind, sha = meta.split()
                if kind != "blob" or mode == "120000" or (name, sha) in seen:
                    continue
                seen.add((name, sha))
                blob = subprocess.run(["git", "cat-file", "blob", sha], cwd=root, capture_output=True, timeout=30, check=True).stdout
                scan_file(name, blob)
        output = git(root, "-c", "push.followTags=false", "push", "origin", "HEAD:refs/heads/main")
        return {"ok": True, "output": output or "备份推送完成；不代表候选版本已稳定"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "recover"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    root = audit.CANONICAL_ROOT
    try:
        if args.command == "check":
            result = prepare(root, args.name)
        else:
            with exclusive(root):
                restore_transaction(root)
            result = {"ok": True, "message": "未完成事务已恢复或清理"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
