# 个人配置

配置位置固定为 `<project-root>/docs/report/daily-report.config.yml`。它包含个人身份、群范围和私人目录，不应提交版本库。

## 校验入口

生成任何报告前用脚本校验配置，不要人工逐字段核对：

```bash
node scripts/monthly-report.mjs check-config --file <配置路径>
```

输出 JSON 含 `status`（`ok` / `invalid` / `missing_config`）、`missing`、`warnings`、`hints` 和 `summary`。`status` 非 `ok` 时退出码为 2；把 `hints` 原样转述给用户即可，不要自行编写补救文案。旧格式配置（见下文）计入统计并产生兼容警告，不算缺失。

```yaml
timezone: "Asia/Shanghai"

dingtalk:
  groups:
    - id: "cid_xxx"
      name: "项目沟通群"
  discovery_keywords:
    - "项目名称"

git:
  repos:
    - path: "backend"
      author_emails:
        - "name@example.com"
      author_names:
        - "姓名"
        - "alias"

reports:
  project_dir: "docs/report/daily"
  private_dir: "~/.daily-report/private"
  private_with_daily: true
```

## 字段

- `timezone`：IANA 时区。默认 `Asia/Shanghai`。
- `dingtalk.groups`：正式采集白名单。每项必须有稳定的群 ID；名称只用于显示。
- `dingtalk.discovery_keywords`：仅供 `init` 或刷新群白名单时搜索候选群，运行时不参与筛选。
- `git.repos`：至少配置一个有效 Git 仓库，`path` 相对项目根目录。
- `git.repos[].author_emails`：当前用户的精确 Git 邮箱列表。采集后按提交元数据精确匹配。
- `git.repos[].author_names`：可选别名，仅用于辅助识别和初始化展示，不得替代邮箱匹配。
- `reports.project_dir`：项目日报与周报目录，默认 `docs/report/daily`。
- `reports.private_dir`：个人复盘目录。支持 `~`，由运行环境解析为当前用户主目录。
- `reports.private_with_daily`：默认 `true`。日报确认写入后才生成个人复盘草稿。

## 旧配置兼容

读取旧配置时进行内存兼容，不自动改写历史配置：

- `dingtalk.cached_groups` 转为 `dingtalk.groups`。
- `dingtalk.group_keywords` 转为 `dingtalk.discovery_keywords`。
- `git.repos[].author_email` 转为单元素 `author_emails`。
- 缺少 `timezone`、`project_dir` 或 `private_dir` 时使用默认值。

下一次用户主动初始化或刷新配置时，再写成新结构。
