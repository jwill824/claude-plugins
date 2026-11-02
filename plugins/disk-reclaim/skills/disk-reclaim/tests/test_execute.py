#!/usr/bin/env python3
"""Tests for execute_system.py against a throwaway home folder. Nothing outside the temp directory is touched.

    python3 -B tests/test_execute.py
"""
import json, os, shutil, subprocess, sys, tempfile, time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'execute_system.py')
HOME = tempfile.mkdtemp(prefix='exec-test-')
CACHES = os.path.join(HOME, 'Library', 'Caches')
os.makedirs(CACHES)
ENV = dict(os.environ, HOME=HOME)
FAILS, N = [], [0]


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {detail}'))
    if not cond:
        FAILS.append(name)


def du(p):
    return int(subprocess.run(['du', '-sk', p], capture_output=True, text=True).stdout.split()[0])


def make(name, files=None, age=60):
    """A cache folder with old files; the folder's own mtime is aged last so a later write is detectable."""
    p = os.path.join(CACHES, name)
    os.makedirs(p)
    for f in (files or {'blob.bin': 'x' * 200_000}):
        fp = os.path.join(p, f)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'w').write((files or {'blob.bin': 'x' * 200_000})[f])
        os.utime(fp, (time.time() - age * 86400,) * 2)
    os.utime(p, (time.time() - age * 86400,) * 2)
    return p


def item(dirpath, name=None, **over):
    N[0] += 1
    it = {'id': f'{N[0]:03d}-{name or os.path.basename(dirpath)}', 'label': name or os.path.basename(dirpath), 'group': 'App caches', 'tier': 'SAFE',
          'bucket': 'SAFE-STALE', 'blocked': None, 'path': dirpath, 'probe': None, 'kb': du(dirpath) if os.path.exists(dirpath) else 0,
          'cmds': [f'rm -rf -- {dirpath}'], 'order': 0, 'evidence': [], 'gates': [],
          'fingerprint': {'mtime': int(os.stat(dirpath).st_mtime), 'kb': du(dirpath)} if os.path.exists(dirpath) else None}
    it.update(over)
    return it


def execute(items, *args, execute=True, age_minutes=0):
    f = os.path.join(HOME, 'scan.json')
    json.dump({'items': items, 'thresholds': {'active_days': 7, 'stale_days': 30}}, open(f, 'w'))
    if age_minutes:
        os.utime(f, (time.time() - age_minutes * 60,) * 2)
    log = os.path.join(HOME, 'log.tsv')
    cmd = [sys.executable, '-B', SCRIPT, f, '--log', log, '--lsof-timeout', '60'] + (['--execute'] if execute else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=300)
    rows = {}
    if execute and os.path.exists(log):
        for line in open(log).read().splitlines()[1:]:
            c = line.split('\t')
            rows[c[0]] = {'status': c[3], 'reason': c[4], 'du_after': int(c[7])}
    return r, rows


def status(rows, it):
    return rows.get(it['id'], {}).get('status')


# 1 dry run changes nothing
a = item(make('dry'))
r, _ = execute([a], execute=False)
check('dry run reports would-run and removes nothing', 'would-run' in r.stdout and os.path.isdir(a['path']), r.stdout + r.stderr)

# 2 a clean stale folder is removed and logged
a = item(make('clean'))
r, rows = execute([a])
check('stale folder is removed', not os.path.exists(a['path']) and status(rows, a) == 'done', r.stdout + r.stderr)

# 3 a file added after the scan holds a stale item
a = item(make('changed'))
open(os.path.join(a['path'], 'new.bin'), 'w').write('y' * 5000)
r, rows = execute([a])
check('change since scan holds the item', status(rows, a) == 'held' and os.path.isdir(a['path']), str(rows))

# 4 an in-place write that leaves the folder mtime and size alone is still caught by the recent-write check
a = item(make('quiet'))
f = os.path.join(a['path'], 'blob.bin')
os.utime(f, None)
os.utime(a['path'], (a['fingerprint']['mtime'],) * 2)
r, rows = execute([a])
check('recent write inside a stale item holds it', status(rows, a) == 'held' and 'written within' in rows[a['id']]['reason'] and os.path.isdir(a['path']), str(rows))

# 5 an open file holds it
a = item(make('openfile'))
p = subprocess.Popen([sys.executable, '-c', 'import sys,time; f=open(sys.argv[1]); time.sleep(60)', os.path.join(a['path'], 'blob.bin')])
time.sleep(1)
r, rows = execute([a])
p.kill()
check('an open file holds the item', status(rows, a) == 'held' and 'in use' in rows[a['id']]['reason'] and os.path.isdir(a['path']), str(rows))

# 6 a command line naming the path holds it
a = item(make('cmdline'))
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', a['path']])
time.sleep(1)
r, rows = execute([a])
p.kill()
check('a process command line holds the item', status(rows, a) == 'held' and os.path.isdir(a['path']), str(rows))

# 7 only SAFE, never a blocked item
v = item(make('verify'), tier='VERIFY')
b = item(make('blocked'), blocked='in use by uv')
r, rows = execute([v, b])
check('VERIFY and blocked items are refused', status(rows, v) == 'refused' and status(rows, b) == 'refused' and os.path.isdir(v['path']) and os.path.isdir(b['path']), str(rows))

