# 数据采集

所有读取命令使用 JSON 输出和 `-y`。独立来源并行采集；单个 DWS 读取命令可重试一次，仍失败时保留已采集数据并记录覆盖缺失。

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
2. 用 `dws chat message list-all --start "YYYY-MM-DD 00:00:00" --end "YYYY-MM-DD 23:59:59" --limit 50 --cursor "<cursor>" -f json -y` 分页读取。最多 20 页；达到上限时明确提示覆盖不完整。
3. 用 `singleChat`、群白名单和当前用户身份筛选消息。普通日报不读取或使用私聊素材。
4. 同时读取待办：

```bash
dws todo task list --status true --size 50 -f json -y
dws todo task list --status false --size 50 -f json -y
```

仅纳入目标日期创建、完成或修改，且与当前用户相关的待办。

5. 读取当日日程：

```bash
dws calendar event list \
  --start "YYYY-MM-DDT00:00:00+08:00" \
  --end "YYYY-MM-DDT23:59:59+08:00" \
  -f json -y
```

仅将工作相关日程作为“参与/沟通”证据，不把日程本身写成工作完成。

## 个人复盘补充来源

日报确认写入后，才从目标日期的 `list-all` 结果中读取工作相关私聊，以及当前用户的听记：

```bash
dws minutes list mine \
  --start "YYYY-MM-DDT00:00:00+08:00" \
  --end "YYYY-MM-DDT23:59:59+08:00" \
  --max 100 -f json -y
dws minutes get summary --id "<id>" -f json -y
dws minutes get todos --id "<id>" -f json -y
```

仅为与已确认日报相关的沟通补充上下文；不要输出完整私聊、听记原文或无关信息。
