---
name: release-cleanup
description: 独立功能合入 master 并发布生产后，归回文档、校验备份并删除本地功能 worktree 和分支。用于“发布后收尾”“清理已上线分支/worktree”，以及任务已授权收尾后收到“已发布”的确认；只有开发或合并完成时先盘点。
---

# 发布后收尾

使用附带脚本完成重复操作，不另写临时清理代码。先读适用的 AGENTS.md 和文档目录规则；已读且未变化则复用。主检出目录可以停在 pre，不为清理切分支。

## 正常路径

1. 确定本次功能分支、主检出目录 REPO、脚本所在技能目录 SKILL_DIR。STATE 取主目录 `ai_work/resources/文档归档/worktree收尾/<本次任务标识>` 的新目录；续跑复用原目录。默认只处理本次功能，用户要求全仓盘点才改用 `--all-features`。
2. 确认候选未被其他聊天或任务使用。Codex 管理的 worktree 走其附件/归档工具，先保存 ignored 文件；不交给本脚本删除。执行只读 Git 盘点（仅写本地计划）：

```bash
python3 "$SKILL_DIR/scripts/cleanup.py" plan --repo "$REPO" --branch "$BRANCH" --state "$STATE"
```

3. 用户确认主分支已更新且目标功能已发布，并已授权收尾，即可执行；同一任务不重复索取授权。`--released` 记录真实确认内容，不由 agent 推断发布状态。执行期间不要编辑候选 worktree。

```bash
python3 "$SKILL_DIR/scripts/cleanup.py" apply --state "$STATE" --released "用户已确认目标功能发布生产并要求收尾"
```

正常只读取两次 JSON 摘要；详细路径、SHA-256 和进度留在 STATE，不把完整文件列表、日志或压缩包内容灌入对话。只有 blocked 项才读 [异常与恢复](references/exceptions.md)，只检查相关差异。

## 完成条件

脚本输出目标项 `status=done` 且 `blocked` 为空：文档已迁回或明确归档，来源备份已校验，worktree 与本地分支已删除。报告删除/保留数量、文档及状态目录、未提交 SQL 等待办即可。只有合并确认时停在计划，问是否已发布；本 skill 不会后台监听发布。

适用 Python 3.8+、Git 与 macOS/Linux。脚本仅检查本地固定 master（可用 `--base` 指定），不联网验证生产。主分支与已知远端引用存在冲突时先查明。保留长期分支、主目录原有改动和远端分支；不提交、推送、部署或运行 Maven。