# 8 a symlink is never followed
real_dir = make('realdir')
link = os.path.join(CACHES, 'link')
os.symlink(real_dir, link)
a = item(link, fingerprint=None, kb=100)
r, rows = execute([a])
check('a symlinked path is refused and its target kept', status(rows, a) == 'held' and os.path.isdir(real_dir), str(rows))

# 9 a command that does not match the item's own path is never run
other = make('other')
a = item(make('mismatch'), cmds=[f'rm -rf -- {other}'])
r, rows = execute([a])
check('a command naming another path is unsupported', status(rows, a) == 'unsupported' and os.path.isdir(other) and os.path.isdir(a['path']), str(rows))

# 10 an old scan file is refused
a = item(make('oldscan'))
r, rows = execute([a], age_minutes=600)
check('a stale scan file is refused', r.returncode != 0 and os.path.isdir(a['path']), r.stdout + r.stderr)

# 11 a folder that shrank since the scan counts as already reclaimed and is not run again
a = item(make('shrunk'))
a['fingerprint']['kb'] += 500
r, rows = execute([a])
check('a shrunken folder is logged already-reclaimed and left alone', status(rows, a) == 'already-reclaimed' and os.path.isdir(a['path']), str(rows))

# 12 recently used items are opt-in; growth is tolerated
a = item(make('used'), bucket='SAFE-USED')
r, rows = execute([a])
skipped_by_default = os.path.isdir(a['path']) and a['id'] not in rows
a['fingerprint']['kb'] = max(1, a['fingerprint']['kb'] - 50)
r, rows = execute([a], '--bucket', 'SAFE-USED')
check('SAFE-USED is opt-in and tolerates growth', skipped_by_default and status(rows, a) == 'done' and not os.path.exists(a['path']), str(rows))

# 13 docker items are unsupported
d = item(make('dockerish'), path=None, cmds=['docker builder prune -f --filter until=72h'], fingerprint=None)
r, rows = execute([d])
check('Docker items are unsupported', status(rows, d) == 'unsupported', str(rows))

# 14 a repo with uncommitted work holds the item
g = make('gitwork', {'readme.txt': 'hi'})
subprocess.run(['git', 'init', '-q', g], check=True)
open(os.path.join(g, 'untracked.txt'), 'w').write('work')
os.utime(os.path.join(g, 'untracked.txt'), (time.time() - 60 * 86400,) * 2)
for root, dirs, files in os.walk(g):
    for n in files + dirs:
        try:
            os.utime(os.path.join(root, n), (time.time() - 60 * 86400,) * 2)
        except OSError:
            pass
os.utime(g, (time.time() - 60 * 86400,) * 2)
a = item(g)
r, rows = execute([a])
check('uncommitted work in a repo holds the item', status(rows, a) == 'held' and 'git' in rows[a['id']]['reason'] and os.path.isdir(g), str(rows))

# 15 key material holds the item
a = item(make('keys', {'.env': 'SECRET=1', 'blob.bin': 'x' * 100_000}))
r, rows = execute([a])
check('key material holds the item', status(rows, a) == 'held' and 'key material' in rows[a['id']]['reason'] and os.path.isdir(a['path']), str(rows))

# 16 a tool command whose reported cache dir differs from the scanned path never runs
a = item(make('toolpath'), cmds=['pnpm store prune'])
r, rows = execute([a])
check('a tool pointed somewhere else is held, not run', status(rows, a) == 'held' and os.path.isdir(a['path']), str(rows))

# 17 a lagging free is waited for, a plateau gives up, and a small expectation does not wait at all (in-process, fake clock and df)
sys.path.insert(0, os.path.dirname(SCRIPT))
import execute_system as ex  # noqa: E402


def with_reads(reads, expect, **kw):
    seq, sleeps = iter(reads), [0]
    real_free, real_sleep = ex.free_kb, ex.time.sleep
    ex.free_kb = lambda: next(seq, reads[-1])
    ex.time.sleep = lambda s: sleeps.__setitem__(0, sleeps[0] + 1)
    try:
        return ex.settle(reads[0], expect, **kw), sleeps[0]
    finally:
        ex.free_kb, ex.time.sleep = real_free, real_sleep


d, n = with_reads([1000, 1000, 5000, 9000, 10000, 10000], 9000)
check('settle waits for a lagging free to land and reports the final change', d == 9000 and n >= 3, f'delta {d}, sleeps {n}')
d, n = with_reads([1000, 1200, 1200, 1200, 1200, 1200, 1200], 9000)
check('settle gives up once the reading stops moving (shared blocks), after a bounded wait', d == 200 and 4 <= n <= 6, f'delta {d}, sleeps {n}')
d, n = with_reads([1000, 1100], 500)
check('settle does not wait when the folder shrank by under 1 MB', d == 0 and n == 0, f'delta {d}, sleeps {n}')

shutil.rmtree(HOME, ignore_errors=True)
print('\nALL PASS' if not FAILS else f'\n{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)
