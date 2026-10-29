#!/usr/bin/env python3
"""本地 Skills 控制台：默认只读，显式写入模式共享事务与版本检查。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import threading
import time
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CANONICAL_ROOT = Path("/Users/tangw/.agents/skills")
MANIFEST_PATH = CANONICAL_ROOT / "SKILLS.md"
ASSETS_DIR = SKILL_DIR / "assets"

sys.path.insert(0, str(SCRIPT_DIR))
import audit_skills  # noqa: E402
import skill_ops

AGENT_DISPLAY_MAP = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "grok": "Grok",
    "antigravity": "Antigravity",
}
DISPLAY_AGENT_MAP = {v: k for k, v in AGENT_DISPLAY_MAP.items()}


def check_runtime_dependencies() -> dict[str, bool]:
    import shutil
    dependencies = ["python3", "git", "dws", "claude", "node"]
    return {dep: bool(shutil.which(dep)) for dep in dependencies}


def get_git_status() -> dict[str, Any]:
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "-b"],
            cwd=CANONICAL_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = res.stdout.strip().splitlines()
        branch_line = lines[0] if lines else "## unknown"
        is_clean = len(lines) <= 1
        ahead = "ahead" in branch_line
        behind = "behind" in branch_line
        return {
            "branch": branch_line.replace("## ", ""),
            "clean": is_clean,
            "ahead": ahead,
            "behind": behind,
            "raw": res.stdout,
        }
    except Exception as e:
        return {"branch": "error", "clean": False, "error": str(e)}


def detect_group(name: str, source: str, notes: str) -> dict[str, Any]:
    """Detect group package name, repo, category and display label."""
    if "mattpocock/skills" in source:
        return {
            "group_id": "pkg-mattpocock",
            "group_name": "Matt Pocock 工程技能套件",
            "group_type": "suite",
            "group_source": "https://github.com/mattpocock/skills",
            "github_repo": "mattpocock/skills",
            "badge": "mattpocock/skills",
            "is_remote_package": True,
        }
    if "dws" in notes.lower() or name.startswith("dingtalk-"):
        return {
            "group_id": "pkg-dws",
            "group_name": "钉钉 DWS 企业办公套件",
            "group_type": "suite",
            "group_source": "DWS CLI 内部拆分",
            "github_repo": None,
            "badge": "DingTalk DWS",
            "is_remote_package": False,
        }
    if "khazix-skills" in source:
        return {
            "group_id": "pkg-khazix",
            "group_name": "Khazix Agent 效能套件",
            "group_type": "suite",
            "group_source": "https://github.com/KKKKhazix/khazix-skills",
            "github_repo": "KKKKhazix/khazix-skills",
            "badge": "KKKKhazix",
            "is_remote_package": True,
        }
    if "anthropics/skills" in source:
        return {
            "group_id": "pkg-anthropic",
            "group_name": "Anthropic 官方技能包",
            "group_type": "suite",
            "group_source": "https://github.com/anthropics/skills",
            "github_repo": "anthropics/skills",
            "badge": "Anthropic Official",
            "is_remote_package": True,
        }
    if "ego lite" in source or name == "ego-browser":
        return {
            "group_id": "pkg-ego",
            "group_name": "ego lite 浏览器扩展",
            "group_type": "plugin",
            "group_source": "ego lite App 内置",
            "github_repo": None,
            "badge": "ego Plugin",
            "is_remote_package": False,
        }
    if "本地" in source or "本地" in notes:
        return {
            "group_id": "pkg-local",
            "group_name": "本地自研与独立维护技能",
            "group_type": "custom",
            "group_source": "本地维护",
            "github_repo": None,
            "badge": "Local Custom",
            "is_remote_package": False,
        }
    
    match = re.search(r"github\.com/([^/\s)]+/[^/\s)]+)", source)
    repo = match.group(1).removesuffix(".git") if match else None
    return {
        "group_id": f"pkg-single-{name}",
        "group_name": f"{name} (独立仓库)",
        "group_type": "standalone",
        "group_source": source,
        "github_repo": repo,
        "badge": repo or "Standalone",
        "is_remote_package": bool(repo),
    }


def parse_skills_full() -> list[dict[str, Any]]:
    """Parse SKILLS.md table along with file system details and grouping."""
    audit_data = audit_skills.inspect()
    findings_by_skill: dict[str, list[dict[str, str]]] = {}
    for f in audit_data.get("findings", []):
        name = f.get("name")
        if name:
            findings_by_skill.setdefault(name, []).append(f)

    skills: list[dict[str, Any]] = []
    for row in audit_skills.manifest_rows(MANIFEST_PATH):
        name = row["_name"]
        agents = row["适用 Agent"].split("、")
        agent_keys = [DISPLAY_AGENT_MAP[a] for a in agents]
        canonical_path = row["_path"]
        is_scoped = canonical_path.parent != CANONICAL_ROOT
        skill_md_path = canonical_path / "SKILL.md"
        skill_content = ""
        files_list = []
        if canonical_path.exists() and canonical_path.is_dir():
            try:
                for p in canonical_path.rglob("*"):
                    if p.is_file() and not any(part in audit_skills.IGNORED_PARTS for part in p.parts):
                        files_list.append(str(p.relative_to(canonical_path)))
            except Exception:
                pass

        if skill_md_path.exists():
            try:
                skill_content = skill_md_path.read_text(encoding="utf-8")
            except Exception:
                pass

        findings = findings_by_skill.get(name, [])
        is_healthy = True
        status_summary = "linked"
        for f in findings:
            if f.get("status") in audit_skills.PROBLEM_STATUSES:
                is_healthy = False
                status_summary = f.get("status", "problem")
                break

        source_raw = row.get("GitHub或官网", "")
        notes_raw = row.get("安装或更新说明", "")
        subpath_match = re.search(r"仓库子目录\s*`([^`]+)`", notes_raw)
        subpath = subpath_match.group(1) if subpath_match else ""

        grp_info = detect_group(name, source_raw, notes_raw)
        record = skill_ops.lock_record(CANONICAL_ROOT, name)

        skills.append({
            "name": name,
            "path": str(canonical_path),
            "management": "external" if canonical_path.is_symlink() else "git",
            "lock": record,
            "version_state": "external" if canonical_path.is_symlink() else skill_ops.version_state(CANONICAL_ROOT, name, record),
            "source": source_raw,
            "github_repo": grp_info.get("github_repo"),
            "subpath": subpath,
            "description": row.get("简介", ""),
            "version": row.get("当前版本、commit 或锁定哈希", "").strip("`"),
            "notes": notes_raw,
            "agents": agents,
            "agent_keys": agent_keys,
            "last_checked": row.get("最后检查", ""),
            "healthy": is_healthy,
            "is_scoped": is_scoped,
            "status": status_summary,
            "findings": findings,
            "files": sorted(files_list),
            "skill_content": skill_content,
            "group_id": grp_info["group_id"],
            "group_name": grp_info["group_name"],
            "group_type": grp_info["group_type"],
            "group_badge": grp_info["badge"],
            "group_source": grp_info["group_source"],
            "is_remote_package": grp_info["is_remote_package"],
        })

    return skills


def prefetch_all_remote_heads(unique_repos: list[str]) -> dict[str, str]:
    """多线程并发嗅探所有独立仓库的最新 HEAD，避免串行阻塞。"""
    if not unique_repos:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(unique_repos), 8)) as executor:
        future_to_repo = {executor.submit(skill_ops.probe_remote_head, r): r for r in unique_repos}
        for future in future_to_repo:
            repo = future_to_repo[future]
            try:
                results[repo] = future.result()
            except Exception:
                results[repo] = ""
    return results


def check_skill_update(skill_name: str, prepare_diff: bool = False) -> dict[str, Any]:
    try:
        row = skill_ops.row_for(CANONICAL_ROOT, skill_name)
        source_val = row.get("GitHub或官网", "")
        notes = row.get("安装或更新说明", "")
        is_local = source_val.startswith("local:") or "本地开发源:" in notes
        is_github = "github.com/" in source_val
        if not is_local and not is_github:
            return {
                "ok": True,
                "name": skill_name,
                "status": "unsupported",
                "message": "未绑定远程 GitHub 上游，当前仅在本地维护",
                "has_update": False,
                "no_remote": True
            }

        if is_local:
            return skill_ops.prepare(CANONICAL_ROOT, skill_name)

        repo, subpath = skill_ops.source_for(row)
        lock = skill_ops.lock_record(CANONICAL_ROOT, skill_name)
        baseline = skill_ops.base_revision(row, lock)

        remote_head = skill_ops.probe_remote_head(repo)
        if not remote_head:
            return {
                "ok": False,
                "name": skill_name,
                "status": "unknown",
                "error": "连接 GitHub 失败，无法获取上游版本",
                "has_update": False
            }

        manifest_hash = hashlib.sha256((CANONICAL_ROOT / "SKILLS.md").read_bytes()).hexdigest()
        local_hash = audit_skills.folder_hash(skill_ops.checked_target(CANONICAL_ROOT, row))

        is_same = remote_head.startswith(baseline) or baseline.startswith(remote_head)

        if is_same:
            return {
                "ok": True,
                "name": skill_name,
                "repo": repo,
                "subpath": subpath,
                "current_version": baseline[:8] if len(baseline) >= 40 else baseline,
                "latest_version": remote_head[:8] if len(remote_head) >= 40 else remote_head,
                "has_update": False,
                "status": "latest",
                "message": f"已是最新版本 ({remote_head[:8]})",
                "local_hash": local_hash,
                "manifest_hash": manifest_hash,
                "diff_summary": {
                    "ok": True,
                    "base_version": baseline,
                    "target_version": remote_head,
                    "diff_stat": "已是最新版本",
                    "commits": [],
                    "summary_bullets": ["当前版本与上游保持一致"],
                    "patch": "",
                    "truncated": False
                }
            }
        else:
            # 轻量检查模式：直接返回发现新版本，0毫秒完成，避免在检查更新阶段做全量克隆卡死！
            if not prepare_diff:
                return {
                    "ok": True,
                    "name": skill_name,
                    "repo": repo,
                    "subpath": subpath,
                    "current_version": baseline[:8] if len(baseline) >= 40 else baseline,
                    "latest_version": remote_head[:8] if len(remote_head) >= 40 else remote_head,
                    "has_update": True,
                    "status": "updatable",
                    "message": f"发现新版本 ({remote_head[:8]})",
                    "local_hash": local_hash,
                    "manifest_hash": manifest_hash,
                }
            # 只有在明确需要 diff / 升级时才执行 prepare
            return skill_ops.prepare(CANONICAL_ROOT, skill_name)
    except Exception as exc:
        return {"ok": False, "status": "unknown", "error": str(exc)}


def prepare_skill_update(skill_name: str) -> dict[str, Any]:
    """按需拉取补丁并准备单个技能的升级计划。"""
    return check_skill_update(skill_name, prepare_diff=True)


def check_group_update(group_id: str, prepare_diff: bool = False) -> dict[str, Any]:
    skills = [s for s in parse_skills_full() if s["group_id"] == group_id]
    if not skills:
        return {"ok": False, "error": "套件不存在"}

    results = [check_skill_update(s["name"], prepare_diff=prepare_diff) for s in skills]
    failures = [r for r in results if not r["ok"]]
    plans = [r for r in results if r.get("has_update")]
    return {"ok": not failures, "group_id": group_id, "has_update": bool(plans),
            "status": "unknown" if failures else ("updatable" if plans else "latest"),
            "error": "; ".join(r["error"] for r in failures), "plans": plans,
            "updatable_skills": [r["name"] for r in plans], "results": results,
            "diff_summary": {"ok": not failures, "commits": [],
                             "summary_bullets": [f'{r["name"]}: {r.get("latest_version", "最新")}' for r in plans],
                             "diff_stat": f"{len(plans)} 个 skill 发现新版本" if not prepare_diff else f"{len(plans)} 个 skill 内容变化"}}


def prepare_group_update(group_id: str) -> dict[str, Any]:
    """按需准备套件的全部更新计划与合并补丁。"""
    return check_group_update(group_id, prepare_diff=True)


def check_all_updates() -> dict[str, Any]:
    skills = parse_skills_full()
    unique_repos = set()
    for s in skills:
        try:
            row = skill_ops.row_for(CANONICAL_ROOT, s["name"])
            repo, _ = skill_ops.source_for(row)
            if repo and not repo.startswith("local:") and repo != "待确认":
                unique_repos.add(repo)
        except Exception:
            pass

    # 并发嗅探所有仓库 HEAD，避免串行网络等待
    prefetch_all_remote_heads(list(unique_repos))

    results = {}
    updatable = []
    for s in skills:
        r = check_skill_update(s["name"], prepare_diff=False)
        results[s["name"]] = r
        if r.get("has_update"):
            updatable.append(s["name"])

    return {
        "ok": True,
        "results": results,
        "plans": [],
        "updatable_skills": updatable,
        "updatable_count": len(updatable),
        "total_checked": len(skills)
    }


class SkillsAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        super().end_headers()

    def authorized(self, api: bool = False) -> bool:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != origin.removeprefix("http://"):
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid Host")
            return False
        if self.headers.get("Origin") not in (None, origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_error(HTTPStatus.FORBIDDEN, "Cross-origin request denied")
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Skill-Token", ""), self.server.api_token):
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid session token")
            return False
        return True

    def send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_error(HTTPStatus.FORBIDDEN, "Cross-origin requests are disabled")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        if not self.authorized(api=path.startswith("/api/")):
            return
        if path in {"/vendor/vue-3.4.21.global.prod.js", "/vendor/tailwind-3.4.17.js"}:
            content = (ASSETS_DIR / path.lstrip("/")).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/favicon.ico":
            svg_ico = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="24" fill="#5e6ad2"/><text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" font-size="56">🧩</text></svg>""".encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(svg_ico)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(svg_ico)
            return

        if path in ("/", "/index.html"):
            html_file = ASSETS_DIR / "index.html"
            if not html_file.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "index.html not found")
                return
            content = html_file.read_text(encoding="utf-8").replace(
                "__SKILL_SESSION__", json.dumps({"token": self.server.api_token, "write": self.server.allow_write})
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/skills":
            try:
                skills = parse_skills_full()
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc), "audit": audit_skills.inspect()}, HTTPStatus.CONFLICT)
                return
            audit_res = audit_skills.inspect()
            git_info = get_git_status()
            
            groups_dict = {}
            for s in skills:
                gid = s["group_id"]
                if gid not in groups_dict:
                    groups_dict[gid] = {
                        "id": gid,
                        "name": s["group_name"],
                        "type": s["group_type"],
                        "badge": s["group_badge"],
                        "source": s["group_source"],
                        "repo": s["github_repo"],
                        "is_remote_package": s["is_remote_package"],
                        "skills": [],
                    }
                groups_dict[gid]["skills"].append(s)

            self.send_json({
                "skills": skills,
                "groups": list(groups_dict.values()),
                "audit": audit_res,
                "git": git_info,
                "dependencies": check_runtime_dependencies(),
                "total": len(skills),
            })
            return

        if path == "/api/tags":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = query.get("name", [""])[0]
            if not name:
                self.send_json({"ok": False, "error": "缺少 skill 名称"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                tags_res = skill_ops.rollback_skill(CANONICAL_ROOT, name)
                self.send_json(tags_res)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/git-status":
            self.send_json(get_git_status())
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Path not found")

    def do_POST(self) -> None:
        if not self.authorized(api=True):
            return
        path = urllib.parse.urlparse(self.path).path
        read_paths = {
            "/api/check-update", "/api/check-group-update", "/api/check-all",
            "/api/prepare-update", "/api/prepare-group-update"
        }
        write_paths = {
            "/api/upgrade-skill", "/api/upgrade-group", "/api/batch-save",
            "/api/git-push", "/api/repair", "/api/tag-stable", "/api/rollback"
        }
        if path not in read_paths | write_paths:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path in write_paths and not self.server.allow_write:
            self.send_json({"ok": False, "error": "当前为只读模式；需显式使用 --allow-write 启动"}, HTTPStatus.FORBIDDEN)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 65536 or self.headers.get_content_type() != "application/json":
                raise ValueError("请求必须为不超过 64 KiB 的 JSON")
            payload = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("请求必须为 JSON 对象")
        except (ValueError, UnicodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if not self.server.operation_lock.acquire(blocking=False):
            self.send_json({"ok": False, "error": "已有检查或写操作执行中"}, HTTPStatus.CONFLICT)
            return
        try:
            if path in read_paths:
                if path == "/api/check-update":
                    result = check_skill_update(payload.get("skill_name"))
                    plans = []
                elif path == "/api/prepare-update":
                    result = prepare_skill_update(payload.get("skill_name"))
                    plans = [result] if result.get("has_update") else []
                elif path == "/api/check-group-update":
                    result = check_group_update(payload.get("group_id"))
                    plans = []
                elif path == "/api/prepare-group-update":
                    result = prepare_group_update(payload.get("group_id"))
                    plans = result.get("plans", [])
                else:
                    result = check_all_updates()
                    plans = []
                # 预览只驻留本次会话；不接受客户端自填的 commit/hash 或其他路径。
                now = time.monotonic()
                self.server.plans = {key: value for key, value in self.server.plans.items() if now - value[0] < 900}
                if result.get("ok") and plans:
                    token = secrets.token_urlsafe(24)
                    self.server.plans[token] = (now, plans)
                    result["plan_id"] = token
                self.send_json(result)
            elif path in {"/api/upgrade-skill", "/api/upgrade-group"}:
                token = payload.get("plan_id", "")
                entry = self.server.plans.pop(token, None)
                if not entry or time.monotonic() - entry[0] > 900:
                    raise ValueError("预览不存在或已过期，请重新检查")
                result = skill_ops.apply_many(CANONICAL_ROOT, entry[1])
                self.send_json(result)
            elif path == "/api/batch-save":
                updates = payload.get("updates")
                if not isinstance(updates, list) or not updates or len(updates) > 100 or any(not isinstance(u, dict) for u in updates):
                    raise ValueError("updates 必须为 1 至 100 项对象列表")
                self.send_json(skill_ops.change_agents(CANONICAL_ROOT, updates))
            elif path == "/api/repair":
                self.send_json(skill_ops.repair(CANONICAL_ROOT))
            elif path == "/api/tag-stable":
                name = payload.get("name", "")
                if not name:
                    raise ValueError("缺少 skill 名称")
                push = bool(payload.get("push", False))
                self.send_json(skill_ops.tag_stable(CANONICAL_ROOT, name, push=push))
            elif path == "/api/rollback":
                name = payload.get("name", "")
                if not name:
                    raise ValueError("缺少 skill 名称")
                tag = payload.get("tag", "")
                self.send_json(skill_ops.rollback_skill(CANONICAL_ROOT, name, tag=tag))
            else:
                self.send_json(skill_ops.push_backup(CANONICAL_ROOT))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
        finally:
            self.server.operation_lock.release()


def run_server(port: int = 8765, auto_open: bool = True, allow_write: bool = False) -> None:
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), SkillsAPIHandler)
            server.api_token = secrets.token_urlsafe(32)
            server.allow_write = allow_write
            server.operation_lock = threading.Lock()
            server.plans = {}
            port = p
            break
        except OSError:
            continue

    if server is None:
        print("Error: Could not bind to any port.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"🚀 Skills Manager Web Console running at: {url}")
    print("Press Ctrl+C to stop.")

    if auto_open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Skills Manager server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Skills Manager Local Web Server")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    parser.add_argument("--allow-write", action="store_true", help="显式开启写操作；默认只读")
    args = parser.parse_args()
    run_server(port=args.port, auto_open=not args.no_open, allow_write=args.allow_write)
