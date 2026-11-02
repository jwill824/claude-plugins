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

# corrupt state never breaks status
open(os.path.join(STATE, 'last-run.json'), 'w').write('{not json')
open(os.path.join(STATE, 'history.tsv'), 'w').write('garbage\n')
rc, out = status(warn_free_gb=0, warn_free_pct=0)
check('corrupt state files are ignored', 'Disk:' in out and 'Last cleanup: none recorded' in out, out)

shutil.rmtree(STATE, ignore_errors=True)
print('\nALL PASS' if not FAILS else f'\n{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)
