---
name: daily-report
description: 从 Git、钉钉 DWS 和手工补录中提取当前用户的工作证据，生成须确认后写入的个人日报、周报、月度总结和个人复盘。用户要求生成日报、周报、月报、工作汇报、个人复盘、汇总钉钉消息或 Git 工作记录时使用；支持 /report 和自然语言触发，适用于 Codex、Claude Code、Cursor、Grok、Windows 与 macOS。
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

| 用户原话示例 | 模式 | 说明 |
| --- | --- | --- |
| “初始化日报配置”“刷新群白名单” | `init` | 初始化或刷新个人配置 |
| “日报”“生成今天/某日日报”“汇总今天工作” | `daily` | 未指定日期时用当天；写入后默认继续生成个人复盘 |
| “只要复盘”“补某天的复盘” | `review` | 只生成指定日期的个人复盘 |
| “周报”“本周工作总结” | `weekly` | 目标日期所在自然周；未指定时用当天 |
| “月报”“月度总结”“这个月干了什么” | `monthly` | 汇总目标月份已确认的日报与周报 |

意图模糊时优先问一句，不要猜。周五完成日报和复盘后，只询问是否生成周报，绝不自动生成；月末同理不自动生成月度总结。

## 总流程

1. 运行 `node scripts/monthly-report.mjs check-config --file <配置路径>` 校验配置。`status` 非 `ok` 时把返回的 `hints` 原样转述给用户，不要自行编写补救文案；配置缺失时直接进入初始化引导，用户拒绝初始化才停止。`warnings` 在草稿的来源覆盖中展示。
2. 检查 Node、Git 和 DWS 可用性。DWS 不可用、未登录或单项命令失败时，记录缺失来源并继续其他来源；不要要求用户为降级模式再次确认。
3. 按 [references/data-collection.md](references/data-collection.md) 并行采集独立来源。先用 `dws contact user get-self` 识别当前用户；不得要求用户手填钉钉用户 ID。
4. 按 [references/evidence-rules.md](references/evidence-rules.md) 归因、去重、判断状态并脱敏。将 AI 推断的事项单列为候选建议，不能混入正式草稿。
5. 生成日报草稿前，回读最近一份已写入的日报（先查当月月文件，月初再查上月，最多回溯 7 天），提取其「明日计划」。将每条计划与今日证据比对，标注"疑似已推进（附证据）"或"未见推进动作"，在展示草稿时顺带问一句进度：哪些有推进、哪些顺延或取消。这只是提醒，用户可跳过不答；无证据且未经用户确认的计划不得写入「已完成」或「推进中」，也不自动带入今日「明日计划」，只有用户明确要求顺延的条目才写入。找不到近 7 天日报时静默跳过，不追问。
6. 使用 [templates/daily.md](templates/daily.md) 生成日报草稿，展示来源覆盖、事实清单、候选建议和月文件 dry-run diff。无自动证据时允许用户手工补录或跳过。
7. 用户确认后才运行写入脚本。日报写入成功后，才采集私聊和听记并生成个人复盘草稿；个人复盘必须再次确认后写入。
8. 生成周报时，优先读取本周已确认的日报；缺少日期时询问是否补采原始数据或手工补录，不能静默重推已确认结论。按证据规则做跨天业务线聚合，不按天罗列。
9. 生成月度总结时，只读取目标月份月文件中已确认的日报与周报作为事实底座，不重新采集原始数据。使用 [templates/monthly.md](templates/monthly.md) 按业务主线聚合；缺失超过一周日报时先提示覆盖缺口再生成。确认后用 `--kind monthly` 写入月文件顶部。

## 日报与复盘边界

- 日报只记录当前用户的工作。群聊中仅纳入用户发送、回复、被 @、被明确分派或明确参与的事项；相邻消息只能解释上下文。
- 无来源的具体数字、客户名、时间节点和他人行动一律不写；证据稀疏时不硬凑条目，按证据规则的兜底流程处理。
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
  --kind daily|weekly|review|monthly \
  --date "YYYY-MM-DD" \
  --content-file "<draft-markdown>" \
  --dry-run
```

确认 diff 后，移除 `--dry-run` 再写入。脚本负责日期、中文星期、ISO 周、同日替换、周报与月度总结位置和原子写入；AI 不得自行计算星期或直接整体覆写月文件。

不要迁移或全量重排历史月报。脚本只替换目标日期或目标周报，并保留无关历史内容。

## 参考资料

- 配置与兼容规则：[references/config-schema.md](references/config-schema.md)
- 采集命令与降级：[references/data-collection.md](references/data-collection.md)
- 归因、状态和安全规则：[references/evidence-rules.md](references/evidence-rules.md)
- 日报模板：[templates/daily.md](templates/daily.md)
- 周报模板：[templates/weekly.md](templates/weekly.md)
- 月度总结模板：[templates/monthly.md](templates/monthly.md)
- 复盘模板：[templates/private.md](templates/private.md)
