#!/usr/bin/env node
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function fail(message) {
  process.stderr.write(`错误：${message}\n`);
  process.exitCode = 1;
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = { command };
  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index];
    if (token === '--dry-run') {
      options.dryRun = true;
      continue;
    }
    if (!token.startsWith('--')) {
      throw new Error(`不支持的参数：${token}`);
    }
    const key = token.slice(2);
    const value = rest[index + 1];
    if (!value || value.startsWith('--')) {
      throw new Error(`参数 ${token} 缺少值`);
    }
    options[key] = value;
    index += 1;
  }
  return options;
}

function parseDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error('--date 必须为 YYYY-MM-DD');
  }
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value) {
    throw new Error(`无效日期：${value}`);
  }
  return date;
}

function formatDate(date) {
  return date.toISOString().slice(0, 10);
}

export function isoWeekInfo(value) {
  const date = typeof value === 'string' ? parseDate(value) : new Date(value.getTime());
  const day = date.getUTCDay() || 7;
  const monday = new Date(date.getTime());
  monday.setUTCDate(date.getUTCDate() - day + 1);
  const sunday = new Date(monday.getTime());
  sunday.setUTCDate(monday.getUTCDate() + 6);
  const thursday = new Date(date.getTime());
  thursday.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((thursday - yearStart) / 86400000) + 1) / 7);
  return {
    week,
    monday: formatDate(monday),
    sunday: formatDate(sunday),
    weekday: WEEKDAYS[date.getUTCDay()],
  };
}

function detectEol(content) {
  return content.includes('\r\n') ? '\r\n' : '\n';
}

function weekHeader(info, weekly) {
  return weekly
    ? `# 第${info.week}周周报（${info.monday} ~ ${info.sunday}）`
    : `# 第${info.week}周（${info.monday} ~ ${info.sunday}）`;
}

function normalizeContent(content, eol) {
  return content.replace(/\r?\n/g, eol).replace(/^[\s\r\n]+|[\s\r\n]+$/g, '');
}

function findWeekBlock(content, info) {
  const pattern = new RegExp(
    `^# 第${info.week}周(?:周报)?（${info.monday} ~ ${info.sunday}）\\r?$`,
    'gm',
  );
  const match = pattern.exec(content);
  if (!match) {
    return undefined;
  }
  const nextHeader = /^# /gm;
  nextHeader.lastIndex = match.index + match[0].length;
  const next = nextHeader.exec(content);
  return {
    start: match.index,
    headingEnd: match.index + match[0].length,
    end: next ? next.index : content.length,
    heading: match[0].replace(/\r$/, ''),
  };
}

