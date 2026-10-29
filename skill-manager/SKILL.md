---
name: skill-manager
description: 统一管理本机用户级 Agent skills。用户要求盘点、登记、安装、迁移、检查更新、更新或修复 Codex、Claude Code、Cursor、Grok、Antigravity 的 skills，或提到 SKILLS.md、skill 来源、重复副本、软链接和版本漂移时使用。不管理 plugins、Agent 内置 skills 或项目级 skills。
---

# Skill Manager

以 `/Users/tangw/.agents/skills` 为唯一维护目录，以同目录的 `SKILLS.md` 为唯一人工清单。私有备份远端为 `git@github.com:TangWin/agent-skills-registry.git`。默认不启动服务；仅在用户明确要求启动服务或打开 Web 控制台时启动。为临时操作启动的服务，在该操作结束后关闭；用户主动要求启动并使用控制台时，交付访问地址后保持运行，待用户明确表示用完或要求关闭时再停止。不得创建定时任务。

## 边界

- 只管理用户安装或自建的全局 skills。
- 不管理 plugins、凭据、缓存、会话、Agent 内置 skills 和项目仓库内 skills。
- Claude Code 专属 plugins（例如 Claude-HUD、Claude-MEM）保留在 `~/.claude/plugins`，即使 plugin 内含 skills，也不迁入唯一维护目录。
- Codex 与 Grok 直接读取 `/Users/tangw/.agents/skills`。
- Claude Code 使用 `/Users/tangw/.claude/skills/<name>` 软链接。
- Cursor 使用 `/Users/tangw/.cursor/skills/<name>` 软链接。
- Antigravity 使用 `/Users/tangw/.gemini/config/skills/<name>` 软链接。
- CC Switch 只管理模型和供应商；`~/.cc-switch/skills` 仅作为历史残留检查，不是合法 skill 来源。
- 通用 skill 放在 `/Users/tangw/.agents/skills/<name>`；工具专属 skill 放在 `/Users/tangw/.agents/skills/.scoped/<scope>/<name>`，再按“适用 Agent”链接到工具目录。
- `SKILLS.md` 的“适用 Agent”决定 Claude Code、Cursor、Antigravity，以及 scoped Codex skill 是否应创建软链接。Grok 当前只直读通用目录，暂不支持 scoped skill。
- 不覆盖来源不明、本地已修改或同名不同内容的目录。
- 不向版本仓库提交凭据、账号、缓存、会话、`.env`、`config.env`、私钥或证书私钥。
- 所有联网检查遵守当前 Agent 的联网 skill、权限和安全规则。

## Git 版本与远端备份

- `main` 保存当前已纳管内容；一个 skill 的安装、更新或回退必须使用一个独立提交，禁止把多个 skill 混入同一版本提交。
- 执行写操作前要求整个工作区和暂存区干净，拒绝夹带任何未提交改动。更新前通过远端实际引用确认当前基线已备份到 `origin/main`；不自动推送，未备份时停止并展示所需操作。
- 推送表示备份，稳定标签表示用户试用认可。`main` 可以包含候选提交；不得把“已备份”表述为“已稳定”。套件更新从同一个已备份基线开始，逐 skill 验证和独立提交，失败停止并报告已完成项。
- 更新完成并验证后创建候选提交，但不自动标记稳定。用户确认试用通过后，创建 `stable/<skill>/<version>` 标签并推送提交和标签。
- 推送前检查暂存文件和敏感信息；存在未确认文件、未登记目录或疑似凭据时停止。
- 远端是本地管理仓库的私有备份，不替代各 skill 在 `SKILLS.md` 中登记的上游来源。
- 禁止使用 `git reset --hard` 回退，禁止强制推送。

## 功能入口

- 命令行与 Agent 原生交互（核心推荐）：
  - `skill_ops.py check-all`：并发探测全量技能上游更新（秒级完成），输出可升级列表与新旧版本。
  - `skill_ops.py check <skill>`：检查单个技能更新并获取完整 `diff_summary` 与 `patch` 补丁。
  - `skill_ops.py upgrade <skill>`：在确认变更后安全升级目标技能，事务隔离并生成独立候选提交。
  - `audit_skills.py`：只读审计；加 `--fix` 自动修复可安全处理的软链接问题（漏链、死链、无效链）。
  - `skill_ops.py repair`：一键安全修复各 Agent 目录下的软链接异常。
  - `skill_ops.py install <source>`：规范安装新技能（支持 GitHub 仓库或本地目录），校验后写入锁定、更新清单、创建软链接并独立提交。
  - `skill_ops.py register <name> <target>`：规范登记本地已存在目录或外部 App 自管软链接。
  - `skill_ops.py tag-stable <skill> [--push]`：为试用合格的技能标记稳定标签（`stable/<skill>/<version>`），可选推送到远端备份。
  - `skill_ops.py rollback <skill> [tag]`：列出或安全回退到历史稳定标签并独立提交。
  - `skill_ops.py recover`：恢复更新、安装或启用范围变更留下的中断事务。
