# 用户级 Skills 清单

唯一维护目录：`/Users/tangw/.agents/skills`

本清单只管理用户安装或自建的全局 skills，不管理 plugins、Agent 内置 skills、项目级 skills、凭据、缓存和会话。Codex 与 Grok 直接读取唯一维护目录；Claude Code 与 Cursor 通过软链接使用受管 skill。

“最后检查”是远端版本检查日期；“仅本地盘点”不代表已经联网确认最新版。来源或版本不明确时保留“待确认”，不得猜测。

## 已纳管

| 名称 | 唯一维护目录 | GitHub或官网 | 简介 | 当前版本、commit 或锁定哈希 | 安装或更新说明 | 适用 Agent | 最后检查 |
|---|---|---|---|---|---|---|---|
| code-review | `~/.agents/skills/code-review` | [mattpocock/skills](https://github.com/mattpocock/skills) | 并行进行规范与需求符合性审查 | `9df6fdac` | 仓库子目录 `skills/engineering/code-review`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| codebase-design | `~/.agents/skills/codebase-design` | [mattpocock/skills](https://github.com/mattpocock/skills) | 深模块设计与接口边界方法 | `c5dfc023` | 仓库子目录 `skills/engineering/codebase-design`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| domain-modeling | `~/.agents/skills/domain-modeling` | [mattpocock/skills](https://github.com/mattpocock/skills) | 建立领域术语、模型和架构决策 | `028a0e44` | 仓库子目录 `skills/engineering/domain-modeling`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| dws | `~/.agents/skills/dws` | 待确认 | 通过 DWS 管理钉钉产品能力 | 待确认；要求 DWS CLI `>=1.0.15` | 来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| find-skills | `~/.agents/skills/find-skills` | [vercel-labs/skills](https://github.com/vercel-labs/skills) | 发现并安装开放生态中的 skills | `3013fdeb` | 仓库子目录 `skills/find-skills`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| grill-me | `~/.agents/skills/grill-me` | [mattpocock/skills](https://github.com/mattpocock/skills) | 通过连续追问打磨方案 | `8320e7b8` | 仓库子目录 `skills/productivity/grill-me`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| grill-with-docs | `~/.agents/skills/grill-with-docs` | [mattpocock/skills](https://github.com/mattpocock/skills) | 在方案追问时同步沉淀领域文档 | `21adba95` | 仓库子目录 `skills/engineering/grill-with-docs`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| grilling | `~/.agents/skills/grilling` | [mattpocock/skills](https://github.com/mattpocock/skills) | 对计划或设计逐项压力测试 | `0ace40ac` | 仓库子目录 `skills/productivity/grilling`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| implement | `~/.agents/skills/implement` | [mattpocock/skills](https://github.com/mattpocock/skills) | 根据 spec 或 tickets 实施工作 | `aa659906` | 仓库子目录 `skills/engineering/implement`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| neat-freak | `~/.agents/skills/neat-freak` | 待确认 | 对代码、文档和记忆进行阶段性同步清理 | 待确认 | 来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| setup-matt-pocock-skills | `~/.agents/skills/setup-matt-pocock-skills` | [mattpocock/skills](https://github.com/mattpocock/skills) | 初始化工程 skills 所需项目配置 | `ed1bbe6a` | 仓库子目录 `skills/engineering/setup-matt-pocock-skills`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| skill-manager | `~/.agents/skills/skill-manager` | 本地自建 | 统一盘点、安装、登记和检查用户级 skills | 本地 Git | 在唯一维护目录修改；变更后校验并独立提交 | Codex、Claude Code、Cursor、Grok | 2026-08-13（本地创建） |
| storage-analyzer | `~/.agents/skills/storage-analyzer` | 待确认 | 只读分析磁盘并生成清理建议报告 | 待确认 | 来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| tdd | `~/.agents/skills/tdd` | [mattpocock/skills](https://github.com/mattpocock/skills) | 测试驱动开发工作流 | `57e1bee8` | 仓库子目录 `skills/engineering/tdd`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| teach | `~/.agents/skills/teach` | [mattpocock/skills](https://github.com/mattpocock/skills) | 在工作区内持续教授概念或技能 | `3b6a4245` | 仓库子目录 `skills/productivity/teach`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| to-spec | `~/.agents/skills/to-spec` | [mattpocock/skills](https://github.com/mattpocock/skills) | 将对话综合为可执行 spec | `b2f449aa` | 仓库子目录 `skills/engineering/to-spec`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| to-tickets | `~/.agents/skills/to-tickets` | [mattpocock/skills](https://github.com/mattpocock/skills) | 将方案拆成带依赖关系的 tickets | `c0bc0e58` | 仓库子目录 `skills/engineering/to-tickets`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| web-access | `~/.agents/skills/web-access` | [eze-is/web-access](https://github.com/eze-is/web-access) | 统一处理联网搜索、抓取和浏览器操作 | `7af34af6`；skill 标注 `2.5.3` | 仓库根目录 skill；检查 release、脚本和浏览器权限变化 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-contact | `~/.agents/skills/wecomcli-contact` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 查询企业微信通讯录成员 | `56c7ad32` | 仓库子目录 `skills/wecomcli-contact`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-doc | `~/.agents/skills/wecomcli-doc` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 管理企业微信文档和智能文档 | `ca9434ad` | 仓库子目录 `skills/wecomcli-doc`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-meeting | `~/.agents/skills/wecomcli-meeting` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 管理企业微信会议 | `01eda8d0` | 仓库子目录 `skills/wecomcli-meeting`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-msg | `~/.agents/skills/wecomcli-msg` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 查询和发送企业微信消息 | `7013cc41` | 仓库子目录 `skills/wecomcli-msg`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-schedule | `~/.agents/skills/wecomcli-schedule` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 管理企业微信日程和闲忙信息 | `e1afbba2` | 仓库子目录 `skills/wecomcli-schedule`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-smartsheet | `~/.agents/skills/wecomcli-smartsheet` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 管理企业微信智能表格结构和数据 | `382be575` | 仓库子目录 `skills/wecomcli-smartsheet`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |
| wecomcli-todo | `~/.agents/skills/wecomcli-todo` | [WeComTeam/wecom-cli](https://github.com/WeComTeam/wecom-cli) | 管理企业微信待办事项 | `1f55b2e5` | 仓库子目录 `skills/wecomcli-todo`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-13（仅本地盘点） |

## 待确认纳管

首次盘点发现以下内容，本次不移动、不覆盖、不删除：

| 类型 | Skill | 位置 | 处理原因 |
|---|---|---|---|
| 未纳管 | daily-report | `~/.codex/skills`、`~/.claude/skills`、`~/.cc-switch/skills` | Codex 版本与 Claude/cc-switch 版本内容不同，需要选择唯一维护版本 |
| 未纳管 | daily-report-workspace | `~/.claude/skills` | 仅 Claude Code 存在，需确认是否为用户级独立 skill |
| 未纳管 | frontend-design | `~/.claude/skills` | 仅 Claude Code 存在，需确认是否跨 Agent 使用 |
| 未纳管 | hatch-pet | `~/.codex/skills` | 仅 Codex 存在，需确认是否跨 Agent 使用 |
| 同名冲突 | find-skills、grill-me、grill-with-docs、setup-matt-pocock-skills、teach | `~/.claude/skills`、`~/.codex/skills` 或 `~/.cc-switch/skills` | 与唯一维护目录内容不同，禁止自动覆盖 |
| 同名冲突 | neat-freak、storage-analyzer | `~/.claude/skills` 或 `~/.cc-switch/skills` | 与唯一维护目录内容不同，禁止自动覆盖 |
| 相同副本 | dws、wecomcli-* | `~/.codex/skills`、`~/.claude/skills` 或 `~/.cc-switch/skills` | 内容相同但仍是实体副本，确认后可改为软链接 |

通过以下命令获取实时明细：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/audit_skills.py
```