function findInsertionPointForWeek(content, info) {
  const headers = [...content.matchAll(/^# 第(\d+)周(?:周报)?（(\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})）\r?$/gm)];
  for (const header of headers) {
    if (header[2] < info.monday) {
      return header.index;
    }
  }
  return content.length;
}

function entryHeader(kind, date, weekday) {
  if (kind === 'review') {
    return `## ${date} 个人复盘`;
  }
  return `## ${date}（${weekday}）`;
}

function findEntry(content, kind, date) {
  const suffix = kind === 'review' ? ' 个人复盘' : '(?:（[^\r\n]*）)?';
  const pattern = new RegExp(`^#{1,2} ${date}${suffix}\\r?$`, 'gm');
  const match = pattern.exec(content);
  if (!match) {
    return undefined;
  }
  const next = /^(?:# |## \d{4}-\d{2}-\d{2})/gm;
  next.lastIndex = match.index + match[0].length;
  const nextMatch = next.exec(content);
  return { start: match.index, end: nextMatch ? nextMatch.index : content.length };
}

function insertIntoWeek(content, block, info, kind, entry, eol) {
  const body = content.slice(block.headingEnd, block.end);
  const entryPattern = /^## (\d{4}-\d{2}-\d{2})(?:（[^\r\n]*）| 个人复盘)\r?$/gm;
  let insertion = block.end;
  let match;
  while ((match = entryPattern.exec(body)) !== null) {
    if (match[1] < formatDate(parseDate(entry.match(/^## (\d{4}-\d{2}-\d{2})/)[1]))) {
      insertion = block.headingEnd + match.index;
      break;
    }
  }
  const prefix = insertion === block.headingEnd ? `${eol}${eol}` : '';
  const suffix = insertion === block.end && !body.endsWith(eol) ? `${eol}` : '';
  return `${content.slice(0, insertion)}${prefix}${entry}${eol}${eol}${suffix}${content.slice(insertion)}`;
}

function replaceRange(content, start, end, replacement, eol) {
  const before = content.slice(0, start).replace(new RegExp(`${eol}{2,}$`), `${eol}${eol}`);
  const after = content.slice(end).replace(new RegExp(`^${eol}{2,}`), `${eol}${eol}`);
  return `${before}${replacement}${eol}${eol}${after}`.replace(new RegExp(`${eol}{3,}`, 'g'), `${eol}${eol}`);
}

export function upsertMonthlyReport({ content, kind, date, body }) {
  if (!['daily', 'weekly', 'review'].includes(kind)) {
    throw new Error('--kind 必须为 daily、weekly 或 review');
  }
  const info = isoWeekInfo(date);
  const eol = detectEol(content);
  const normalizedBody = normalizeContent(body, eol);
  if (!normalizedBody) {
    throw new Error('报告内容不能为空');
  }

  if (kind === 'weekly') {
    return upsertWeekly(content, info, normalizedBody, eol);
  }

  const entry = `${entryHeader(kind, date, info.weekday)}${eol}${eol}${normalizedBody}`;
  const existing = findEntry(content, kind, date);
  if (existing) {
    return replaceRange(content, existing.start, existing.end, entry, eol);
  }

  const block = findWeekBlock(content, info);
  if (block) {
    return insertIntoWeek(content, block, info, kind, entry, eol);
  }

  const heading = weekHeader(info, false);
  const newBlock = `${heading}${eol}${eol}${entry}${eol}`;
  const insertion = findInsertionPointForWeek(content, info);
  const before = content.slice(0, insertion).replace(/[\s\r\n]*$/, '');
  const after = content.slice(insertion).replace(/^[\s\r\n]*/, '');
  return [before, newBlock, after].filter(Boolean).join(`${eol}${eol}`) + eol;
}

function upsertWeekly(content, info, body, eol) {
  const block = findWeekBlock(content, info);
  const heading = weekHeader(info, true);
  if (!block) {
    const newBlock = `${heading}${eol}${eol}${body}${eol}`;
    const insertion = findInsertionPointForWeek(content, info);
    const before = content.slice(0, insertion).replace(/[\s\r\n]*$/, '');
    const after = content.slice(insertion).replace(/^[\s\r\n]*/, '');
    return [before, newBlock, after].filter(Boolean).join(`${eol}${eol}`) + eol;
  }

  const dailyHeader = /^## \d{4}-\d{2}-\d{2}/gm;
  dailyHeader.lastIndex = block.headingEnd;
  const firstDaily = dailyHeader.exec(content.slice(0, block.end));
  const weeklyEnd = firstDaily ? firstDaily.index : block.end;
  const replacement = `${heading}${eol}${eol}${body}`;
  return `${content.slice(0, block.start)}${replacement}${eol}${eol}${content.slice(weeklyEnd)}`;
}

async function renderDiff(file, next) {
  const current = await readFile(file, 'utf8').catch((error) => (error.code === 'ENOENT' ? undefined : Promise.reject(error)));
  if (current === next) {
    process.stdout.write('预览：内容无变化。\n');
    return;
  }
  if (current === undefined) {
    process.stdout.write(`--- ${file}（新建）\n+++ ${file}\n`);
    process.stdout.write(next.split(/\r?\n/).filter(Boolean).map((line) => `+${line}`).join('\n'));
    process.stdout.write('\n');
    return;
  }
  const directory = await mkdtemp(join(tmpdir(), 'daily-report-'));
  const candidate = join(directory, basename(file));
  try {
    await writeFile(candidate, next, 'utf8');
    const diff = spawnSync('git', ['diff', '--no-index', '--no-color', '--', file, candidate], {
      encoding: 'utf8',
    });
    if (diff.stdout) {
      process.stdout.write(diff.stdout.replaceAll(candidate, file));
    } else {
      process.stdout.write(`预览：${file} 的内容已变化，但无法生成 Git diff。\n`);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.command !== 'upsert') {
    throw new Error('用法：monthly-report.mjs upsert --file <月报文件> --kind <daily|weekly|review> --date <YYYY-MM-DD> --content-file <Markdown> [--dry-run]');
  }
  for (const required of ['file', 'kind', 'date', 'content-file']) {
    if (!options[required]) {
      throw new Error(`缺少 --${required}`);
    }
  }
  parseDate(options.date);
  const source = await readFile(options.file, 'utf8').catch((error) => (error.code === 'ENOENT' ? '' : Promise.reject(error)));
  const body = await readFile(options['content-file'], 'utf8');
  const next = upsertMonthlyReport({ content: source, kind: options.kind, date: options.date, body });
  if (options.dryRun) {
    await renderDiff(options.file, next);
    return;
  }
  await mkdir(dirname(options.file), { recursive: true });
  await writeFile(join(dirname(options.file), `.${basename(options.file)}.tmp`), next, 'utf8');
  await rename(join(dirname(options.file), `.${basename(options.file)}.tmp`), options.file);
  process.stdout.write(`已写入：${options.file}\n`);
}

if (process.argv[1] && process.argv[1].endsWith('monthly-report.mjs')) {
  main().catch((error) => fail(error.message));
}
