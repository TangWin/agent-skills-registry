---
name: skill-manager
description: 统一管理本机用户级 Agent skills。用户要求盘点、登记、安装、迁移、检查更新、更新或修复 Codex、Claude Code、Cursor、Grok 的 skills，或提到 SKILLS.md、skill 来源、重复副本、软链接和版本漂移时使用。不管理 plugins、Agent 内置 skills 或项目级 skills。
---

# Skill Manager

以 `/Users/tangw/.agents/skills` 为唯一维护目录，以同目录的 `SKILLS.md` 为唯一人工清单。私有备份远端为 `git@github.com:TangWin/agent-skills-registry.git`。不得启动服务或创建定时任务。

## 边界

- 只管理用户安装或自建的全局 skills。
- 不管理 plugins、凭据、缓存、会话、Agent 内置 skills 和项目仓库内 skills。
- Codex 与 Grok 直接读取 `/Users/tangw/.agents/skills`。
- Claude Code 使用 `/Users/tangw/.claude/skills/<name>` 软链接。
- Cursor 使用 `/Users/tangw/.cursor/skills/<name>` 软链接。
- 不覆盖来源不明、本地已修改或同名不同内容的目录。
- 不向版本仓库提交凭据、账号、缓存、会话、`.env`、`config.env`、私钥或证书私钥。
- 所有联网检查遵守当前 Agent 的联网 skill、权限和安全规则。

## Git 版本与远端备份

- `main` 保存当前已纳管内容；一个 skill 的安装、更新或回退必须使用一个独立提交，禁止把多个 skill 混入同一版本提交。
- 执行更新前，要求目标 skill 无本地未提交改动，并将当前已提交的稳定基线推送到 `origin/main`；推送失败时停止更新。
- 更新完成并验证后创建候选提交，但不自动标记稳定。用户确认试用通过后，创建 `stable/<skill>/<version>` 标签并推送提交和标签。
- 推送前检查暂存文件和敏感信息；存在未确认文件、未登记目录或疑似凭据时停止。
- 远端是本地管理仓库的私有备份，不替代各 skill 在 `SKILLS.md` 中登记的上游来源。
- 禁止使用 `git reset --hard` 回退，禁止强制推送。

## 首次盘点或审计

运行：

```bash
python3 /Users/tangw/.agents/skills/skill-manager/scripts/audit_skills.py
```

按以下状态报告：

- `linked`：正确指向唯一维护目录。
- `duplicate-identical`：内容相同但存在实体副本，等待用户确认后改为软链接。
- `conflict`：同名但内容不同，只报告，不判断哪个版本正确。
- `unmanaged`：其他 Agent 目录存在、但唯一维护目录没有。
- `broken-link`：软链接目标失效。

盘点默认只读。迁移、替换或删除前，逐项展示路径、差异和建议，并等待用户明确确认。

## 安装或登记

1. 确认官方/GitHub 来源、skill 子目录、适用 Agent 和更新方式；无法确认就停止并询问。
2. 在临时目录读取完整 `SKILL.md`，列出 `scripts/`、hooks、可执行文件、联网及外部工具要求。
3. 向用户展示风险和将写入、链接的准确路径，等待确认。
4. 目标 `/Users/tangw/.agents/skills/<name>` 已存在时禁止覆盖；内容有差异时转入盘点流程。
5. 安装后，只在目标位置不存在时创建 Claude Code、Cursor 软链接；禁止替换现有实体目录或不同目标的链接。
6. 更新 `SKILLS.md` 的主表，填写来源、版本或 commit、更新说明、适用 Agent 和检查日期。
7. 运行本 skill 的审计脚本和可用的 skill validator。
8. 仅在验证成功且用户已授权本次安装时，在 `/Users/tangw/.agents/skills` 创建一个独立候选提交；试用通过后按 Git 版本与远端备份规则标记稳定。

## 检查更新

1. 读取 `SKILLS.md`，只处理有明确远端来源和版本依据的行。
2. 查询远端 tag、release 或 commit；对 monorepo 同时检查登记的子目录。
3. 比较当前内容、登记版本和远端版本，禁止仅凭发布时间判断。
4. 输出 `有更新`、`已是最新`、`无法自动判断`、`来源失效` 四类结果，并列出远端变更中的脚本、hooks、权限和依赖变化。
5. 只报告，不下载覆盖、不修改检查日期。用户明确选择某个 skill 更新后，才进入更新流程。

## 更新

1. 更新前检查 Git 工作区和目标 skill 内容；存在未提交改动或与登记校验不一致时停止。
2. 确认当前稳定基线已经提交并推送到 `origin/main`，未推送成功不得覆盖本地 skill。
3. 将新版本下载到临时目录，完成与安装相同的安全检查并展示差异。
4. 等待用户明确确认具体版本。
5. 只替换该 skill，保留其他目录和软链接；验证失败则恢复旧内容。
6. 验证成功后更新 `SKILLS.md`，为该 skill 创建独立候选提交。
7. 用户确认试用通过后，创建 `stable/<skill>/<version>` 标签并推送提交和标签。

## 标记稳定与回退

用户确认候选版本好用后：

1. 确认候选提交只包含目标 skill 与其 `SKILLS.md` 登记变化。
2. 创建唯一标签 `stable/<skill>/<version>`；标签已存在时禁止移动或覆盖。
3. 推送 `main` 和该标签。

用户要求回退某个 skill 时：

1. 展示该 skill 可用的稳定标签、对应上游版本和提交时间，等待用户指定目标。
2. 从选定标签只恢复 `<skill>/` 目录，不整体恢复旧版 `SKILLS.md`。
3. 只修改 `SKILLS.md` 中该 skill 的版本、状态和检查信息。
4. 重新验证 skill、软链接和脚本；失败则恢复回退前内容。
5. 创建独立的回退提交，保留候选版本和回退操作的完整历史；用户确认后推送。

## 清单约束

- `SKILLS.md` 主表只放已纳管且唯一维护目录真实存在的 skills。
- 来源不明写“待确认”，不得猜测官网、GitHub、版本或兼容性。
- 内容冲突和未纳管项写入“待确认纳管”区，不混入主表。
- 安装或更新必须在同一操作中同步修改清单。
- 不因格式统一而改写其他 skill 的内容。
