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
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Any
from zoneinfo import ZoneInfo

import audit_skills as audit

BACKUP_REMOTE = "git@github.com:TangWin/agent-skills-registry.git"


def git(root: Path | None, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root if root is not None else None,
                            capture_output=True, text=True, timeout=120, check=True)
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
    source_val = row.get("GitHub或官网", "")
    notes = row.get("安装或更新说明", "")
    if source_val.startswith("local:") or "本地开发源:" in notes:
        m = re.search(r"local:([^\s)]+)", source_val)
        if not m:
            m = re.search(r"本地开发源:\s*`?([^\s`)]+)`?", notes)
        if m:
            return f"local:{m.group(1)}", ""
    match = re.search(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", source_val)
    if not match:
        raise ValueError("来源没有明确 GitHub 仓库，无法自动判断")
    repo = match.group(1).removesuffix(".git")
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


REPO_CACHE_DIR = Path.home() / ".cache" / "skill-manager" / "repos"
REPO_CACHE_TTL = 300
HEAD_CACHE_FILE = Path.home() / ".cache" / "skill-manager" / "remote_heads.json"
_REMOTE_HEAD_CACHE: dict[str, tuple[float, str]] = {}


def _load_disk_head_cache() -> None:
    try:
        if HEAD_CACHE_FILE.exists():
            data = json.loads(HEAD_CACHE_FILE.read_text(encoding="utf-8"))
            now = time.time()
            for r, v in data.items():
                if isinstance(v, dict) and now - v.get("ts", 0) < 1800:
                    _REMOTE_HEAD_CACHE[r] = (time.monotonic() - (now - v["ts"]), v.get("sha", ""))
    except Exception:
        pass


def _save_disk_head_cache() -> None:
    try:
        HEAD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_epoch = time.time()
        now_mono = time.monotonic()
        data = {}
        for r, (ts, sha) in _REMOTE_HEAD_CACHE.items():
            if sha:
                data[r] = {"ts": now_epoch - (now_mono - ts), "sha": sha}
        HEAD_CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


_load_disk_head_cache()


def probe_remote_head(repo: str, ttl: int = 1800) -> str:
    """快速嗅探远端 HEAD SHA，带持久化缓存与失败负缓存。"""
    if BACKUP_REMOTE != "git@github.com:TangWin/agent-skills-registry.git":
        return ""
    now = time.monotonic()
    last_known_sha = ""
    if repo in _REMOTE_HEAD_CACHE:
        ts, sha = _REMOTE_HEAD_CACHE[repo]
        if sha:
            last_known_sha = sha
        limit = ttl if sha else 180
        if now - ts < limit:
            return sha
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        res = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-remote", f"https://github.com/{repo}.git", "HEAD"],
            capture_output=True, text=True, timeout=35, check=True, env=env
        )
        sha = res.stdout.strip().split()[0] if res.stdout.strip().split() else ""
        _REMOTE_HEAD_CACHE[repo] = (now, sha or last_known_sha)
        if sha:
            _save_disk_head_cache()
        return sha or last_known_sha
    except Exception:
        # 网络异常时，降级使用上次成功的已知 SHA，避免因偶发抖动导致反复阻塞
        _REMOTE_HEAD_CACHE[repo] = (now, last_known_sha)
        return last_known_sha


def ensure_repo_cache(repo: str, force_fetch: bool = False) -> Path:
    """维护持久化本地仓库缓存，避免重复网络克隆。"""
    REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = repo.replace("/", "@")
    cache_path = REPO_CACHE_DIR / slug
    stamp_file = cache_path / ".FETCH_STAMP"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    if not (cache_path / ".git").exists():
        temp_dir = cache_path.with_name(f"{slug}.tmp.{secrets.token_hex(4)}")
        try:
            subprocess.run(
                ["git", "-c", "core.quotePath=false", "clone", "--filter=blob:none", f"https://github.com/{repo}.git", str(temp_dir)],
                cwd=REPO_CACHE_DIR, capture_output=True, text=True, timeout=300, check=True, env=env
            )
            temp_dir.rename(cache_path)
            stamp_file.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    else:
        last_fetch = 0
        try:
            if stamp_file.exists():
                last_fetch = int(stamp_file.read_text(encoding="utf-8").strip())
        except Exception:
            last_fetch = 0
        now = int(time.time())
        if force_fetch or (now - last_fetch > REPO_CACHE_TTL):
            try:
                subprocess.run(
                    ["git", "-c", "core.quotePath=false", "fetch", "--filter=blob:none", "origin"],
                    cwd=cache_path, capture_output=True, text=True, timeout=60, check=True, env=env
                )
                stamp_file.write_text(str(now), encoding="utf-8")
            except Exception:
                pass
    return cache_path