- Web 控制台（备用大盘看板）：供用户需要全局扫盘或批量鼠标勾选时使用；默认不启动。全部变更均受进程互斥锁与脚本事务保护。

## 首次盘点或审计

运行命令行审计：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/audit_skills.py
```

用户明确要求 Web 控制台时，默认以只读模式启动：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/web_server.py
```

仅在用户已授权写操作时添加 `--allow-write`。控制台绑定 `127.0.0.1`，使用会话令牌、Host/Origin 检查；前端依赖固定保存在 `assets/vendor`，不加载 CDN 脚本。每次写入仍须展示并确认具体差异，启动写入模式不等于确认所有更新。

命令行检查单个 skill（只读；会联网下载到临时目录）：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/skill_ops.py check <skill>
```


按以下状态报告：

- `invalid-manifest`：清单缺列、坏行、重复名称、未知 Agent、路径越界或锁定记录损坏。
- `invalid-skill`：受管目录缺少可读取的 `SKILL.md`。
- `duplicate-name`：通用目录或 scoped 目录出现重名。
- `content-drift`：实际内容与安装时锁定摘要不同。
- `metadata-drift`：外部自管应用版本与清单登记不同，只报告，不自动改写来源信息。
- `linked`：正确指向唯一维护目录；不代表版本已验证或已稳定。
- `missing-link`：清单声明适用，但目标 Agent 缺少软链接。
- `wrong-target`：软链接存在，但没有指向唯一维护目录，包含指向 CC Switch 的链接。
- `unexpected-link`：清单未声明适用，但目标 Agent 存在该链接。
- `duplicate-identical`：内容相同但存在实体副本，等待用户确认后改为软链接。
- `conflict`：同名但内容不同，只报告，不判断哪个版本正确。
- `unmanaged`：其他 Agent 目录存在、但唯一维护目录没有。
- `broken-link`：软链接目标失效。
- `unregistered-canonical`：唯一维护目录存在，但 `SKILLS.md` 未登记。
- `registered-missing`：`SKILLS.md` 已登记，但唯一维护目录缺失。
- `unsupported-scope`：skill 的存储位置与直读 Agent 的适用范围无法同时满足。
- `legacy-cc-switch`：CC Switch 目录仍有残留，只报告，不自动删除。

盘点默认只读。迁移、替换或删除前，逐项展示路径、差异和建议，并等待用户明确确认。

## 安装或登记

1. 确认官方/GitHub 来源、skill 子目录、适用 Agent 和更新方式；无法确认就停止并询问。
2. 在临时目录读取完整 `SKILL.md`，列出 `scripts/`、hooks、可执行文件、联网及外部工具要求。
3. 向用户展示风险和将写入、链接的准确路径，等待确认。
4. 目标 `/Users/tangw/.agents/skills/<name>` 已存在时禁止覆盖；内容有差异时转入盘点流程。
5. 通用 skill 安装在唯一维护目录顶层；工具专属 skill 安装在 `.scoped/<scope>/<name>`。根据“适用 Agent”为 Claude Code、Cursor 和 scoped Codex skill 创建所需软链接；禁止替换现有实体目录或不同目标的链接。Grok 专属或不适用于 Grok 的 skill 不得放在通用目录顶层。
6. 更新 `SKILLS.md` 的主表，填写来源、版本或 commit、更新说明、适用 Agent 和检查日期。
7. 运行本 skill 的审计脚本和可用的 skill validator。
8. 仅在验证成功且用户已授权本次安装时，在 `/Users/tangw/.agents/skills` 创建一个独立候选提交；试用通过后按 Git 版本与远端备份规则标记稳定。

## 检查更新与 Agent 智能摘要

1. 用户要求“检查更新”或询问技能状态时：
   - Agent 优先执行 `python3 /Users/tangw/.agents/skills/skill-manager/scripts/skill_ops.py check-all`；
   - 用精炼 Markdown 表格直接在对话中输出可升级技能名称、当前版本与最新版本，列出可重点关注的工具。
2. 用户询问“某技能更新了什么”或请求“升级某技能”时：
   - Agent 执行 `python3 /Users/tangw/.agents/skills/skill-manager/scripts/skill_ops.py check <skill>` 获取完整差异；
   - **由当前 Agent 亲自阅读 `diff_summary.patch` 代码与文档变更，提炼人类可读的结构化中文摘要**：
     - ① **核心功能变化**：新增了什么能力、工具或命令；
     - ② **优化与修复**：修复了什么具体问题或提示词调整；
     - ③ **安全性与风险**：是否引入新依赖、脚本、破坏性修改或权限变化；
   - 在对话流内清晰展示给用户，并询问是否确认升级。

## 更新执行

1. 用户确认升级后（如回复“升级”、“确认”）：
   - Agent 执行 `python3 /Users/tangw/.agents/skills/skill-manager/scripts/skill_ops.py upgrade <skill>`；
   - 脚本自动执行事务隔离升级、核对基线、更新清单和锁定文件，并在 Git 中生成独立的候选版本提交；
   - Agent 向用户汇报升级完成结果（新 commit 与版本号），提示可日常试用。试用满意后可再按需打标稳定。
2. 异常处理：
   - 升级期间出现异常或中断，Agent 运行 `python3 /Users/tangw/.agents/skills/skill-manager/scripts/skill_ops.py recover` 恢复事务；
   - 若本地存在未提交改动或版本漂移，拒绝覆盖，先向用户报告漂移原因。
7. Agent 启用范围变更也必须先整批预检再逐项提交；写入前持久化旧清单、旧链接及预期链接状态，中断后通过 `recover` 恢复。恢复时发现链接被其他操作改动则保留事务并停止，禁止覆盖；布局无法满足 Codex/Grok 直读约束时拒绝写入，迁移 `.scoped` 另行展示路径与差异并确认。适用 Agent 是兼容性声明与实际启用范围的共同约束，不能通过取消一个复选框伪装停止直读。

## 标记稳定与回退

用户确认候选版本好用后：

1. 确认候选提交只包含目标 skill 与其 `SKILLS.md` 登记变化。
2. 创建唯一标签 `stable/<skill>/<version>`；标签已存在时禁止移动或覆盖。
3. 经敏感信息与范围检查后推送 `main` 和该标签。锁定文件记录安装时的候选快照，不手工修改状态；界面仅在稳定标签中的锁定记录与当前记录完全相同时显示“已稳定”。

用户要求回退某个 skill 时：

1. 展示该 skill 可用的稳定标签、对应上游版本和提交时间，等待用户指定目标。
2. 从选定标签按该 skill 的真实路径（含 `.scoped`）恢复目录及其 `.skill-locks/<skill>.json`；旧标签没有锁定记录则移除该项记录并明确标为未锁定。不整体恢复旧版 `SKILLS.md`。
3. 只修改 `SKILLS.md` 中该 skill 的版本、状态和检查信息。
4. 重新验证 skill、软链接和脚本；失败则恢复回退前内容。
5. 创建独立的回退提交，保留候选版本和回退操作的完整历史；用户确认后推送。

## 清单约束

- `SKILLS.md` 主表只放已纳管且唯一维护目录真实存在的 skills。
- 工具专属普通 skill 也必须登记，唯一维护目录填写 `.scoped` 下的真实路径；plugins 不登记。
- “适用 Agent”必须使用 `Codex`、`Claude Code`、`Cursor`、`Grok`、`Antigravity`，多个值用中文顿号分隔；审计脚本据此校验加载路径。
- 来源不明写“待确认”，不得猜测官网、GitHub、版本或兼容性。
- 内容冲突和未纳管项写入“待确认纳管”区，不混入主表。
- 安装或更新必须在同一操作中同步修改清单。
- 不因格式统一而改写其他 skill 的内容。

## 锁定记录与验证

- `SKILLS.md` 保持唯一人工清单；`.skill-locks/<skill>.json` 是更新成功时生成的机器记录，包括来源、子目录、完整 commit、内容摘要、管理方式和安装时间，不手工维护第二份来源。
- 既有 skill 没有锁定记录时显示“未锁定”，不能伪造已验证或稳定基线。外部安装器自管链接显示“外部自管”，不纳入自动更新。
- 本机路径健康、内容一致、上游版本一致、试用稳定是不同状态，不能用单一绿色状态替代。
- 更新验收须覆盖忽略规则下的文件完整提交、从候选提交恢复后摘要一致，以及启用范围变更在中断后恢复清单和链接；普通异常回滚测试不能代替崩溃恢复测试。
- 回归测试：`python3 -B -m unittest discover -s /Users/tangw/.agents/skills/skill-manager/tests -v`。测试仅使用临时仓库和本地裸仓库，模拟网络，不触及用户 skills、真实远端和监听端口。界面静态检查：`node /Users/tangw/.agents/skills/skill-manager/tests/check_ui.cjs`，验证内联脚本语法与 Vue 模板编译，不代替真实浏览器试用。
