#!/usr/bin/env python3
"""只读审计用户级 Agent skills 的登记、适用范围和加载路径。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


CANONICAL_ROOT = Path("/Users/tangw/.agents/skills")
MANIFEST_PATH = CANONICAL_ROOT / "SKILLS.md"
LINK_ROOTS = {
    "codex": Path("/Users/tangw/.codex/skills"),
    "claude-code": Path("/Users/tangw/.claude/skills"),
    "cursor": Path("/Users/tangw/.cursor/skills"),
}
LEGACY_ROOTS = {
    "cc-switch": Path("/Users/tangw/.cc-switch/skills"),
}
DIRECT_AGENTS = {"codex", "grok"}
AGENT_NAMES = {
    "Codex": "codex",
    "Claude Code": "claude-code",
    "Cursor": "cursor",
    "Grok": "grok",
}
IGNORED_NAMES = {".system", "skills-cursor"}
IGNORED_PARTS = {".git", "__pycache__", ".DS_Store"}
PROBLEM_STATUSES = {
    "broken-link",
    "invalid-manifest",
    "invalid-skill",
    "duplicate-name",
    "content-drift",
    "metadata-drift",
    "conflict",
    "duplicate-identical",
    "legacy-cc-switch",
    "missing-link",
    "registered-missing",
    "unmanaged",
    "unregistered-canonical",
    "unexpected-link",
    "unsupported-scope",
    "wrong-target",
}


def skill_entries(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.name in IGNORED_NAMES:
            continue
        if path.is_symlink() or path.is_dir():
            result[path.name] = path
    return result


def manifest_rows(path: Path) -> list[dict[str, Any]]:
    """严格读取人工清单；错误必须带行号，不能静默丢失登记。"""
    rows: list[dict[str, Any]] = []
    columns = None
    active = False
    seen = set()
    paths = set()
    required = {"名称", "唯一维护目录", "适用 Agent"}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if text == "## 已纳管":
            if active or columns is not None:
                raise ValueError(f"第 {number} 行：重复的已纳管章节")
            active = True
            continue
        if active and text.startswith("## "):
            active = False
        if not active or not text.startswith("|"):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if columns is None:
            if not required.issubset(cells) or len(cells) != len(set(cells)):
                raise ValueError(f"第 {number} 行：缺失或重复的清单列")
            columns = cells
            continue
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue
        if len(cells) != len(columns):
            raise ValueError(f"第 {number} 行：列数不一致，请勿在单元格中使用竖线")
        row = dict(zip(columns, cells))
        name = row["名称"].strip("`")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) or name in seen:
            raise ValueError(f"第 {number} 行：名称非法或重复：{name}")
        agents = row["适用 Agent"].split("、")
        if any(a not in AGENT_NAMES for a in agents) or len(agents) != len(set(agents)):
            raise ValueError(f"第 {number} 行：适用 Agent 未知、重复或为空")
        canonical = Path(row["唯一维护目录"].strip("`")).expanduser()
        try:
            relative = canonical.relative_to(CANONICAL_ROOT)
        except ValueError:
            raise ValueError(f"第 {number} 行：维护目录不在受管根目录内") from None
        parts = relative.parts
        if ".." in parts or canonical.name != name or not (
            len(parts) == 1 or (len(parts) == 3 and parts[0] == ".scoped")
        ):
            raise ValueError(f"第 {number} 行：维护目录与名称或 scoped 布局不一致")
        if str(canonical) in paths:
            raise ValueError(f"第 {number} 行：维护目录重复")
        seen.add(name)
        paths.add(str(canonical))
        rows.append({**row, "_name": name, "_path": canonical, "_line": number})
    if columns is None or not rows:
        raise ValueError("清单缺少有效的已纳管表或登记行")
    return rows


def parse_manifest(path: Path) -> dict[str, dict[str, Any]]:
    return {
        row["_name"]: {
            "agents": {AGENT_NAMES[a] for a in row["适用 Agent"].split("、")},
            "path": row["_path"], "row": row,
        }
        for row in manifest_rows(path)
    }


def folder_hash(path: Path) -> str | None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not (resolved / "SKILL.md").is_file():
        return None

    digest = hashlib.sha256()
    for file_path in sorted(resolved.rglob("*")):
        if not file_path.is_file() or any(part in IGNORED_PARTS for part in file_path.parts):
            continue
        relative = file_path.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"x" if file_path.stat().st_mode & 0o111 else b"-")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def finding(agent: str, name: str, status: str, path: Path, target: str = "") -> dict[str, str]:
    return {
        "agent": agent,
        "name": name,
        "status": status,
        "path": str(path),
        "target": target,
    }


def inspect() -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    try:
        registered = parse_manifest(MANIFEST_PATH)
    except (ValueError, OSError) as exc:
        registered = {}
        findings.append(finding("registry", "SKILLS.md", "invalid-manifest", MANIFEST_PATH, str(exc)))
    canonical = skill_entries(CANONICAL_ROOT)
    scoped_root = CANONICAL_ROOT / ".scoped"
    if scoped_root.is_dir():
        for scope in sorted(scoped_root.iterdir()):
            for name, path in skill_entries(scope).items():
                if name in canonical:
                    findings.append(finding("registry", name, "duplicate-name", path))
                else:
                    canonical[name] = path
    canonical_hashes = {name: folder_hash(path) for name, path in canonical.items()}

    for name, path in canonical.items():
        if name not in registered:
            findings.append(finding("registry", name, "unregistered-canonical", path))
            continue
        if canonical_hashes[name] is None:
            findings.append(finding("registry", name, "invalid-skill", path))
        declared_path = registered[name]["path"]
        lock_path = CANONICAL_ROOT / ".skill-locks" / f"{name}.json"
        if lock_path.exists():
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
                if lock["content_hash"] != canonical_hashes[name]:
                    findings.append(finding("registry", name, "content-drift", path))
            except (ValueError, KeyError, TypeError):
                findings.append(finding("registry", name, "invalid-manifest", lock_path))
        if path.is_symlink():
            version = registered[name]["row"].get("当前版本、commit 或锁定哈希", "")
            recorded = re.search(r"ego lite ([0-9.]+)", version)
            actual = re.search(r"/Versions/([^/]+)/", str(path.resolve(strict=False)))
            if recorded and actual and recorded.group(1) != actual.group(1):
                findings.append(finding("registry", name, "metadata-drift", path,
                                        f"登记应用版本 {recorded.group(1)}，实际 {actual.group(1)}"))
        if path.resolve(strict=False) != declared_path.resolve(strict=False):
            findings.append(finding("registry", name, "wrong-target", path, str(declared_path)))
        if path.parent == CANONICAL_ROOT:
            for agent in DIRECT_AGENTS - registered[name]["agents"]:
                findings.append(finding(agent, name, "unsupported-scope", path))
        elif "grok" in registered[name]["agents"]:
            findings.append(finding("grok", name, "unsupported-scope", path))

    for name in registered.keys() - canonical.keys():
        findings.append(finding("registry", name, "registered-missing", registered[name]["path"]))

    for agent, root in LINK_ROOTS.items():
        entries = skill_entries(root)
        for name in sorted(set(entries) | set(registered)):
            path = entries.get(name)
            canonical_path = canonical.get(name)
            common_direct = canonical_path is not None and canonical_path.parent == CANONICAL_ROOT
            applicable = name in registered and agent in registered[name]["agents"]
            expected = applicable and not (agent == "codex" and common_direct)

            if path is None:
                if expected and canonical_path is not None:
                    findings.append(finding(agent, name, "missing-link", root / name))
                continue
            if path.is_symlink() and not path.exists():
                findings.append(finding(agent, name, "broken-link", path))
                continue
            if canonical_path is None:
                findings.append(finding(agent, name, "unmanaged", path))
                continue
            if not expected:
                findings.append(finding(agent, name, "unexpected-link", path))
                continue

            target = str(path.resolve(strict=False)) if path.is_symlink() else ""
            current_hash = folder_hash(path)
            if path.is_symlink() and current_hash is None:
                status = "broken-link"
            elif path.is_symlink() and path.resolve(strict=False) != canonical_path.resolve(strict=False):
                status = "wrong-target"
            elif path.is_symlink():
                status = "linked"
            elif current_hash is not None and current_hash == canonical_hashes[name]:
                status = "duplicate-identical"
            else:
                status = "conflict"
            findings.append(finding(agent, name, status, path, target))

    for source, root in LEGACY_ROOTS.items():
        status = f"legacy-{source}"
        for name, path in skill_entries(root).items():
            target = str(path.resolve(strict=False)) if path.is_symlink() else ""
            findings.append(finding(source, name, status, path, target))

    return {
        "canonical_root": str(CANONICAL_ROOT),
        "manifest": str(MANIFEST_PATH),
        "registered_skills": sorted(registered),
        "canonical_skills": sorted(canonical),
        "plugins_excluded": ["/Users/tangw/.claude/plugins"],
        "findings": findings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Skills 审计报告",
        "",
        f"唯一维护目录：`{report['canonical_root']}`",
        f"人工清单：`{report['manifest']}`",
        "Claude Code plugins：排除管理（保留在 `~/.claude/plugins`）",
        "CC Switch skills：仅识别历史残留，不作为有效来源",
        "",
        f"清单登记数：{len(report['registered_skills'])}",
        f"唯一维护目录实际数：{len(report['canonical_skills'])}",
        "",
        "| Agent/来源 | Skill | 状态 | 路径或目标 |",
        "|---|---|---|---|",
    ]
    for item in report["findings"]:
        location = item["target"] or item["path"]
        lines.append(f"| {item['agent']} | {item['name']} | {item['status']} | `{location}` |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在登记、适用范围或加载路径问题时返回非零状态",
    )
    args = parser.parse_args()
    report = inspect()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))

    if args.strict and any(item["status"] in PROBLEM_STATUSES for item in report["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