@contextmanager
def checkout(repo: str, revision: str | None = None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("仓库名非法")
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("必须使用完整 commit")

    cache_repo = None
    try:
        cache_repo = ensure_repo_cache(repo)
    except Exception:
        cache_repo = None

    if cache_repo is not None:
        with tempfile.TemporaryDirectory(prefix="skill-source-") as temp:
            dest = Path(temp) / "repo"
            target_commit = revision or git(cache_repo, "rev-parse", "HEAD")
            git(cache_repo, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", str(dest), target_commit)
            try:
                yield dest, target_commit
            finally:
                try:
                    git(cache_repo, "worktree", "remove", "--force", str(dest))
                except Exception:
                    pass
    else:
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
    local_hash = audit.folder_hash(target)
    if repo.startswith("local:"):
        local_dir = Path(repo.removeprefix("local:")).expanduser().resolve()
        if not local_dir.is_dir():
            raise ValueError(f"本地开发目录不存在: {local_dir}")
        validate_tree(local_dir)
        target_hash = audit.folder_hash(local_dir)
        has_update = local_hash != target_hash
        return {
            "ok": True, "name": name, "repo": repo, "subpath": "",
            "current_version": "local", "latest_version": f"local-{datetime.now().strftime('%Y%m%d')}",
            "has_update": has_update,
            "status": "updatable" if has_update else "latest",
            "local_hash": local_hash, "target_hash": target_hash,
            "manifest_hash": hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest(),
            "diff_summary": {"ok": True, "base_version": "local", "target_version": "local-latest",
                             "diff_stat": "本地源内容存在变更" if has_update else "该 skill 内容没有变化",
                             "commits": [], "summary_bullets": ["本地工作区差异比对"],
                             "patch": "本地工作区文件同步", "truncated": False,
                             "patch_hash": ""},
            "review_required": []
        }
    baseline = base_revision(row, lock)
    if lock and (lock.get("repo"), lock.get("subpath")) != (repo, subpath):
        raise ValueError("清单来源与锁定记录不一致")
    if lock and local_hash != lock.get("content_hash"):
        raise ValueError("本地内容与登记基线不一致，必须先处理本地定制或版本漂移")

    remote_head = probe_remote_head(repo)
    if remote_head and (remote_head.startswith(baseline) or baseline.startswith(remote_head)):
        return {
            "ok": True, "name": name, "repo": repo, "subpath": subpath,
            "current_version": baseline, "latest_version": remote_head,
            "has_update": False,
            "status": "latest",
            "message": "已是最新版本",
            "local_hash": local_hash, "target_hash": local_hash,
            "manifest_hash": hashlib.sha256((root / "SKILLS.md").read_bytes()).hexdigest(),
            "diff_summary": {"ok": True, "base_version": baseline, "target_version": remote_head,
                             "diff_stat": "该 skill 已是最新版本",
                             "commits": [], "summary_bullets": ["当前版本与上游保持一致"],
                             "patch": "", "truncated": False, "patch_hash": ""},
            "review_required": []
        }

    with checkout(repo) as (clone, commit):
        base = git(clone, "rev-parse", "--verify", f"{baseline}^{{commit}}")
        git(clone, "merge-base", "--is-ancestor", base, commit)
        source = clone / subpath
        validate_tree(source)
        target_hash = audit.folder_hash(source)
        if base == commit:
            baseline_hash = target_hash
            changes = ""
            patch = ""
        else:
            with tempfile.TemporaryDirectory(prefix="skill-baseline-") as temp:
                baseline_hash = snapshot_at(clone, base, subpath, Path(temp) / "baseline")
            args = ["diff", "--name-status", base, commit, "--", subpath or "."]
            changes = git(clone, *args)
            patch = git(clone, "diff", "--no-ext-diff", base, commit, "--", subpath or ".")
        if local_hash != baseline_hash or (lock and local_hash != lock["content_hash"]):
            raise ValueError("本地内容与登记基线不一致，必须先处理本地定制或版本漂移")
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
    elif target.exists():
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
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


def push_backup(root: Path, *, _locked: bool = False) -> dict[str, Any]:
    with nullcontext() if _locked else exclusive(root):
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


def append_row(root: Path, row_data: dict[str, str]) -> None:
    path = root / "SKILLS.md"
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    insert_idx = -1
    in_table = False
    for idx, line in enumerate(lines):
        text = line.strip()
        if text == "## 已纳管":
            in_table = True
            continue
        if in_table and text.startswith("## "):
            insert_idx = idx - 1
            break
        if in_table and text.startswith("|"):
            insert_idx = idx
    if insert_idx == -1:
        raise ValueError("未找到已纳管表格插入点")
    cols = ["名称", "唯一维护目录", "GitHub或官网", "简介", "当前版本、commit 或锁定哈希", "安装或更新说明", "适用 Agent", "最后检查"]
    row_line = "| " + " | ".join(row_data.get(c, "") for c in cols) + " |\n"
    lines.insert(insert_idx + 1, row_line)
    atomic_write(path, "".join(lines).encode("utf-8"))


def repair(root: Path) -> dict[str, Any]:
    rep = audit.inspect()
    actions = audit.repair_links(rep)
    return {
        "ok": True,
        "repaired_count": len(actions),
        "actions": actions,
        "message": f"成功修复 {len(actions)} 处软链接异常" if actions else "无需要修复的软链接异常"
    }


def tag_stable(root: Path, name: str, *, push: bool = False) -> dict[str, Any]:
    with exclusive(root):
        preflight(root)
        lock = lock_record(root, name)
        row = row_for(root, name)
        version_cell = row.get("当前版本、commit 或锁定哈希", "")
        v_match = re.search(r"v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)", version_cell)
        if v_match:
            version_str = v_match.group(1)
        elif lock and lock.get("commit"):
            version_str = lock["commit"][:7]
        else:
            matches = re.findall(r"([0-9a-f]{7,40})", version_cell)
            version_str = matches[0][:7] if matches else datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")

        tag_name = f"stable/{name}/{version_str}"
        existing = git(root, "tag", "--list", tag_name)
        if existing:
            tag_commit = git(root, "rev-parse", f"{tag_name}^{{commit}}")
            head_commit = git(root, "rev-parse", "HEAD")
            if tag_commit != head_commit:
                raise ValueError(f"标签 {tag_name} 已存在且指向不同提交 {tag_commit[:7]}")
            tag_created = False
        else:
            git(root, "tag", "-a", tag_name, "-m", f"stable: {name} {version_str}")
            tag_created = True

        push_output = ""
        if push:
            push_res = push_backup(root, _locked=True)
            push_output = push_res.get("output", "")
            git(root, "push", "origin", tag_name)

        return {
            "ok": True,
            "tag": tag_name,
            "created": tag_created,
            "version": version_str,
            "message": f"已成功标记稳定标签 {tag_name}" + (" 并推送到远端备份" if push else "")
        }


def rollback_skill(root: Path, name: str, tag: str | None = None) -> dict[str, Any]:
    with exclusive(root):
        preflight(root)
        tags = [t for t in git(root, "tag", "--list", f"stable/{name}/*").splitlines() if t]
        if not tags:
            raise ValueError(f"{name} 没有可用的稳定标签 (stable/{name}/*)")
        if not tag:
            tag_list = []
            for t in sorted(tags, reverse=True):
                info = git(root, "log", "-1", "--format=%ci (%h)", t)
                tag_list.append({"tag": t, "info": info})
            return {"ok": True, "name": name, "available_tags": tag_list}

        if tag not in tags:
            raise ValueError(f"指定的标签 {tag} 不在可用稳定标签列表中")

        row = row_for(root, name)
        target = row["_path"]
        relative = str(target.relative_to(root))
        lock_rel = f".skill-locks/{name}.json"
        lock_path = root / lock_rel

        head = git(root, "rev-parse", "HEAD")
        transaction = git_dir(root) / "skill-manager-transaction"
        transaction.mkdir()
        message = f"rollback: {name} to {tag}"
        state = {"head": head, "target": relative, "lock": lock_rel, "message": message}
        try:
            shutil.copyfile(root / "SKILLS.md", transaction / "manifest")
            if lock_path.exists():
                shutil.copyfile(lock_path, transaction / "lock")
            shutil.copytree(target, transaction / "old")
            atomic_write(transaction / "state.json", json.dumps(state).encode())

            shutil.rmtree(target)
            git(root, "checkout", tag, "--", relative)

            try:
                tagged_lock = git(root, "show", f"{tag}:{lock_rel}")
                atomic_write(lock_path, tagged_lock.encode("utf-8"))
            except subprocess.SubprocessError:
                lock_path.unlink(missing_ok=True)

            v_from_tag = tag.removeprefix(f"stable/{name}/")
            rewrite_row(root, name, {
                "当前版本、commit 或锁定哈希": f"`{v_from_tag}` (回退)",
                "最后检查": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            })
            assert_target_audit(name)
            commit_paths(root, [relative, "SKILLS.md", lock_rel], message)
        except BaseException:
            if (transaction / "state.json").exists():
                restore_transaction(root)
            else:
                shutil.rmtree(transaction)
            raise
        shutil.rmtree(transaction)
        return {"ok": True, "name": name, "rolled_back_to": tag, "message": f"{name} 已成功回退到 {tag} 并独立提交"}


def _execute_install(root: Path, head: str, name: str, source_dir: Path, repo: str, subpath: str,
                     commit: str, target_hash: str, agents_in: list[str] | None, desc: str,
                     notes: str, is_git: bool) -> dict[str, Any]:
    target = root / name
    if target.exists():
        raise ValueError(f"目标目录已存在：{target}")

    applicable_agents = agents_in or ["Codex", "Claude Code", "Cursor", "Grok", "Antigravity"]
    if any(a not in audit.AGENT_NAMES for a in applicable_agents):
        raise ValueError("存在未知适用 Agent")

    transaction = git_dir(root) / "skill-manager-transaction"
    transaction.mkdir()
    lock_rel = f".skill-locks/{name}.json"
    lock_path = root / lock_rel
    message = f"install: {name} {commit[:7] if is_git else commit} (candidate)"
    state = {"head": head, "target": name, "lock": lock_rel, "message": message}

    try:
        shutil.copyfile(root / "SKILLS.md", transaction / "manifest")
        atomic_write(transaction / "state.json", json.dumps(state).encode())

        shutil.copytree(source_dir, target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))

        record = {
            "schema": 1,
            "repo": repo,
            "subpath": subpath,
            "commit": commit if is_git else "",
            "content_hash": target_hash,
            "status": "candidate",
            "management": "git" if is_git else "local",
            "installed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        }
        atomic_write(lock_path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())

        agent_keys = {audit.AGENT_NAMES[a] for a in applicable_agents}
        for agent_key, link_root in audit.LINK_ROOTS.items():
            if agent_key in agent_keys:
                link = link_root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.exists() and not link.is_symlink():
                    link.symlink_to(os.path.relpath(target, link.parent))

        if not desc:
            skill_md = (target / "SKILL.md").read_text(encoding="utf-8")
            desc_m = re.search(r"^description:\s*(.+)$", skill_md, re.M)
            desc = desc_m.group(1).strip()[:60] if desc_m else "用户级技能"

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        display_path = f"~/.agents/skills/{name}" if root == Path.home() / ".agents" / "skills" else str(target)
        row_data = {
            "名称": name,
            "唯一维护目录": f"`{display_path}`",
            "GitHub或官网": f"[{repo}]({repo})" if repo.startswith("http") else repo,
            "简介": desc,
            "当前版本、commit 或锁定哈希": f"`{commit}`",
            "安装或更新说明": notes,
            "适用 Agent": "、".join(applicable_agents),
            "最后检查": today
        }
        append_row(root, row_data)

        assert_target_audit(name)
        commit_paths(root, [name, "SKILLS.md", lock_rel], message, snapshot=(name, target_hash))
    except BaseException:
        if (transaction / "state.json").exists():
            restore_transaction(root)
        else:
            shutil.rmtree(transaction)
        raise
    shutil.rmtree(transaction)
    return {"ok": True, "name": name, "version": commit, "message": f"{name} 已成功安装并生成独立候选提交"}


def install_skill(root: Path, source: str, *, subpath: str = "", name: str | None = None,
                  agents: list[str] | None = None, desc: str = "", notes: str = "") -> dict[str, Any]:
    with exclusive(root):
        head = preflight(root, require_backup=False)
        is_local = source.startswith("local:") or (Path(source).expanduser().is_dir() if ":" not in source else False)
        if is_local:
            local_path = Path(source.removeprefix("local:")).expanduser().resolve()
            if not local_path.is_dir():
                raise ValueError(f"本地目录不存在: {local_path}")
            validate_tree(local_path)
            skill_text = (local_path / "SKILL.md").read_text(encoding="utf-8")
            front = skill_text.split("---", 2)[1]
            m_name = re.search(r"^name:\s*([a-z0-9][a-z0-9_-]*)", front, re.M)
            final_name = name or (m_name.group(1) if m_name else None)
            if not final_name:
                raise ValueError("无法解析 skill 名称")
            target_hash = audit.folder_hash(local_path)
            if not target_hash:
                raise ValueError("无法计算源目录哈希")
            commit = f"local-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')}"
            repo_display = f"local:{local_path}"
            install_note = notes or "本地工作区源安装；变更由本地同步更新"
            return _execute_install(root, head, final_name, local_path, repo_display, subpath, commit,
                                    target_hash, agents, desc, install_note, is_git=False)
        else:
            repo = source.removeprefix("https://github.com/").removesuffix(".git")
            with checkout(repo) as (clone, rev_commit):
                src_tree = clone / subpath if subpath else clone
                validate_tree(src_tree)
                skill_text = (src_tree / "SKILL.md").read_text(encoding="utf-8")
                front = skill_text.split("---", 2)[1]
                m_name = re.search(r"^name:\s*([a-z0-9][a-z0-9_-]*)", front, re.M)
                final_name = name or (m_name.group(1) if m_name else None)
                if not final_name:
                    raise ValueError("无法解析 skill 名称")
                target_hash = audit.folder_hash(src_tree)
                if not target_hash:
                    raise ValueError("无法计算源目录哈希")
                repo_url = f"https://github.com/{repo}"
                repo_note = notes or (f"仓库子目录 `{subpath}`；按子目录差异更新" if subpath else "仓库根目录 skill；按版本发布或 commit 更新")
                return _execute_install(root, head, final_name, src_tree, repo_url, subpath, rev_commit,
                                        target_hash, agents, desc, repo_note, is_git=True)


def register_skill(root: Path, name: str, target_path: str, *, source: str = "本地自建",
                   description: str = "", version: str = "0.1.0",
                   agents: list[str] | None = None, notes: str = "", external: bool = False) -> dict[str, Any]:
    with exclusive(root):
        head = preflight(root, require_backup=False)
        target = root / name
        p = Path(target_path).expanduser()

        applicable_agents = agents or ["Codex", "Claude Code", "Cursor", "Grok", "Antigravity"]
        if any(a not in audit.AGENT_NAMES for a in applicable_agents):
            raise ValueError("存在未知适用 Agent")

        if external:
            if not p.exists():
                raise ValueError(f"外部目标路径不存在: {p}")
            if target.exists() and not target.is_symlink():
                raise ValueError(f"目标目录已存在实体，非软链接: {target}")
            if not target.is_symlink():
                target.symlink_to(p)
            management_type = "external"
        else:
            if not target.is_dir():
                raise ValueError(f"受管目录不存在: {target}")
            validate_tree(target)
            management_type = "local"

        agent_keys = {audit.AGENT_NAMES[a] for a in applicable_agents}
        for agent_key, link_root in audit.LINK_ROOTS.items():
            if agent_key in agent_keys:
                link = link_root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.exists() and not link.is_symlink():
                    link.symlink_to(os.path.relpath(target, link.parent))

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        display_path = f"~/.agents/skills/{name}" if root == Path.home() / ".agents" / "skills" else str(target)
        row_data = {
            "名称": name,
            "唯一维护目录": f"`{display_path}`",
            "GitHub或官网": source,
            "简介": description or "用户级技能",
            "当前版本、commit 或锁定哈希": f"`{version}`",
            "安装或更新说明": notes or ("外部自管应用安装，不在此处自动更新" if external else "本地维护"),
            "适用 Agent": "、".join(applicable_agents),
            "最后检查": f"{today}（{'外部自管' if external else '仅本地安装校验'}）"
        }
        append_row(root, row_data)
        assert_target_audit(name)
        commit_message = f"register: {name} {version} ({management_type})"
        commit_paths(root, [name, "SKILLS.md"], commit_message)
        return {"ok": True, "name": name, "message": f"{name} 已成功登记并独立提交"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # 兼容位置参数调用 skill_ops.py check <name> 或 skill_ops.py recover
    parser.add_argument("command", choices=["check", "recover", "repair", "tag-stable", "rollback", "install", "register"])
    parser.add_argument("arg1", nargs="?", help="skill 名称、仓库地址或来源")
    parser.add_argument("arg2", nargs="?", help="标签名或目标路径")
    parser.add_argument("--subpath", default="", help="仓库子目录")
    parser.add_argument("--name", help="自定义 skill 名称")
    parser.add_argument("--agents", help="适用 Agent，顿号分隔")
    parser.add_argument("--desc", default="", help="技能简述")
    parser.add_argument("--notes", default="", help="安装或更新说明")
    parser.add_argument("--version", default="0.1.0", help="版本号")
    parser.add_argument("--source", default="本地自建", help="来源说明")
    parser.add_argument("--external", action="store_true", help="是否为外部自管应用软链接")
    parser.add_argument("--push", action="store_true", help="打标后同时推送到远端备份")

    args = parser.parse_args()
    root = audit.CANONICAL_ROOT
    try:
        cmd = args.command
        if cmd == "check":
            if not args.arg1:
                raise ValueError("check 命令必须提供 skill 名称")
            result = prepare(root, args.arg1)
        elif cmd == "recover":
            with exclusive(root):
                restore_transaction(root)
            result = {"ok": True, "message": "未完成事务已恢复或清理"}
        elif cmd == "repair":
            result = repair(root)
        elif cmd == "tag-stable":
            if not args.arg1:
                raise ValueError("tag-stable 命令必须提供 skill 名称")
            result = tag_stable(root, args.arg1, push=args.push)
        elif cmd == "rollback":
            if not args.arg1:
                raise ValueError("rollback 命令必须提供 skill 名称")
            result = rollback_skill(root, args.arg1, tag=args.arg2)
        elif cmd == "install":
            if not args.arg1:
                raise ValueError("install 命令必须提供仓库名或本地路径")
            agents = args.agents.split("、") if args.agents else None
            result = install_skill(root, args.arg1, subpath=args.subpath, name=args.name,
                                   agents=agents, desc=args.desc, notes=args.notes)
        elif cmd == "register":
            if not args.arg1 or not args.arg2:
                raise ValueError("register 命令必须提供 skill 名称和目标路径")
            agents = args.agents.split("、") if args.agents else None
            result = register_skill(root, args.arg1, args.arg2, source=args.source,
                                    description=args.desc, version=args.version,
                                    agents=agents, notes=args.notes, external=args.external)
        else:
            raise ValueError(f"未知命令: {cmd}")

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
