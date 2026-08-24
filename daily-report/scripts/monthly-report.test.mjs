import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { isoWeekInfo, upsertMonthlyReport } from './monthly-report.mjs';

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

test('CLI can create, replace and dry-run a monthly file', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'daily-report-cli-'));
  const report = join(directory, 'daily', '2026-06.md');
  const draft = join(directory, 'draft.md');
  try {
    await writeFile(draft, '### 已完成\n\n1. 新内容\n', 'utf8');
    const create = spawnSync(process.execPath, [
      new URL('./monthly-report.mjs', import.meta.url).pathname,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft,
    ], { encoding: 'utf8' });
    assert.equal(create.status, 0, create.stderr);
    const unchanged = spawnSync(process.execPath, [
      new URL('./monthly-report.mjs', import.meta.url).pathname,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft, '--dry-run',
    ], { encoding: 'utf8' });
    assert.equal(unchanged.status, 0, unchanged.stderr);
    assert.match(unchanged.stdout, /内容无变化/);
    await writeFile(draft, '### 已完成\n\n1. 替换内容\n', 'utf8');
    const dryRun = spawnSync(process.execPath, [
      new URL('./monthly-report.mjs', import.meta.url).pathname,
      'upsert', '--file', report, '--kind', 'daily', '--date', '2026-06-24', '--content-file', draft, '--dry-run',
    ], { encoding: 'utf8' });
    assert.equal(dryRun.status, 0, dryRun.stderr);
    assert.match(dryRun.stdout, /替换内容/);
    assert.match(await readFile(report, 'utf8'), /新内容/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
