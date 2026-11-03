#!/usr/bin/env python3
"""Tests for the --status mode (disk_status.py), using a throwaway state directory.

    python3 -B tests/test_status.py
"""
import contextlib, io, os, shutil, sys, tempfile, time

STATE = tempfile.mkdtemp(prefix='status-test-')
os.environ['DISK_RECLAIM_STATE'] = STATE
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import disk_status as ds

FAILS = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {detail}'))
    if not cond:
        FAILS.append(name)


def status(**kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ds.status(**kw)
    return rc, buf.getvalue()


total, free = ds.volume()
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('no recorded scan is shown but not a problem while space is fine', rc == 0 and 'none recorded' in out and 'Attention' not in out, out)
rc, out = status(warn_free_gb=10 ** 6)
check('no recorded scan counts when space is low', rc == 1 and 'no full scan recorded' in out, out)

ds.save_scan({'free_kb': free, 'items': [{'kb': 4096, 'tier': 'SAFE', 'bucket': 'SAFE-STALE'}, {'kb': 2048, 'tier': 'SAFE', 'bucket': 'SAFE-USED'},
                                          {'kb': 1024, 'tier': 'SAFE', 'bucket': 'SAFE-BLOCKED'}, {'kb': 99, 'tier': 'VERIFY', 'bucket': 'VERIFY'}]})
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('a fresh saved scan is healthy and summarised', rc == 0 and 'Last scan: today' in out and '4.0 MB stale' in out, out)

rc, out = status(warn_free_gb=0, warn_free_pct=0, quiet_if_ok=True)
check('--quiet-if-ok prints nothing when healthy', rc == 0 and out == '', repr(out))

rc, out = status(warn_free_gb=10 ** 6)
check('low free space is flagged', rc == 1 and 'LOW' in out and 'Attention' in out, out)

# an old scan is flagged
import json
p = os.path.join(STATE, 'last-scan.json')
d = json.load(open(p)); d['time'] = time.time() - 30 * 86400; json.dump(d, open(p, 'w'))
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('an old scan is labelled but quiet while space is fine', rc == 0 and 'OLD' in out and 'Attention' not in out, out)
rc, out = status(warn_free_gb=0, warn_free_pct=0, quiet_if_ok=True)
check('--quiet-if-ok stays silent with an old scan and plenty of space', rc == 0 and out == '', repr(out))
rc, out = status(warn_free_gb=10 ** 6)
check('an old scan counts when space is low', rc == 1 and 'days old' in out, out)

# trend: history from 2 days ago with 20 GB more free
with open(os.path.join(STATE, 'history.tsv'), 'w') as f:
    f.write(f'{int(time.time() - 2 * 86400)}\t{free + 20 * 1048576}\t{total}\n')
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('a falling trend shows the rate and runway', 'Trend: -20.0 GB over 2.0 days' in out and 'days of room' in out, out)

ds.save_run(5, 2 * 1048576, 3 * 1048576, '/tmp/x.tsv')
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('the last cleanup is reported', 'Last cleanup: today: 5 item(s)' in out, out)

# hook mode: a one-line summary for the user, model context only when something needs attention
import subprocess
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')


def hook(**kw):
    rc, out = status(hook=True, **kw)
    return rc, (json.loads(out) if out.strip() else None)


rc, h = hook(warn_free_gb=0, warn_free_pct=0, source='startup')
check('hook: healthy gives a one-line summary for the user and nothing for the model',
      rc == 0 and h['systemMessage'].startswith('Disk:') and '\n' not in h['systemMessage'] and 'hookSpecificOutput' not in h, h)
rc, h = hook(warn_free_gb=10 ** 6, source='resume')
ctx = (h or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
check('hook: low space says which threshold was crossed and exits 0', rc == 0 and 'under' in h['systemMessage'] and 'free' in h['systemMessage'], h)
check('hook: low space gives the model choices to offer and says to remove nothing itself',
      '/disk-reclaim:system' in ctx and '/disk-reclaim:review' in ctx and 'AskUserQuestion' in ctx
      and 'do not remove anything yourself' in ctx and 'source: resume' in ctx
      and h['hookSpecificOutput']['hookEventName'] == 'SessionStart', ctx)
rc, h = hook(warn_free_gb=0, warn_free_pct=0, quiet_if_ok=True)
check('hook: --quiet-if-ok prints nothing when healthy', rc == 0 and h is None, h)
rc, h = hook(warn_free_gb=10 ** 6, quiet_if_ok=True)
check('hook: --quiet-if-ok still speaks when space is low', rc == 0 and h is not None and 'hookSpecificOutput' in h, h)

SS = os.path.join(SCRIPTS, 'scan_system.py')
cp = subprocess.run([sys.executable, '-B', SS, '--status', '--hook', '--warn-free-gb', '999999'], input='{"source": "resume"}',
                    capture_output=True, text=True, timeout=60)
check('hook: the CLI reads the session source from stdin', cp.returncode == 0 and 'source: resume' in json.loads(cp.stdout)['hookSpecificOutput']['additionalContext'], cp.stdout + cp.stderr)
# thresholds can come from the environment (settings.json "env"), so a personal threshold stays out of the shared plugin
used_free_pct = 100 * free / total


def hook_cli(env_pct=None, *args):
    env = dict(os.environ)
    env.pop('DISK_RECLAIM_WARN_FREE_PCT', None)
    if env_pct is not None:
        env['DISK_RECLAIM_WARN_FREE_PCT'] = str(env_pct)
    cp = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'scan_system.py'), '--status', '--hook', '--quiet-if-ok', *args],
                        input='{"source": "startup"}', capture_output=True, text=True, env=env, timeout=60)
    return cp.returncode, (json.loads(cp.stdout) if cp.stdout.strip() else None), cp.stdout + cp.stderr


rc, h, raw = hook_cli(used_free_pct + 1)
check('hook: speaks and nags once free space is under a threshold set in the environment',
      rc == 0 and h is not None and 'under' in h['systemMessage'] and 'hookSpecificOutput' in h, raw)
rc, h, raw = hook_cli(max(used_free_pct - 1, 0))
check('hook: stays silent while above that threshold', rc == 0 and h is None, raw)
rc, h, raw = hook_cli('not-a-number')
check('hook: a bad value in the environment falls back to the default', rc == 0 and h is None, raw)
rc, h, raw = hook_cli(100, '--warn-free-pct', '0')
check('hook: an explicit flag beats the environment', rc == 0 and h is None, raw)

blocker = os.path.join(STATE, 'not-a-folder')
open(blocker, 'w').write('x')
env = dict(os.environ, DISK_RECLAIM_STATE=blocker)
cp = subprocess.run([sys.executable, '-B', SS, '--status', '--hook'], input='{}', capture_output=True, text=True, env=env, timeout=60)
check('hook: a broken state folder never fails the session', cp.returncode == 0 and 'Traceback' not in cp.stderr, cp.stdout + cp.stderr)
cp = subprocess.run([sys.executable, '-B', SS, '--status'], capture_output=True, text=True, env=env, timeout=60)
check('plain --status still reports a broken state folder loudly', cp.returncode != 0, cp.stdout + cp.stderr)

# corrupt state never breaks status
open(os.path.join(STATE, 'last-run.json'), 'w').write('{not json')
open(os.path.join(STATE, 'history.tsv'), 'w').write('garbage\n')
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('corrupt state files are ignored', 'Disk:' in out and 'Last cleanup: none recorded' in out, out)

shutil.rmtree(STATE, ignore_errors=True)
print('\nALL PASS' if not FAILS else f'\n{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)
