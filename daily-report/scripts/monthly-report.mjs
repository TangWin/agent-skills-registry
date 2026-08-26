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

function monthHeader(month) {
  return `# 月度总结（${month}）`;
}

function findMonthBlock(content, month) {
  const pattern = new RegExp(`^# 月度总结（${month}）\\r?$`, 'gm');
  const match = pattern.exec(content);
  if (!match) {
    return undefined;
  }
  const nextHeader = /^# /gm;
  nextHeader.lastIndex = match.index + match[0].length;
  const next = nextHeader.exec(content);
  return { start: match.index, end: next ? next.index : content.length };
}

function upsertMonthly(content, month, body, eol) {
  const replacement = `${monthHeader(month)}${eol}${eol}${body}`;
  const block = findMonthBlock(content, month);
  if (block) {
    return replaceRange(content, block.start, block.end, replacement, eol);
  }
  const rest = content.replace(/^[\s\r\n]*/, '');
  if (!rest) {
    return `${replacement}${eol}`;
  }
  return `${replacement}${eol}${eol}${rest}`;
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
  if (!['daily', 'weekly', 'review', 'monthly'].includes(kind)) {
    throw new Error('--kind 必须为 daily、weekly、review 或 monthly');
  }
  const info = isoWeekInfo(date);
  const eol = detectEol(content);
  const normalizedBody = normalizeContent(body, eol);
  if (!normalizedBody) {
    throw new Error('报告内容不能为空');
  }

  if (kind === 'monthly') {
    return upsertMonthly(content, date.slice(0, 7), normalizedBody, eol);
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

export function checkConfig(content) {
  const lines = content.split(/\r?\n/);
  let section = '';
  let dingtalkSubkey = '';
  const found = {
    timezone: false,
    groups: 0,
    repos: 0,
    emailLists: 0,
    projectDir: false,
    privateDir: false,
    legacyGroups: false,
    legacyEmail: false,
  };
  for (const raw of lines) {
    if (!raw.trim() || /^\s*#/.test(raw)) {
      continue;
    }
    if (/^[A-Za-z_]+:/.test(raw)) {
      section = raw.split(':')[0].trim();
    }
    if (/^timezone:\s*\S/.test(raw)) {
      found.timezone = true;
    }
    if (section === 'dingtalk') {
      const subkey = raw.match(/^\s{2}([A-Za-z_]+):/);
      if (subkey) {
        dingtalkSubkey = subkey[1];
      }
      if (/^\s*-\s*id:\s*\S/.test(raw)) {
        found.groups += 1;
      }
      if (/^\s*cached_groups:/.test(raw)) {
        found.legacyGroups = true;
      }
      if (dingtalkSubkey === 'cached_groups' && /^\s{4,}[^\s#-].*:\s*\S/.test(raw)) {
        found.groups += 1;
      }
    }
    if (section === 'git') {
      if (/^\s*-\s*path:\s*\S/.test(raw)) {
        found.repos += 1;
      }
      if (/^\s*author_emails:/.test(raw)) {
        found.emailLists += 1;
      }
      if (/^\s*author_email:\s*\S/.test(raw)) {
        found.emailLists += 1;
        found.legacyEmail = true;
      }
    }
    if (section === 'reports') {
      if (/^\s*project_dir:\s*\S/.test(raw)) {
        found.projectDir = true;
      }
      if (/^\s*private_dir:\s*\S/.test(raw)) {
        found.privateDir = true;
      }
    }
  }

  const missing = [];
  const warnings = [];
  const hints = {};
  if (found.repos === 0 && found.groups === 0) {
    missing.push('evidence_source');
    hints.evidence_source = '至少配置一个证据来源： git.repos 填仓库相对路径，或 dingtalk.groups 填群 ID 白名单。可直接说“初始化日报配置”进入引导。';
  }
  if (found.repos > 0 && found.emailLists === 0) {
    missing.push('git.author_emails');
    hints['git.author_emails'] = '已配置 Git 仓库但缺少 author_emails，无法归因提交。在每个仓库下运行 git config user.email 确认后填入精确邮箱列表。';
  }
  if (!found.timezone) {
    warnings.push('timezone 未配置，使用默认 Asia/Shanghai。');
  }
  if (found.repos > 0 && found.groups === 0) {
    warnings.push('未配置钉钉群白名单，日报将只有 Git 证据。');
  }
  if (found.groups > 0 && found.repos === 0) {
    warnings.push('未配置 Git 仓库，日报将只有钉钉证据。');
  }
  if (!found.projectDir) {
    warnings.push('reports.project_dir 未配置，使用默认 docs/report/daily。');
  }
  if (!found.privateDir) {
    warnings.push('reports.private_dir 未配置，使用默认 ~/.daily-report/private。');
  }
  if (found.legacyGroups || found.legacyEmail) {
    warnings.push('检测到旧字段（cached_groups 或 author_email），本次按内存兼容规则读取；下次初始化时再写成新结构。');
  }
  return {
    status: missing.length ? 'invalid' : 'ok',
    missing,
    warnings,
    hints,
    summary: {
      groups: found.groups,
      repos: found.repos,
      timezone_configured: found.timezone,
    },
  };
}

async function runCheckConfig(options) {
  if (!options.file) {
    throw new Error('缺少 --file');
  }
  const content = await readFile(options.file, 'utf8').catch((error) => {
    if (error.code === 'ENOENT') {
      return undefined;
    }
    return Promise.reject(error);
  });
  const result = content === undefined
    ? {
        status: 'missing_config',
        missing: ['config_file'],
        warnings: [],
        hints: { config_file: '配置文件不存在。进入 init 模式引导用户创建 docs/report/daily-report.config.yml。' },
      }
    : checkConfig(content);
  result.config_file = options.file;
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (result.status !== 'ok') {
    process.exitCode = 2;
  }
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
  if (options.command === 'check-config') {
    await runCheckConfig(options);
    return;
  }
  if (options.command !== 'upsert') {
    throw new Error('用法：monthly-report.mjs upsert --file <月报文件> --kind <daily|weekly|review|monthly> --date <YYYY-MM-DD> --content-file <Markdown> [--dry-run]，或 monthly-report.mjs check-config --file <配置文件>');
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
