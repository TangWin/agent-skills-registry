import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { checkConfig, isoWeekInfo, upsertMonthlyReport } from './monthly-report.mjs';

const SCRIPT_PATH = fileURLToPath(new URL('./monthly-report.mjs', import.meta.url));

test('calculates ISO week and Chinese weekday in UTC', () => {
  assert.deepEqual(isoWeekInfo('2026-06-24'), {
    week: 26,
    monday: '2026-06-22',
    sunday: '2026-06-28',
    weekday: '周三',
  });
});

test('creates a daily entry inside its ISO week', () => {
  const result = upsertMonthlyReport({
    content: '',
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. 修复订单价格问题',
  });
  assert.match(result, /# 第26周（2026-06-22 ~ 2026-06-28）/);
  assert.match(result, /## 2026-06-24（周三）/);
});

test('replaces the same daily entry without duplicating it', () => {
  const initial = upsertMonthlyReport({
    content: '',
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. 旧内容',
  });
  const result = upsertMonthlyReport({
    content: initial,
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. 新内容',
  });
  assert.equal((result.match(/## 2026-06-24/g) || []).length, 1);
  assert.match(result, /新内容/);
  assert.doesNotMatch(result, /旧内容/);
});

test('places weekly content before daily entries in the same week', () => {
  const daily = upsertMonthlyReport({
    content: '',
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. 日报内容',
  });
  const result = upsertMonthlyReport({
    content: daily,
    kind: 'weekly',
    date: '2026-06-24',
    body: '### 本周完成\n\n1. 周报内容',
  });
  assert.match(result, /# 第26周周报（2026-06-22 ~ 2026-06-28）/);
  assert.ok(result.indexOf('周报内容') < result.indexOf('日报内容'));
});

test('keeps unrelated legacy text while inserting a new week', () => {
  const legacy = '# 历史记录\n\n旧内容\n';
  const result = upsertMonthlyReport({
    content: legacy,
    kind: 'review',
    date: '2026-06-24',
    body: '### 今日最有价值推进\n\n完成排查',
  });
  assert.match(result, /# 历史记录\n\n旧内容/);
  assert.match(result, /## 2026-06-24 个人复盘/);
});

test('preserves CRLF line endings when updating a Windows report', () => {
  const initial = '# 第26周（2026-06-22 ~ 2026-06-28）\r\n\r\n## 2026-06-24（周三）\r\n\r\n旧内容\r\n';
  const result = upsertMonthlyReport({
    content: initial,
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. Windows 内容',
  });
  assert.match(result, /Windows 内容\r\n/);
  assert.equal(result.replace(/\r\n/g, '').includes('\n'), false);
});

test('inserts a monthly summary at the top and replaces it on rerun', () => {
  const daily = upsertMonthlyReport({
    content: '',
    kind: 'daily',
    date: '2026-06-24',
    body: '### 已完成\n\n1. 日报内容',
  });
  const withMonthly = upsertMonthlyReport({
    content: daily,
    kind: 'monthly',
    date: '2026-06-24',
    body: '### 本月概览\n\n旧总结',
  });
  assert.match(withMonthly, /# 月度总结（2026-06）/);
  assert.ok(withMonthly.indexOf('旧总结') < withMonthly.indexOf('日报内容'));
  const replaced = upsertMonthlyReport({
    content: withMonthly,
    kind: 'monthly',
    date: '2026-06-24',
    body: '### 本月概览\n\n新总结',
  });
  assert.equal((replaced.match(/# 月度总结/g) || []).length, 1);
  assert.match(replaced, /新总结/);
  assert.doesNotMatch(replaced, /旧总结/);
  assert.match(replaced, /日报内容/);
});

test('checkConfig reports ok for a complete config', () => {
  const config = [
    'timezone: "Asia/Shanghai"',
    '',
    'dingtalk:',
    '  groups:',
    '    - id: "cid_xxx"',
    '      name: "项目群"',
    '',
    'git:',
    '  repos:',
    '    - path: "backend"',
    '      author_emails:',
    '        - "me@example.com"',
    '',
    'reports:',
    '  project_dir: "docs/report/daily"',
    '  private_dir: "~/.daily-report/private"',
  ].join('\n');
  const result = checkConfig(config);
  assert.equal(result.status, 'ok');
  assert.deepEqual(result.missing, []);
  assert.equal(result.summary.groups, 1);
  assert.equal(result.summary.repos, 1);
});

test('checkConfig flags missing evidence source and author emails with hints', () => {
  const empty = checkConfig('timezone: "Asia/Shanghai"\n');
  assert.equal(empty.status, 'invalid');
  assert.ok(empty.missing.includes('evidence_source'));
  assert.ok(empty.hints.evidence_source.length > 0);

  const noEmail = checkConfig('git:\n  repos:\n    - path: "backend"\n');
  assert.equal(noEmail.status, 'invalid');
  assert.ok(noEmail.missing.includes('git.author_emails'));
  assert.ok(noEmail.warnings.some((item) => item.includes('钉钉群')));
});

test('checkConfig treats legacy fields as compatible with a warning', () => {
  const legacy = [
    'dingtalk:',
    '  group_keywords: ["项目"]',
    '  cached_groups:',
    '    cid_old==: "旧群"',
    'git:',
    '  repos:',
    '    - path: "backend"',
    '      author_email: "me@example.com"',
  ].join('\n');
  const result = checkConfig(legacy);
  assert.equal(result.status, 'ok');
  assert.equal(result.summary.groups, 1);
  assert.ok(result.warnings.some((item) => item.includes('旧字段')));
});

test('CLI can create, replace and dry-run a monthly file', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'daily-report-cli-'));
  const report = join(directory, 'daily', '2026-06.md');
  const draft = join(directory, 'draft.md');
  try {
    await writeFile(draft, '### 已完成\n\n1. 新内容\n', 'utf8');
    const create = spawnSync(process.execPath, [
      SCRIPT_PATH,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft,
    ], { encoding: 'utf8' });
    assert.equal(create.status, 0, create.stderr);
    const unchanged = spawnSync(process.execPath, [
      SCRIPT_PATH,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft, '--dry-run',
    ], { encoding: 'utf8' });
    assert.equal(unchanged.status, 0, unchanged.stderr);
    assert.match(unchanged.stdout, /内容无变化/);
    await writeFile(draft, '### 已完成\n\n1. 替换内容\n', 'utf8');
    const dryRun = spawnSync(process.execPath, [
      SCRIPT_PATH,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft, '--dry-run',
    ], { encoding: 'utf8' });
    assert.equal(dryRun.status, 0, dryRun.stderr);
    assert.match(dryRun.stdout, /替换内容/);
    assert.match(await readFile(report, 'utf8'), /新内容/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
