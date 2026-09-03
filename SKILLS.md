# 用户级 Skills 清单

唯一维护目录：`/Users/tangw/.agents/skills`

私有版本仓库：`git@github.com:TangWin/agent-skills-registry.git`

本清单只管理用户安装或自建的全局 skills，不管理 plugins、Agent 内置 skills、项目级 skills、凭据、缓存和会话。Codex 与 Grok 直接读取唯一维护目录；Claude Code 与 Cursor 通过软链接使用受管 skill。

“最后检查”是远端版本检查日期；“仅本地盘点”不代表已经联网确认最新版。来源或版本不明确时保留“待确认”，不得猜测。

每个 skill 的安装、更新和回退使用独立 Git 提交。更新后的版本先作为候选版本试用；确认稳定后使用 `stable/<skill>/<version>` 标签并推送到私有版本仓库。

## 已纳管

| 名称 | 唯一维护目录 | GitHub或官网 | 简介 | 当前版本、commit 或锁定哈希 | 安装或更新说明 | 适用 Agent | 最后检查 |
|---|---|---|---|---|---|---|---|
| code-review | `~/.agents/skills/code-review` | [mattpocock/skills](https://github.com/mattpocock/skills) | 并行进行规范与需求符合性审查 | `5b15a47` | 仓库子目录 `skills/engineering/code-review`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| codebase-design | `~/.agents/skills/codebase-design` | [mattpocock/skills](https://github.com/mattpocock/skills) | 深模块设计与接口边界方法 | `5b15a47` | 仓库子目录 `skills/engineering/codebase-design`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| daily-report | `~/.agents/skills/daily-report` | 待确认 | 从 Git、钉钉和手工补录生成日报、周报、月度总结与复盘 | 本地迭代 2026-08-26 | 本地自研迭代：按群拉取消息、私聊自动翻页、昨日计划追问、时区统一、月度总结模式；来源确认前不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-26 |
| domain-modeling | `~/.agents/skills/domain-modeling` | [mattpocock/skills](https://github.com/mattpocock/skills) | 建立领域术语、模型和架构决策 | `5b15a47` | 仓库子目录 `skills/engineering/domain-modeling`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| dingtalk-aisearch | `~/.agents/skills/dingtalk-aisearch` | 待确认 | 钉钉人员语义搜索与跨源定位 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-aitable | `~/.agents/skills/dingtalk-aitable` | 待确认 | 钉钉 AI 表格管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-calendar | `~/.agents/skills/dingtalk-calendar` | 待确认 | 钉钉日历与会议室管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-chat | `~/.agents/skills/dingtalk-chat` | 待确认 | 钉钉群聊与消息管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-contact | `~/.agents/skills/dingtalk-contact` | 待确认 | 钉钉通讯录精确查询 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-doc | `~/.agents/skills/dingtalk-doc` | 待确认 | 钉钉在线文档管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-drive | `~/.agents/skills/dingtalk-drive` | 待确认 | 钉钉文件与文档空间管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-event | `~/.agents/skills/dingtalk-event` | 待确认 | 钉钉个人 IM 与审批事件监听 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-mail | `~/.agents/skills/dingtalk-mail` | 待确认 | 钉钉邮箱管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-minutes | `~/.agents/skills/dingtalk-minutes` | 待确认 | 钉钉 AI 听记查询 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-misc | `~/.agents/skills/dingtalk-misc` | 待确认 | 钉钉长尾产品能力入口 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-shared | `~/.agents/skills/dingtalk-shared` | 待确认 | 钉钉跨产品共享入口 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-todo | `~/.agents/skills/dingtalk-todo` | 待确认 | 钉钉待办管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| dingtalk-wiki | `~/.agents/skills/dingtalk-wiki` | 待确认 | 钉钉知识库与空间管理 | 待确认；要求 DWS CLI | DWS 拆分 skill；来源确认前只允许本地维护，不自动更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23（仅本地盘点） |
| ego-browser | `~/.agents/skills/ego-browser` | [ego lite 官网](https://lite.ego.app/) | AI 友好的 Chromium 浏览器自动化（ego lite 内置 skill） | `v1.2.3`（ego lite 0.4.7.3 内置） | 唯一维护目录处为 ego 安装器自管软链接，指向 app 内置目录并随 app 升级自动更新；仓库只记录链接，不备份 skill 内容 | Codex、Claude Code、Cursor、Grok | 2026-08-26 |
| find-skills | `~/.agents/skills/find-skills` | [vercel-labs/skills](https://github.com/vercel-labs/skills) | 发现并安装开放生态中的 skills | `v1.5.23 / 435076e` | 仓库子目录 `skills/find-skills`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| frontend-design | `~/.agents/skills/frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) | 为前端页面提供有辨识度、非模板化的视觉设计指导 | `3b3fad9` | 仓库子目录 `skills/frontend-design`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-23 |
| grill-me | `~/.agents/skills/grill-me` | [mattpocock/skills](https://github.com/mattpocock/skills) | 通过连续追问打磨方案 | `5b15a47` | 仓库子目录 `skills/productivity/grill-me`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| grill-with-docs | `~/.agents/skills/grill-with-docs` | [mattpocock/skills](https://github.com/mattpocock/skills) | 在方案追问时同步沉淀领域文档 | `5b15a47` | 仓库子目录 `skills/engineering/grill-with-docs`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| hatch-pet | `~/.agents/skills/.scoped/codex/hatch-pet` | 本地安装 | 创建、修复和验证 Codex 动画宠物资源 | 本地迁移快照 | 依赖 Codex 的 imagegen 系统 skill，仅链接到 Codex | Codex | 2026-08-23（仅本地盘点） |
| grilling | `~/.agents/skills/grilling` | [mattpocock/skills](https://github.com/mattpocock/skills) | 对计划或设计逐项压力测试 | `5b15a47` | 仓库子目录 `skills/productivity/grilling`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| implement | `~/.agents/skills/implement` | [mattpocock/skills](https://github.com/mattpocock/skills) | 根据 spec 或 tickets 实施工作 | `5b15a47` | 仓库子目录 `skills/engineering/implement`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| leader | `~/.agents/skills/leader` | [KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) | 将模糊想法拆成可直接交给 Agent 执行的目标任务书 | `7a5c493` | 仓库子目录 `leader`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-09-03 |
| neat-freak | `~/.agents/skills/neat-freak` | [KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) | 对代码、文档和记忆进行阶段性同步清理 | `7a5c493` | 仓库子目录 `neat-freak`；更新前检查盘点脚本和评测文件 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| prototype | `~/.agents/skills/prototype` | [mattpocock/skills](https://github.com/mattpocock/skills) | 用一次性原型验证业务逻辑、状态模型或 UI 方案 | `5b15a47` | 仓库子目录 `skills/engineering/prototype`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| setup-matt-pocock-skills | `~/.agents/skills/setup-matt-pocock-skills` | [mattpocock/skills](https://github.com/mattpocock/skills) | 初始化工程 skills 所需项目配置 | `5b15a47` | 仓库子目录 `skills/engineering/setup-matt-pocock-skills`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| skill-creator | `~/.agents/skills/skill-creator` | [anthropics/skills](https://github.com/anthropics/skills) | 创建、改进与评测 Agent Skills（官方） | `3b3fad9` | 仓库子目录 `skills/skill-creator`；评测脚本依赖本机 claude CLI | Codex、Claude Code、Cursor、Grok | 2026-08-25 |
| skill-manager | `~/.agents/skills/skill-manager` | 本地自建 | 统一盘点、安装、登记和检查用户级 skills | 本地 Git | 在唯一维护目录修改；变更后校验并独立提交 | Codex、Claude Code、Cursor、Grok | 2026-08-13（本地创建） |
| storage-analyzer | `~/.agents/skills/storage-analyzer` | [KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) | 只读分析磁盘并生成清理建议报告 | `7a5c493` | 仓库子目录 `storage-analyzer`；更新前检查扫描、报告服务和清理脚本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| tdd | `~/.agents/skills/tdd` | [mattpocock/skills](https://github.com/mattpocock/skills) | 测试驱动开发工作流 | `5b15a47` | 仓库子目录 `skills/engineering/tdd`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| teach | `~/.agents/skills/teach` | [mattpocock/skills](https://github.com/mattpocock/skills) | 在工作区内持续教授概念或技能 | `5b15a47` | 仓库子目录 `skills/productivity/teach`；更新前处理现有冲突副本 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| to-spec | `~/.agents/skills/to-spec` | [mattpocock/skills](https://github.com/mattpocock/skills) | 将对话综合为可执行 spec | `5b15a47` | 仓库子目录 `skills/engineering/to-spec`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| to-tickets | `~/.agents/skills/to-tickets` | [mattpocock/skills](https://github.com/mattpocock/skills) | 将方案拆成带依赖关系的 tickets | `5b15a47` | 仓库子目录 `skills/engineering/to-tickets`；按子目录差异更新 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |
| web-access | `~/.agents/skills/web-access` | [eze-is/web-access](https://github.com/eze-is/web-access) | 统一处理联网搜索、抓取和浏览器操作 | `2.5.4 / 33eef84` | 仓库根目录 skill；检查 release、脚本和浏览器权限变化 | Codex、Claude Code、Cursor、Grok | 2026-08-22 |

## 待确认纳管

当前无待确认纳管项。迁移前的冲突版本已保存在 `/Users/tangw/skill-migration-backup/20260823-cc-switch-migration`，未删除。

通过以下命令获取实时明细：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/audit_skills.py
```
