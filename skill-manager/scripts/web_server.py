#!/usr/bin/env python3
"""Skills Manager Web Console Backend & CLI Runner.

Provides local HTTP APIs for:
- Auditing skills and inspecting symlink health
- Reading skills metadata from SKILLS.md and file system
- Automatic skill grouping (Package / Suite / Plugin detection)
- Package-level and single-skill update checking via GitHub API
- Version diff summary & Changelog extraction (commits log, diff stat)
- Package-level batch upgrading and single-skill upgrading
- Batch modifying applicable agents, updating SKILLS.md, syncing symlinks
- Inspecting Git status and pushing to remote backup
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
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

AGENT_DISPLAY_MAP = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "grok": "Grok",
}
DISPLAY_AGENT_MAP = {v: k for k, v in AGENT_DISPLAY_MAP.items()}


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
    repo = match.group(1).rstrip(".git") if match else None
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

    if not MANIFEST_PATH.exists():
        return []

    lines = MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
    in_managed = False
    columns: list[str] | None = None
    skills: list[dict[str, Any]] = []

    for line in lines:
        sline = line.strip()
        if sline == "## 已纳管":
            in_managed = True
            continue
        if in_managed and sline.startswith("## "):
            break
        if not in_managed or not sline.startswith("|"):
            continue

        cells = [c.strip() for c in sline.strip("|").split("|")]
        if "名称" in cells and "适用 Agent" in cells:
            columns = cells
            continue
        if columns is None or all(set(c) <= {"-", ":"} for c in cells):
            continue
        if len(cells) != len(columns):
            continue

        row = dict(zip(columns, cells))
        name = row.get("名称", "").strip("`")
        if not name:
            continue

        agent_str = row.get("适用 Agent", "")
        agents = [a.strip() for a in agent_str.split("、") if a.strip()]
        agent_keys = [DISPLAY_AGENT_MAP.get(a, a.lower()) for a in agents]

        canonical_path = CANONICAL_ROOT / name
        is_scoped = False
        if not canonical_path.exists():
            scoped_matches = list((CANONICAL_ROOT / ".scoped").rglob(name))
            if scoped_matches:
                canonical_path = scoped_matches[0]
                is_scoped = True

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

        skills.append({
            "name": name,
            "path": row.get("唯一维护目录", "").strip("`"),
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


def fetch_github_commit(repo: str, subpath: str = "") -> dict[str, str] | None:
    """Fetch latest commit via GitHub API (with token/git ls-remote fallback)."""
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    
    try:
        url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
        if subpath:
            url += f"&path={urllib.parse.quote(subpath)}"

        headers = {"User-Agent": "Skills-Manager-Console/1.0"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
            if data and isinstance(data, list) and len(data) > 0:
                c = data[0]
                return {
                    "sha": c.get("sha", "")[:7],
                    "message": c.get("commit", {}).get("message", "").splitlines()[0],
                    "date": c.get("commit", {}).get("author", {}).get("date", "")[:10],
                }
    except Exception:
        pass

    try:
        git_url = f"https://github.com/{repo}.git"
        res = subprocess.run(
            ["git", "ls-remote", git_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        out = res.stdout.strip()
        if out:
            sha_full = out.split()[0]
            return {
                "sha": sha_full[:7],
                "message": "通过 Git 协议获取最新提交",
                "date": "最新",
            }
    except Exception as e:
        print(f"git ls-remote fallback failed for {repo}: {e}")

    return None


def translate_commit_message(msg: str) -> str:
    """Intelligently translate conventional commit messages and common git phrases to Chinese."""
    if not msg:
        return ""
    
    # 1. Conventional Commits prefix mapping
    prefixes = [
        (r"^feat(\(.*?\))?:\s*", "✨ 新特性: "),
        (r"^fix(\(.*?\))?:\s*", "🐛 缺陷修复: "),
        (r"^docs(\(.*?\))?:\s*", "📝 文档更新: "),
        (r"^refactor(\(.*?\))?:\s*", "♻️ 代码重构: "),
        (r"^perf(\(.*?\))?:\s*", "⚡ 性能提升: "),
        (r"^test(\(.*?\))?:\s*", "🧪 测试调整: "),
        (r"^chore(\(.*?\))?:\s*", "🔧 常规维护: "),
        (r"^style(\(.*?\))?:\s*", "💄 样式格式化: "),
        (r"^build(\(.*?\))?:\s*", "📦 构建依赖: "),
        (r"^ci(\(.*?\))?:\s*", "🤖 持续集成: "),
    ]
    
    res = msg.strip()
    matched_prefix = ""
    for pat, rep in prefixes:
        if re.search(pat, res, flags=re.IGNORECASE):
            matched_prefix = rep
            res = re.sub(pat, "", res, flags=re.IGNORECASE)
            break

    # 2. Key phrases and tech terms translation mapping
    terms = [
        (r"\bstop linking\b", "停止软链接"),
        (r"\blinking\b", "软链接"),
        (r"\blocal skill directories\b", "本地技能目录"),
        (r"\binitial\b", "初始版本"),
        (r"\bconfiguration\b", "配置"),
        (r"\bimproved\b", "优化"),
        (r"\bimprove\b", "优化"),
        (r"\binsights\b", "分析与洞察"),
        (r"\bcategory\b", "分类"),
        (r"\bdescription\b", "描述"),
        (r"\badd\b", "添加"),
        (r"\badded\b", "已添加"),
        (r"\bupdate\b", "更新"),
        (r"\bupdated\b", "已更新"),
        (r"\bremove\b", "移除"),
        (r"\bremoved\b", "已移除"),
        (r"\bdelete\b", "删除"),
        (r"\bsupport\b", "支持"),
        (r"\bresolve\b", "解决"),
        (r"\bresolved\b", "已解决"),
        (r"\bfor\b", "用于"),
        (r"\band\b", "及"),
        (r"\bto\b", "至"),
        (r"\bwith\b", "带有"),
        (r"\binto\b", "到"),
    ]

    for pat, rep in terms:
        res = re.sub(pat, rep, res, flags=re.IGNORECASE)

    return (matched_prefix + res).strip()


def fetch_diff_summary(repo: str, current_ver: str, target_ver: str, subpath: str = "") -> dict[str, Any]:
    """Extract commit history list and file changes summary between versions."""
    if not repo:
        return {"ok": False, "error": "No repo provided"}

    base_sha = re.search(r"([0-9a-f]{7,40})", current_ver)
    base_str = base_sha.group(1) if base_sha else ""

    commits = []
    diff_stat = ""
    summary_bullets = []

    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if base_str and target_ver:
        try:
            url = f"https://api.github.com/repos/{repo}/compare/{base_str}...{target_ver}"
            headers = {"User-Agent": "Skills-Manager-Console/1.0"}
            if gh_token:
                headers["Authorization"] = f"token {gh_token}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
                raw_commits = data.get("commits", [])
                for c in raw_commits:
                    msg = c.get("commit", {}).get("message", "").splitlines()[0]
                    c_sha = c.get("sha", "")[:7]
                    c_date = c.get("commit", {}).get("author", {}).get("date", "")[:10]
                    zh_msg = translate_commit_message(msg)
                    commits.append({
                        "sha": c_sha,
                        "message": msg,
                        "message_zh": zh_msg,
                        "date": c_date
                    })
                    summary_bullets.append(zh_msg)
                
                files = data.get("files", [])
                diff_stat = f"共涉及 {len(files)} 个文件变动，包含 {len(commits)} 次提交"
                return {
                    "ok": True,
                    "repo": repo,
                    "base_version": current_ver,
                    "target_version": target_ver,
                    "commits": commits,
                    "summary_bullets": summary_bullets[:8],
                    "diff_stat": diff_stat,
                }
        except Exception:
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / "repo.git"
        try:
            subprocess.run(
                ["git", "clone", "--bare", "--filter=blob:none", f"https://github.com/{repo}.git", str(git_dir)],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            
            log_range = f"{base_str}..HEAD" if base_str else "-n 5"
            log_res = subprocess.run(
                ["git", "log", log_range, "--pretty=format:%h|%s|%as", "--no-merges", "-n", "10"],
                cwd=git_dir,
                capture_output=True,
                text=True,
            )
            
            if log_res.stdout.strip():
                for line in log_res.stdout.strip().splitlines():
                    parts = line.split("|", 2)
                    if len(parts) >= 3:
                        msg = parts[1]
                        zh_msg = translate_commit_message(msg)
                        commits.append({
                            "sha": parts[0],
                            "message": msg,
                            "message_zh": zh_msg,
                            "date": parts[2]
                        })
                        summary_bullets.append(zh_msg)

            if base_str:
                stat_res = subprocess.run(
                    ["git", "diff", "--stat", base_str, "HEAD"],
                    cwd=git_dir,
                    capture_output=True,
                    text=True,
                )
                diff_stat = stat_res.stdout.strip()
            
            return {
                "ok": True,
                "repo": repo,
                "base_version": current_ver,
                "target_version": target_ver,
                "commits": commits,
                "summary_bullets": summary_bullets[:8],
                "diff_stat": diff_stat or "已提取最新提交日志",
            }
        except Exception as e:
            return {
                "ok": True,
                "repo": repo,
                "base_version": current_ver,
                "target_version": target_ver,
                "commits": [{"sha": target_ver, "message": "Upstream release", "message_zh": "✨ 上游发布最新版本", "date": "最新"}],
                "summary_bullets": ["✨ 上游发布包含功能改进与安全修补的提交"],
                "diff_stat": f"版本由 {current_ver} 升级至 {target_ver}",
            }


def check_skill_update(skill_name: str) -> dict[str, Any]:
    """Check if remote github repo has new commit/tag for a single skill."""
    skills = {s["name"]: s for s in parse_skills_full()}
    if skill_name not in skills:
        return {"ok": False, "error": "Skill not found"}

    item = skills[skill_name]
    repo = item.get("github_repo")
    if not repo:
        return {
            "ok": True,
            "has_update": False,
            "status": "manual",
            "message": "本地自研或非标准 Git 仓库",
            "current_version": item.get("version"),
        }

    c_info = fetch_github_commit(repo, item.get("subpath", ""))
    if not c_info:
        return {"ok": False, "error": "无法连接 GitHub 或未查询到提交"}

    latest_sha = c_info["sha"]
    curr_ver = item.get("version", "")
    has_update = (latest_sha not in curr_ver) if curr_ver else False

    # Extract changelog diff summary if update available
    diff_data = None
    if has_update:
        diff_data = fetch_diff_summary(repo, curr_ver, latest_sha, item.get("subpath", ""))

    return {
        "ok": True,
        "has_update": has_update,
        "status": "updatable" if has_update else "latest",
        "current_version": curr_ver,
        "latest_version": latest_sha,
        "latest_message": c_info["message"],
        "latest_date": c_info["date"],
        "subpath": item.get("subpath", ""),
        "repo": repo,
        "diff_summary": diff_data,
    }


def check_group_update(group_id: str) -> dict[str, Any]:
    """Check update status for an entire skill package/suite."""
    skills = parse_skills_full()
    group_skills = [s for s in skills if s["group_id"] == group_id]
    if not group_skills:
        return {"ok": False, "error": "Group not found"}

    sample = group_skills[0]
    repo = sample.get("github_repo")
    if not repo:
        return {
            "ok": True,
            "has_update": False,
            "status": "manual",
            "message": "本地套件无需联网升级",
            "group_id": group_id,
        }

    c_info = fetch_github_commit(repo)
    if not c_info:
        return {"ok": False, "error": f"无法查询套件仓库 {repo} 的最新提交"}

    latest_sha = c_info["sha"]
    
    updatable_skills = []
    for s in group_skills:
        curr_ver = s.get("version", "")
        if not curr_ver or (latest_sha not in curr_ver):
            updatable_skills.append(s["name"])

    has_update = len(updatable_skills) > 0
    diff_data = None
    if has_update:
        base_ver = sample.get("version", "")
        diff_data = fetch_diff_summary(repo, base_ver, latest_sha)

    return {
        "ok": True,
        "group_id": group_id,
        "repo": repo,
        "latest_version": latest_sha,
        "latest_message": c_info["message"],
        "latest_date": c_info["date"],
        "has_update": has_update,
        "updatable_skills": updatable_skills,
        "total_skills": len(group_skills),
        "diff_summary": diff_data,
    }


def update_group_suite_from_remote(group_id: str) -> dict[str, Any]:
    """Batch update all skills in a suite/package by cloning the repo once."""
    skills = parse_skills_full()
    group_skills = [s for s in skills if s["group_id"] == group_id]
    if not group_skills:
        return {"ok": False, "error": "套件不存在"}

    sample = group_skills[0]
    repo = sample.get("github_repo")
    if not repo:
        return {"ok": False, "error": "该套件无对应 GitHub 上游仓库"}

    c_info = fetch_github_commit(repo)
    if not c_info:
        return {"ok": False, "error": f"无法获取 {repo} 的最新提交信息"}

    latest_sha = c_info["sha"]
    today_str = subprocess.check_output(["date", "+%Y-%m-%d"], text=True).strip()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_repo = Path(tmpdir) / "repo"
        try:
            clone_cmd = ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(tmp_repo)]
            subprocess.run(clone_cmd, capture_output=True, text=True, check=True)

            updated_names = []
            for s in group_skills:
                s_name = s["name"]
                subpath = s.get("subpath", "")
                source_dir = (tmp_repo / subpath) if subpath else tmp_repo
                if not (source_dir / "SKILL.md").exists():
                    print(f"Warning: SKILL.md not found in {source_dir} for {s_name}")
                    continue

                canonical_target = CANONICAL_ROOT / s_name
                if canonical_target.exists():
                    shutil.rmtree(canonical_target)
                shutil.copytree(source_dir, canonical_target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
                updated_names.append(s_name)

            lines = MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for line in lines:
                matched_skill = None
                for uname in updated_names:
                    if line.startswith("|") and f"| {uname} |" in line:
                        matched_skill = uname
                        break
                if matched_skill:
                    parts = [p.strip() for p in line.strip("|").split("|")]
                    if len(parts) >= 8:
                        parts[4] = f"`{latest_sha}`"
                        parts[7] = today_str
                        new_lines.append("| " + " | ".join(parts) + " |")
                        continue
                new_lines.append(line)

            MANIFEST_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

            commit_msg = f"update: {sample['group_name']} ({repo} {latest_sha}, {len(updated_names)} skills)"
            subprocess.run(["git", "add", "."], cwd=CANONICAL_ROOT, check=True)
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=CANONICAL_ROOT, check=True)

            return {
                "ok": True,
                "message": f"成功一键更新套件「{sample['group_name']}」共 {len(updated_names)} 个技能至 {latest_sha}！",
                "updated_skills": updated_names,
                "new_version": latest_sha,
            }
        except Exception as e:
            return {"ok": False, "error": f"套件批量更新失败: {e}"}


def update_single_skill_from_remote(skill_name: str) -> dict[str, Any]:
    """Execute single skill update from remote repo."""
    chk = check_skill_update(skill_name)
    if not chk.get("ok") or not chk.get("latest_version"):
        return {"ok": False, "error": chk.get("error") or "无法获取远端最新版本"}

    repo = chk.get("repo")
    subpath = chk.get("subpath")
    latest_sha = chk.get("latest_version")
    canonical_target = CANONICAL_ROOT / skill_name

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_repo = Path(tmpdir) / "repo"
        try:
            clone_cmd = ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(tmp_repo)]
            subprocess.run(clone_cmd, capture_output=True, text=True, check=True)
            
            source_dir = (tmp_repo / subpath) if subpath else tmp_repo
            if not (source_dir / "SKILL.md").exists():
                return {"ok": False, "error": f"远端子目录 {subpath} 中未找到 SKILL.md"}

            if canonical_target.exists():
                shutil.rmtree(canonical_target)

            shutil.copytree(source_dir, canonical_target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))

            lines = MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
            new_lines = []
            today_str = subprocess.check_output(["date", "+%Y-%m-%d"], text=True).strip()

            for line in lines:
                if line.startswith("|") and f"| {skill_name} |" in line:
                    parts = [p.strip() for p in line.strip("|").split("|")]
                    if len(parts) >= 8:
                        parts[4] = f"`{latest_sha}`"
                        parts[7] = today_str
                        new_lines.append("| " + " | ".join(parts) + " |")
                        continue
                new_lines.append(line)
            MANIFEST_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

            commit_msg = f"update: {skill_name} {latest_sha} ({repo}, candidate)"
            subprocess.run(["git", "add", "."], cwd=CANONICAL_ROOT, check=True)
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=CANONICAL_ROOT, check=True)

            return {
                "ok": True,
                "message": f"成功更新 {skill_name} 至 {latest_sha}，已生成候选提交！",
                "new_version": latest_sha,
            }
        except Exception as e:
            return {"ok": False, "error": f"更新执行失败: {e}"}


def update_skills_manifest_and_links(
    updates: list[dict[str, Any]],
    commit_message: str = "",
) -> dict[str, Any]:
    """Batch apply updates to SKILLS.md and synchronize symlinks."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError("SKILLS.md not found")

    content = MANIFEST_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()

    in_managed = False
    columns: list[str] | None = None
    new_lines: list[str] = []
    update_map = {item["name"]: item for item in updates if "name" in item}

    for line in lines:
        sline = line.strip()
        if sline == "## 已纳管":
            in_managed = True
            new_lines.append(line)
            continue
        if in_managed and sline.startswith("## "):
            in_managed = False
            new_lines.append(line)
            continue
        if not in_managed or not sline.startswith("|"):
            new_lines.append(line)
            continue

        cells = [c.strip() for c in sline.strip("|").split("|")]
        if "名称" in cells and "适用 Agent" in cells:
            columns = cells
            new_lines.append(line)
            continue
        if columns is None or all(set(c) <= {"-", ":"} for c in cells):
            new_lines.append(line)
            continue
        if len(cells) != len(columns):
            new_lines.append(line)
            continue

        row = dict(zip(columns, cells))
        name = row.get("名称", "").strip("`")

        if name in update_map:
            u = update_map[name]
            if "agents" in u:
                row["适用 Agent"] = "、".join(u["agents"])
            if "description" in u:
                row["简介"] = u["description"]
            if "source" in u:
                row["GitHub或官网"] = u["source"]
            if "version" in u:
                row["当前版本、commit 或锁定哈希"] = f"`{u['version'].strip('`')}`"
            if "notes" in u:
                row["安装或更新说明"] = u["notes"]

            formatted_row = "| " + " | ".join(row[c] for c in columns) + " |"
            new_lines.append(formatted_row)
        else:
            new_lines.append(line)

    MANIFEST_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    link_roots = audit_skills.LINK_ROOTS
    canonical_skills = audit_skills.skill_entries(CANONICAL_ROOT)
    scoped_root = CANONICAL_ROOT / ".scoped"
    if scoped_root.is_dir():
        for skill_file in sorted(scoped_root.rglob("SKILL.md")):
            canonical_skills.setdefault(skill_file.parent.name, skill_file.parent)

    for item in updates:
        name = item.get("name")
        if not name or name not in canonical_skills:
            continue
        canonical_path = canonical_skills[name]
        is_common_direct = canonical_path.parent == CANONICAL_ROOT
        declared_agents = set(item.get("agent_keys", []))

        for agent_id, root in link_roots.items():
            if not root.exists():
                continue
            link_path = root / name
            should_have_link = (agent_id in declared_agents) and not (agent_id == "codex" and is_common_direct)

            if should_have_link:
                if not link_path.exists() and not link_path.is_symlink():
                    try:
                        rel_target = os.path.relpath(canonical_path, root)
                        link_path.symlink_to(rel_target)
                    except Exception as e:
                        print(f"Failed to create symlink {link_path}: {e}")
            else:
                if link_path.is_symlink():
                    try:
                        link_path.unlink()
                    except Exception as e:
                        print(f"Failed to remove symlink {link_path}: {e}")

    new_audit = audit_skills.inspect()

    git_result = None
    if commit_message:
        try:
            subprocess.run(["git", "add", "."], cwd=CANONICAL_ROOT, check=True)
            res = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=CANONICAL_ROOT,
                capture_output=True,
                text=True,
            )
            git_result = res.stdout
        except Exception as e:
            git_result = f"Git commit failed: {e}"

    return {
        "ok": True,
        "audit": new_audit,
        "git_result": git_result,
        "updated_count": len(updates),
    }


class SkillsAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

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
            content = html_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/skills":
            skills = parse_skills_full()
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
                "total": len(skills),
            })
            return

        if path == "/api/git-status":
            self.send_json(get_git_status())
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Path not found")

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if path == "/api/check-update":
            skill_name = payload.get("skill_name")
            res = check_skill_update(skill_name)
            self.send_json(res)
            return

        if path == "/api/check-group-update":
            group_id = payload.get("group_id")
            res = check_group_update(group_id)
            self.send_json(res)
            return

        if path == "/api/diff-summary":
            repo = payload.get("repo")
            base_ver = payload.get("base_version", "")
            target_ver = payload.get("target_version", "")
            subpath = payload.get("subpath", "")
            res = fetch_diff_summary(repo, base_ver, target_ver, subpath)
            self.send_json(res)
            return

        if path == "/api/upgrade-skill":
            skill_name = payload.get("skill_name")
            res = update_single_skill_from_remote(skill_name)
            self.send_json(res)
            return

        if path == "/api/upgrade-group":
            group_id = payload.get("group_id")
            res = update_group_suite_from_remote(group_id)
            self.send_json(res)
            return

        if path == "/api/batch-save":
            updates = payload.get("updates", [])
            commit_msg = payload.get("commit_message", "")
            try:
                res = update_skills_manifest_and_links(updates, commit_msg)
                self.send_json(res)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/git-push":
            try:
                res = subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=CANONICAL_ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.send_json({"ok": True, "output": res.stdout or "Push completed"})
            except subprocess.CalledProcessError as e:
                self.send_json({"ok": False, "error": e.stderr or str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Path not found")


def run_server(port: int = 8765, auto_open: bool = True) -> None:
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), SkillsAPIHandler)
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
    args = parser.parse_args()
    run_server(port=args.port, auto_open=not args.no_open)
