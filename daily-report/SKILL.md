---
name: daily-report
description: 从 Git、钉钉 DWS 和手工补录中提取当前用户的工作证据，生成须确认后写入的个人日报、周报和个人复盘。用户要求生成日报、周报、工作汇报、个人复盘、汇总钉钉消息或 Git 工作记录时使用；支持 /report 和自然语言触发，适用于 Codex、Claude Code、Cursor、Grok、Windows 与 macOS。
---

# 日报、周报与个人复盘

以“可信的个人工作记录”为目标。不要把采集到的信息直接当作事实；先归因、分级、脱敏，再生成草稿。

## 前提

- 要求 Node.js 18+，用于运行 `scripts/monthly-report.mjs`。
- 将个人配置保存在 `<project-root>/docs/report/daily-report.config.yml`。读取 [references/config-schema.md](references/config-schema.md) 后再校验或初始化配置。
- Git、DWS 都是可选证据源。至少一个来源成功或用户补录内容后即可生成草稿。
- 使用配置的 `timezone` 解释日期；未配置时使用 `Asia/Shanghai`。

## 模式

按用户意图选择唯一主模式：

- `init`：初始化或刷新个人配置。
- `daily`：生成指定日期的日报；未指定日期时生成当天日报。默认在日报写入后继续生成个人复盘。
- `review`：只生成指定日期的个人复盘。
- `weekly`：生成目标日期所在自然周的周报；未指定日期时使用当天。

用户只说“日报”时执行 `daily`。周五完成日报和复盘后，只询问是否生成周报，绝不自动生成。

## 总流程

1. 读取配置；配置缺失时说明原因并直接进入初始化引导。用户拒绝初始化才停止。
2. 检查 Node、Git 和 DWS 可用性。DWS 不可用、未登录或单项命令失败时，记录缺失来源并继续其他来源；不要要求用户为降级模式再次确认。
3. 按 [references/data-collection.md](references/data-collection.md) 并行采集独立来源。先用 `dws contact user get-self` 识别当前用户；不得要求用户手填钉钉用户 ID。
4. 按 [references/evidence-rules.md](references/evidence-rules.md) 归因、去重、判断状态并脱敏。将 AI 推断的事项单列为候选建议，不能混入正式草稿。
5. 使用 [templates/daily.md](templates/daily.md) 生成日报草稿，展示来源覆盖、事实清单、候选建议和月文件 dry-run diff。无自动证据时允许用户手工补录或跳过。
6. 用户确认后才运行写入脚本。日报写入成功后，才采集私聊和听记并生成个人复盘草稿；个人复盘必须再次确认后写入。
7. 生成周报时，优先读取本周已确认的日报；缺少日期时询问是否补采原始数据或手工补录，不能静默重推已确认结论。

## 日报与复盘边界

- 日报只记录当前用户的工作。群聊中仅纳入用户发送、回复、被 @、被明确分派或明确参与的事项；相邻消息只能解释上下文。
- 日报月文件保留“已完成”“推进中”作为事实留档，并保留“今日工作（可复制）”作为去噪后的发送版本。
- 个人复盘在日报确认后生成，使用已确认日报作为事实底座，只读取与工作有关的私聊、日程和听记补充；不重复日报清单，不评分。
- 默认脱敏内部 IP、账号、手机号、群 ID、完整链接和凭证；他人姓名优先替换为职责角色。

## 初始化

1. 引导用户选择输出目录、私人复盘目录、时区、Git 仓库和群聊。
2. 用关键词搜索候选群，由用户确认后将群 ID 写入白名单。关键词只用于发现与刷新，不能作为运行时筛选条件。
3. 自动读取各仓库 Git 邮箱；配置支持多个精确邮箱，作者名只作兼容信息。
4. 将配置写到项目内既定路径。项目根本身是 Git 仓库时，将该路径加入 `.git/info/exclude`；不要修改团队 `.gitignore`。项目根不是 Git 仓库时，仅提示该配置为本地文件。

## 月文件写入

使用脚本而不是手工重排 Markdown：

```bash
node scripts/monthly-report.mjs upsert \
  --file "<month-file>" \
  --kind daily|weekly|review \
  --date "YYYY-MM-DD" \
  --content-file "<draft-markdown>" \
  --dry-run
```

确认 diff 后，移除 `--dry-run` 再写入。脚本负责日期、中文星期、ISO 周、同日替换、周报位置和原子写入；AI 不得自行计算星期或直接整体覆写月文件。

不要迁移或全量重排历史月报。脚本只替换目标日期或目标周报，并保留无关历史内容。

## 参考资料

- 配置与兼容规则：[references/config-schema.md](references/config-schema.md)
- 采集命令与降级：[references/data-collection.md](references/data-collection.md)
- 归因、状态和安全规则：[references/evidence-rules.md](references/evidence-rules.md)
- 日报模板：[templates/daily.md](templates/daily.md)
- 周报模板：[templates/weekly.md](templates/weekly.md)
- 复盘模板：[templates/private.md](templates/private.md)
