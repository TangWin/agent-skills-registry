#!/usr/bin/env python3
"""发布后的本地 Git 收尾。仅依赖 Python 3 标准库和 Git；默认只生成计划。"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile

PROTECTED = {'master', 'main', 'dev', 'pre', 'test'}
FEATURES = ('feature/', 'fix/', 'hotfix/')


class Stop(Exception):
    pass


def git(repo, *args, check=True):
    env = dict(os.environ, LC_ALL='C')
    p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, env=env)
    if check and p.returncode:
        raise Stop('Git 操作失败: ' + ' '.join(args[:2]) + ': ' + p.stderr.decode(errors='replace').strip())
    return p


def oid(repo, ref):
    p = git(repo, 'show-ref', '--verify', '--hash', ref, check=False)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def worktrees(repo):
    result = []
    for block in git(repo, 'worktree', 'list', '--porcelain', '-z').stdout.split(b'\0\0'):
        if not block.strip(b'\0'):
            continue
        record = {}
        for raw in block.split(b'\0'):
            if raw:
                k, _, v = os.fsdecode(raw).partition(' ')
                record[k] = v
        result.append(record)
    return result


def safe_path(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise Stop('路径越界')
    out = root
    for part in rel.parts:
        out = out / part
        if out.is_symlink():
            raise Stop('存在符号链接，保留并人工检查: ' + relative)
        if out.exists() and out != root / rel and not out.is_dir():
            raise Stop('目标父路径不是目录: ' + relative)
    return out


def fingerprint(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise Stop('存在特殊文件或嵌套仓库，保留: ' + str(path.name))
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {'sha256': h.hexdigest(), 'size': s.st_size, 'mode': stat.S_IMODE(s.st_mode)}


def clean(repo):
    # Git diff 会隐藏 assume-unchanged / skip-worktree 的实际修改，不能据此删除。
    entries = git(repo, 'ls-files', '-v', '-z').stdout.split(b'\0')
    if any(x and (x[:1].islower() or x.startswith(b'S ')) for x in entries):
        return False
    return (git(repo, 'diff', '--quiet', check=False).returncode == 0
            and git(repo, 'diff', '--cached', '--quiet', check=False).returncode == 0
            and not git(repo, 'ls-files', '-u').stdout)


def route(relative):
    p = Path(relative).parts
    if len(p) >= 3 and p[:2] == ('ai_work', 'docs'):
        return len(p) == 5 and p[-1] in {'需求.md', '实现方案.md', '变更记录.md'}
    if len(p) >= 4 and p[:2] in {('ai_work', 'scripts'), ('ai_work', 'resources')}:
        return p[2] not in {'reports', '文档归档'}
    return len(p) == 2 and p[0] == 'db' and p[1].endswith('.sql')


def inventory(repo):
    tracked = git(repo, 'ls-files', '--stage', '-z').stdout.split(b'\0')
    if any(x.startswith(b'160000 ') for x in tracked):
        raise Stop('含 submodule，保留 worktree')
    poms = set()
    for entry in tracked:
        if b'\t' in entry:
            name = os.fsdecode(entry.split(b'\t', 1)[1])
            if Path(name).name == 'pom.xml':
                poms.add(str(Path(name).parent))
    normal = {os.fsdecode(x) for x in git(repo, 'ls-files', '--others', '--exclude-standard', '-z').stdout.split(b'\0') if x}
    ignored = {os.fsdecode(x) for x in git(repo, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').stdout.split(b'\0') if x}
    files, builds = {}, 0
    for rel in sorted(normal | ignored):
        path = safe_path(repo, rel)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise Stop('存在特殊文件或嵌套仓库，保留: ' + rel)
        parts = Path(rel).parts
        # 只排除具有已跟踪 pom.xml 的模块默认 target，支持多级 Maven 模块。
        if any(part == 'target' and str(Path(*parts[:i])) in poms for i, part in enumerate(parts)):
            builds += 1
            continue
        if rel in normal and not rel.startswith('ai_work/') and not route(rel):
            raise Stop('存在未提交的非文档文件，保留: ' + rel)
        files[rel] = fingerprint(path)
    return files, builds


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name + '.', delete=False) as f:
        temp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


@contextlib.contextmanager
def lock(repo):
    common = Path(os.fsdecode(git(repo, 'rev-parse', '--git-common-dir').stdout).strip())
    if not common.is_absolute():
        common = repo / common
    with (common / 'release-cleanup.lock').open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('另一个收尾操作正在运行')
        yield


def state_path(raw):
    p = Path(os.path.abspath(raw))
    if p.is_symlink():
        raise Stop('状态目录不能是符号链接')
    # 规范化 macOS 的 /var -> /private/var 等系统路径；内部文件另行拒绝链接。
    return p.resolve()


def summary(plan, journal=None):
    states = (journal or {}).get('items', {})
    return {'base': plan['base_sha'], 'items': [
        {'branch': x['branch'], 'status': states.get(x['id'], {}).get('phase', 'planned'),
         'blocked': states.get(x['id'], {}).get('error') or x['blocked'],
         'files': len(x['files']), 'migrate': sum(route(f) for f in x['files']),
         'archive_only': sum(not route(f) for f in x['files']),
         'pending_sql': sum(f.startswith('db/') and route(f) for f in x['files']),
         'build_files': x['build_files']} for x in plan['items']]}


def plan_command(args):
    repo = Path(args.repo).resolve()
    trees = worktrees(repo)
    if not trees or Path(trees[0]['worktree']).resolve() != repo:
        raise Stop('--repo 必须是主检出目录')
    ref = 'refs/heads/' + args.base
    base = oid(repo, ref)
    if not base:
        raise Stop('本地主分支不存在')
    state = state_path(args.state)
    if state == repo or any(state == Path(t['worktree']) or Path(t['worktree']) in state.parents for t in trees[1:]):
        raise Stop('状态目录不能放在待清理 worktree 中')
    with lock(repo):
        if state.exists() and any(state.iterdir()):
            raise Stop('状态目录非空；续跑使用 apply，重新盘点使用新目录')
        names = args.branch or [x for x in git(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads').stdout.decode().splitlines() if x.startswith(FEATURES)]
        items = []
        for name in dict.fromkeys(names):
            if name in PROTECTED or name == args.base:
                raise Stop('长期分支禁止清理: ' + name)
            branch_ref = 'refs/heads/' + name
            head = oid(repo, branch_ref)
            if not head:
                raise Stop('本地分支不存在: ' + name)
            occupied = [t for t in trees if t.get('branch') == branch_ref]
            blocked, files, builds, path = [], {}, 0, None
            if git(repo, 'merge-base', '--is-ancestor', head, base, check=False).returncode:
                blocked.append('未完整合入固定主分支')
            if len(occupied) > 1:
                blocked.append('分支被多个 worktree 占用')
            if occupied:
                t = occupied[0]
                path = t['worktree']
                if Path(path).resolve() == repo or Path(path).parent != repo.parent:
                    blocked.append('主目录或非同级 worktree；需人工或管理工具处理')
                if 'locked' in t or 'prunable' in t or not Path(path).is_dir():
                    blocked.append('worktree 锁定或失效')
                if not blocked:
                    if not clean(Path(path)):
                        blocked.append('存在暂存、源码改动、冲突或 assume-unchanged/skip-worktree 索引标记')
                    else:
                        try:
                            files, builds = inventory(Path(path))
                        except Stop as e:
                            blocked.append(str(e))
            item_id = hashlib.sha256(name.encode()).hexdigest()[:16]
            items.append({'id': item_id, 'branch': name, 'head': head, 'path': path,
                          'blocked': blocked, 'files': files, 'build_files': builds})
        value = {'version': 1, 'repo': str(repo), 'base_ref': ref, 'base_sha': base, 'items': items}
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(state, 0o700)
        save(state / 'plan.json', value)
        print(json.dumps(summary(value), ensure_ascii=False))


def check_base(plan):
    if oid(Path(plan['repo']), plan['base_ref']) != plan['base_sha']:
        raise Stop('主分支已变化，请重新盘点')


def check_source(plan, item):
    repo = Path(plan['repo'])
    check_base(plan)
    if oid(repo, 'refs/heads/' + item['branch']) != item['head']:
        raise Stop('功能分支已变化，请重新盘点')
    attached = [t for t in worktrees(repo) if t.get('branch') == 'refs/heads/' + item['branch']]
    if item['path']:
        if len(attached) != 1 or attached[0]['worktree'] != item['path'] or 'locked' in attached[0] or 'prunable' in attached[0]:
            raise Stop('worktree 占用或锁定状态已变化')
        path = Path(item['path'])
        if os.fsdecode(git(path, 'rev-parse', 'HEAD').stdout).strip() != item['head'] or not clean(path):
            raise Stop('worktree HEAD、暂存或源码已变化')
        current, _ = inventory(path)
        if current != item['files']:
            raise Stop('本地文件已变化，请重新盘点')
    elif attached:
        raise Stop('分支新增 worktree 占用')


def archive_path(state, item):
    return state / (item['id'] + '.tar.gz')


def verify_archive(state, item):
    path = archive_path(state, item)
    safe_path(state, path.name)
    entries = json.loads(safe_path(state, item['id'] + '.backup.json').read_text(encoding='utf-8'))
    source_entries = {name[7:]: meta for name, meta in entries.items() if name.startswith('source/')}
    if source_entries != item['files']:
        raise Stop('归档清单与原计划不符')
    with tarfile.open(path, 'r:gz') as tar:
        members = {m.name: m for m in tar.getmembers()}
        if len(members) != len(tar.getmembers()) or set(members) != set(entries):
            raise Stop('归档路径清单不符或含重名项')
        for rel, meta in entries.items():
            m = members[rel]
            if not m or not m.isfile() or m.size != meta['size'] or m.mode != meta['mode']:
                raise Stop('归档文件信息不符: ' + rel)
            h = hashlib.sha256()
            with tar.extractfile(m) as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != meta['sha256']:
                raise Stop('归档校验失败: ' + rel)


def backup(state, plan, item):
    out = archive_path(state, item)
    if out.exists():
        verify_archive(state, item)
        return
    entries = {}
    # 不提取归档。目录 0700、文件 0600，避免本地配置内容泄露。
    with tempfile.NamedTemporaryFile(dir=state, prefix=item['id'] + '.backup-', delete=False) as raw:
        temporary = Path(raw.name)
        # 来源已拒绝符号链接；将常规硬链接按独立内容归档，避免 tar LNKTYPE 无法回读。
        with tarfile.open(fileobj=raw, mode='w:gz', dereference=True) as tar:
            if item['path']:
                for rel in item['files']:
                    entries['source/' + rel] = item['files'][rel]
                    tar.add(safe_path(Path(item['path']), rel), arcname='source/' + rel, recursive=False)
                    if route(rel):
                        dest = safe_path(Path(plan['repo']), rel)
                        if dest.exists():
                            entries['destination/' + rel] = fingerprint(dest)
                            tar.add(dest, arcname='destination/' + rel, recursive=False)
        raw.flush()
        os.fsync(raw.fileno())
    save(state / (item['id'] + '.backup.json'), entries)
    os.replace(temporary, out)
    verify_archive(state, item)


def destination_ok(repo, rel, source, resolutions):
    p = safe_path(repo, rel)
    if not p.exists():
        return False
    current = fingerprint(p)
    return current == source or current == resolutions.get(rel)


def migrate(state, plan, item, progress):
    repo = Path(plan['repo'])
    for rel, meta in item['files'].items():
        if not route(rel):
            continue
        dest = safe_path(repo, rel)
        if dest.exists():
            if not destination_ok(repo, rel, meta, progress.get('resolutions', {})):
                raise Stop('文档或 SQL 冲突: ' + rel + '；保留来源，查看该项归档后解决')
            continue
        if (git(repo, 'ls-files', '--error-unmatch', '--', rel, check=False).returncode == 0
                or git(repo, 'cat-file', '-e', 'HEAD:' + rel, check=False).returncode == 0):
            raise Stop('主目录已跟踪文件被删除，保留用户改动: ' + rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 先在私有状态目录完整写入，再以 hard link 原子地创建目标；目标存在时拒绝覆盖。
        # 中途退出只会留下 STATE 内的临时文件，主文档不会出现半份内容。
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=state, prefix=item['id'] + '.migration-', delete=False) as out:
                temporary = Path(out.name)
                with tarfile.open(archive_path(state, item), 'r:gz') as tar:
                    with tar.extractfile('source/' + rel) as src:
                        for block in iter(lambda: src.read(1024 * 1024), b''):
                            out.write(block)
                out.flush()
                os.fchmod(out.fileno(), meta['mode'])
                os.fsync(out.fileno())
            if fingerprint(temporary) != meta:
                raise Stop('迁移暂存校验失败: ' + rel)
            os.link(temporary, dest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if fingerprint(dest) != meta:
            raise Stop('迁移校验失败: ' + rel)


def check_migration(plan, item, progress):
    for rel, meta in item['files'].items():
        if route(rel) and not destination_ok(Path(plan['repo']), rel, meta, progress.get('resolutions', {})):
            raise Stop('迁回文件缺失或发生变更: ' + rel)


def finish_item(state, plan, item, progress, persist):
    repo = Path(plan['repo'])
    if item['blocked']:
        raise Stop('；'.join(item['blocked']))
    if progress['phase'] == 'done':
        if oid(repo, 'refs/heads/' + item['branch']) or (item['path'] and Path(item['path']).exists()):
            raise Stop('已完成项出现同名新分支或目录，保留')
        verify_archive(state, item)
        check_migration(plan, item, progress)
        return
    check_base(plan)
    present = item['path'] and Path(item['path']).exists()
    if progress['phase'] == 'worktree_removed':
        if present or any(t.get('branch') == 'refs/heads/' + item['branch'] for t in worktrees(repo)):
            raise Stop('删除后的 worktree 重新出现，保留')
    elif not item['path'] or present:
        check_source(plan, item)
    elif progress['phase'] not in {'migrated', 'worktree_removed'}:
        raise Stop('来源提前消失，不能推断清理成功')
    if progress['phase'] == 'pending':
        backup(state, plan, item)
        progress['phase'] = 'backed_up'
        persist()
    verify_archive(state, item)
    if progress['phase'] in {'backed_up', 'migrated'}:
        migrate(state, plan, item, progress)
        check_migration(plan, item, progress)
        progress['phase'] = 'migrated'
        persist()
    if present:
        check_source(plan, item)
        check_migration(plan, item, progress)
        # 所有非构建文件已有校验归档，tracked 状态干净，才允许移除 ignored/untracked 文件。
        p = git(repo, 'worktree', 'remove', item['path'], check=False)
        if p.returncode:
            check_source(plan, item)
            git(repo, 'worktree', 'remove', '--force', item['path'])
    progress['phase'] = 'worktree_removed'
    persist()
    check_migration(plan, item, progress)
    check_base(plan)
    attached = [t for t in worktrees(repo) if t.get('branch') == 'refs/heads/' + item['branch']]
    if attached:
        raise Stop('删除分支前发现 worktree 占用')
    head = oid(repo, 'refs/heads/' + item['branch'])
    if head:
        if head != item['head'] or git(repo, 'merge-base', '--is-ancestor', head, plan['base_sha'], check=False).returncode:
            raise Stop('分支不再满足删除条件')
        p = git(repo, 'branch', '-d', '--', item['branch'], check=False)
        if p.returncode:
            if b'not fully merged' not in p.stderr:
                raise Stop('分支删除失败，保留: ' + p.stderr.decode(errors='replace').strip())
            check_base(plan)
            if oid(repo, 'refs/heads/' + item['branch']) != head or any(t.get('branch') == 'refs/heads/' + item['branch'] for t in worktrees(repo)):
                raise Stop('强制删除前分支或占用已变化')
            # -d 参考 upstream/当前 HEAD；这里依据已再次验证的固定发布主分支执行。
            git(repo, 'branch', '-D', '--', item['branch'])
    if oid(repo, 'refs/heads/' + item['branch']) or (item['path'] and Path(item['path']).exists()):
        raise Stop('清理后验收失败')
    progress['phase'] = 'done'
    persist()


def load_state(args):
    state = state_path(args.state)
    plan = json.loads(safe_path(state, 'plan.json').read_text(encoding='utf-8'))
    if plan.get('version') != 1:
        raise Stop('不支持的计划版本')
    journal_file = safe_path(state, 'journal.json')
    journal = json.loads(journal_file.read_text(encoding='utf-8')) if journal_file.exists() else {'items': {}}
    return state, plan, journal


def apply_command(args):
    if not args.released.strip():
        raise Stop('缺少明确发布确认')
    state, plan, journal = load_state(args)
    with lock(Path(plan['repo'])):
        # 获取仓库互斥锁后重读，避免并行 apply 覆盖进度。
        state, plan, journal = load_state(args)
        journal['released'] = args.released
        def persist():
            save(state / 'journal.json', journal)
        for item in plan['items']:
            progress = journal['items'].setdefault(item['id'], {'phase': 'pending'})
            progress.pop('error', None)
            try:
                finish_item(state, plan, item, progress, persist)
            except (Stop, OSError, tarfile.TarError, EOFError) as e:
                progress['error'] = str(e)
            persist()
        print(json.dumps(summary(plan, journal), ensure_ascii=False))
        return int(any(p.get('error') for p in journal['items'].values()))


def resolve_command(args):
    state, plan, journal = load_state(args)
    with lock(Path(plan['repo'])):
        state, plan, journal = load_state(args)
        item = next((x for x in plan['items'] if x['branch'] == args.branch), None)
        if not item or args.file not in item['files'] or not route(args.file):
            raise Stop('只能确认计划内的文档或 SQL 冲突')
        progress = journal['items'].get(item['id'], {})
        if progress.get('phase') != 'backed_up':
            raise Stop('先 apply 生成并校验归档，再处理冲突')
        check_source(plan, item)
        verify_archive(state, item)
        dest = safe_path(Path(plan['repo']), args.file)
        progress.setdefault('resolutions', {})[args.file] = fingerprint(dest)
        save(state / 'journal.json', journal)
        print(json.dumps({'resolved': args.file}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--repo', required=True)
    p.add_argument('--base', default='master')
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument('--branch', action='append')
    select.add_argument('--all-features', action='store_true')
    p.add_argument('--state', required=True)
    a = sub.add_parser('apply')
    a.add_argument('--state', required=True)
    a.add_argument('--released', required=True)
    r = sub.add_parser('resolve')
    r.add_argument('--state', required=True)
    r.add_argument('--branch', required=True)
    r.add_argument('--file', required=True)
    args = parser.parse_args()
    try:
        return {'plan': plan_command, 'apply': apply_command, 'resolve': resolve_command}[args.command](args) or 0
    except (Stop, OSError, ValueError, KeyError) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
