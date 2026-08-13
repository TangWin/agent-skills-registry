#!/usr/bin/env python3
"""只读盘点用户级 Agent skills，并识别软链接、重复副本和冲突。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


CANONICAL_ROOT = Path("/Users/tangw/.agents/skills")
ROOTS = {
    "canonical": CANONICAL_ROOT,
    "codex": Path("/Users/tangw/.codex/skills"),
    "claude-code": Path("/Users/tangw/.claude/skills"),
    "cursor": Path("/Users/tangw/.cursor/skills"),
    "cc-switch": Path("/Users/tangw/.cc-switch/skills"),
}
IGNORED_NAMES = {".system", "skills-cursor"}
IGNORED_PARTS = {".git", "__pycache__", ".DS_Store"}


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


def inspect() -> dict[str, Any]:
    canonical = skill_entries(CANONICAL_ROOT)
    canonical_hashes = {name: folder_hash(path) for name, path in canonical.items()}
    findings: list[dict[str, str]] = []

    for agent, root in ROOTS.items():
        if agent == "canonical":
            continue
        for name, path in skill_entries(root).items():
            target = str(path.resolve(strict=False)) if path.is_symlink() else ""
            current_hash = folder_hash(path)
            canonical_path = canonical.get(name)
            if path.is_symlink() and current_hash is None:
                status = "broken-link"
            elif canonical_path is None:
                status = "unmanaged"
            elif path.is_symlink() and path.resolve(strict=False) == canonical_path.resolve(strict=False):
                status = "linked"
            elif current_hash is not None and current_hash == canonical_hashes[name]:
                status = "duplicate-identical"
            else:
                status = "conflict"
            findings.append({
                "agent": agent,
                "name": name,
                "status": status,
                "path": str(path),
                "target": target,
            })

    return {
        "canonical_root": str(CANONICAL_ROOT),
        "canonical_skills": sorted(canonical),
        "findings": findings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Skills 盘点报告",
        "",
        f"唯一维护目录：`{report['canonical_root']}`",
        "",
        f"已纳管 skill 数：{len(report['canonical_skills'])}",
        "",
        "| Agent | Skill | 状态 | 路径或目标 |",
        "|---|---|---|---|",
    ]
    for item in report["findings"]:
        location = item["target"] or item["path"]
        lines.append(
            f"| {item['agent']} | {item['name']} | {item['status']} | `{location}` |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在 conflict、unmanaged 或 broken-link 时返回非零状态",
    )
    args = parser.parse_args()
    report = inspect()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))

    if args.strict and any(
        item["status"] in {"conflict", "unmanaged", "broken-link"}
        for item in report["findings"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
