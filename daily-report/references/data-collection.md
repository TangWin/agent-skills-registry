# 数据采集

所有读取命令使用 JSON 输出和 `-y`。独立来源并行采集；单个 DWS 读取命令可重试一次，仍失败时保留已采集数据并记录覆盖缺失。

命令中的日期时间一律按配置的 `timezone` 生成：带偏移量的参数（日历、听记）使用该时区的偏移量；聊天消息命令的时间字符串不带偏移量，DWS 按上海时间解释，配置时区不是上海时先把目标日边界换算成上海时间再传入。

## 能力检查

```bash
node --version
git --version
dws --version
dws auth status -f json -y
dws contact user get-self -f json -y
```

`dws` 不存在或认证失败时，不运行任何 DWS 命令。说明“钉钉数据缺失”，继续 Git 与手工补录。

## Git

对每个配置仓库读取非合并提交，再按 `author_emails` 和作者时间（`%aI`）在本地精确过滤。不要向 `git log` 传 `--since` 或 `--until`：这两个参数按提交者时间筛选，可能遗漏目标日创建、但之后被变基、补提交或合入的工作。

```bash
git -C "<repo>" log --all --no-merges \
  --format="%H%x1f%an%x1f%ae%x1f%s%x1f%aI%x1f%cI"
```

`%aI` 是作者时间，`%cI` 是提交者时间；展示两者差异并按作者时间归属日报。提交只能证明代码变更，不能单独证明测试、发布或业务完成。

## 日报 DWS 来源

1. 验证每个群白名单：`dws chat conversation-info --group "<id>" -f json -y`。
2. 对每个白名单群单独拉取消息，不要用 `list-all` 全量流（会被非白名单会话挤占分页配额）：

```bash
dws chat message list --group "<id>" \
  --time "YYYY-MM-DD 23:59:59" --direction older --limit 50 -f json -y
```

`hasMore=true` 时，用本页最早消息的 `createTime` 作为下一次 `--time` 继续拉取；出现早于目标日 00:00:00 的消息即停止，并丢弃越界消息。单个群最多 20 页；达到上限时明确提示该群覆盖不完整。
3. 按当前用户身份筛选消息。普通日报不读取或使用私聊素材。
4. 同时读取待办：

```bash
dws todo task list --status true --size 50 -f json -y
dws todo task list --status false --size 50 -f json -y
```

仅纳入目标日期创建、完成或修改，且与当前用户相关的待办。

5. 读取当日日程：

```bash
dws calendar event list \
  --start "YYYY-MM-DDT00:00:00<时区偏移>" \
  --end "YYYY-MM-DDT23:59:59<时区偏移>" \
  -f json -y
```

仅将工作相关日程作为“参与/沟通”证据，不把日程本身写成工作完成。

## 个人复盘补充来源

日报确认写入后，才读取工作相关私聊与当前用户的听记。私聊没有按会话过滤的读取方式，用 `list-all` 自动翻页拉取目标日期全量消息流，再从中筛选工作相关私聊：

```bash
dws chat message list-all \
  --start "YYYY-MM-DD 00:00:00" --end "YYYY-MM-DD 23:59:59" \
  --limit 50 --page-all --page-limit 20 --max-items 1000 -f json -y
```

CLI 按 `--page-all` 自动翻页并合并结果；达到 `--page-limit` 或 `--max-items` 上限时明确提示私聊覆盖不完整。听记：

```bash
dws minutes list mine \
  --start "YYYY-MM-DDT00:00:00<时区偏移>" \
  --end "YYYY-MM-DDT23:59:59<时区偏移>" \
  --max 100 -f json -y
dws minutes get summary --id "<id>" -f json -y
dws minutes get todos --id "<id>" -f json -y
```

仅为与已确认日报相关的沟通补充上下文；不要输出完整私聊、听记原文或无关信息。
