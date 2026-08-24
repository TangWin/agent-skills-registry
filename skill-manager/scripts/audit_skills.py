#!/usr/bin/env python3
"""只读审计用户级 Agent skills 的登记、适用范围和加载路径。"""

from __future__ import annotations

import argparse
import hashlib
import json
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
        if path.is_symlink() or (path.is_dir() and (path / "SKILL.md").is_file()):
            result[path.name] = path
    return result


def parse_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """从 SKILLS.md 的“已纳管”表读取 skill 与适用 Agent。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    in_managed_section = False
    column_names: list[str] | None = None
    result: dict[str, dict[str, Any]] = {}

    for line in lines:
        if line.strip() == "## 已纳管":
            in_managed_section = True
            continue
        if in_managed_section and line.startswith("## "):
            break
        if not in_managed_section or not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "名称" in cells and "适用 Agent" in cells:
            column_names = cells
            continue
        if column_names is None or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if len(cells) != len(column_names):
            continue

        row = dict(zip(column_names, cells))
        name = row["名称"].strip("`")
        agents = {
            AGENT_NAMES[item.strip()]
            for item in row["适用 Agent"].split("、")
            if item.strip() in AGENT_NAMES
        }
        if name:
            canonical_path = Path(row["唯一维护目录"].strip("`")).expanduser()
            result[name] = {"agents": agents, "path": canonical_path}

    return result


def folder_hash(path: Path) -> str | None:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
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
    registered = parse_manifest(MANIFEST_PATH)
    canonical = skill_entries(CANONICAL_ROOT)
    scoped_root = CANONICAL_ROOT / ".scoped"
    if scoped_root.is_dir():
        for skill_file in sorted(scoped_root.rglob("SKILL.md")):
            canonical.setdefault(skill_file.parent.name, skill_file.parent)
    canonical_hashes = {name: folder_hash(path) for name, path in canonical.items()}
    findings: list[dict[str, str]] = []

    for name, path in canonical.items():
        if name not in registered:
            findings.append(finding("registry", name, "unregistered-canonical", path))
            continue
        declared_path = registered[name]["path"]
        if path.resolve(strict=False) != declared_path.resolve(strict=False):
            findings.append(finding("registry", name, "wrong-target", path, str(declared_path)))
        if path.parent == CANONICAL_ROOT:
            for agent in DIRECT_AGENTS - registered[name]["agents"]:
                findings.append(finding(agent, name, "unsupported-scope", path))
        elif "grok" in registered[name]["agents"]:
            findings.append(finding("grok", name, "unsupported-scope", path))

    for name in registered.keys() - canonical.keys():
        findings.append(finding("registry", name, "registered-missing", CANONICAL_ROOT / name))

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
