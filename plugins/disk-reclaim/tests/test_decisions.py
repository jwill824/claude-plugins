#!/usr/bin/env python3
"""Tests for decisions.py and how scan_system.py applies keep rulings. Uses a throwaway state folder only.

    python3 -B tests/test_decisions.py
"""
import json, os, subprocess, sys, tempfile, time

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
STATE = tempfile.mkdtemp(prefix='decisions-test-')
os.environ['DISK_RECLAIM_STATE'] = STATE
sys.path.insert(0, SCRIPTS)
import decisions, scan_system  # noqa: E402

FAILS = []
GB = 1048576


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {detail}'))
    if not cond:
        FAILS.append(name)


def reset():
    for f in os.listdir(STATE):
        os.remove(os.path.join(STATE, f))


def cli(*args):
    return subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'decisions.py')] + list(args), capture_output=True, text=True,
                          env=dict(os.environ, DISK_RECLAIM_STATE=STATE))


def item(key, kb=2 * GB, tier='VERIFY', **over):
    it = {'id': '001-x', 'label': 'x', 'group': 'App caches', 'tier': tier, 'base_tier': tier, 'kb': kb, 'key': key, 'members': None,
          'evidence': [], 'gates': [], 'blocked': None, 'cmds': [], 'path': None}
    it.update(over)
    return it


# 1 a keep ruling is remembered and found by key
reset()
decisions.keep('/home/u/Library/Caches/Foo', 'Foo cache', 2 * GB, 'still used')
d, lapsed = decisions.lookup('/home/u/Library/Caches/Foo', 2 * GB)
check('a keep ruling is remembered', d and d['decision'] == 'keep' and d['note'] == 'still used' and lapsed is None, str((d, lapsed)))

# 2 growth lapses it; small growth does not
d, why = decisions.lookup('/home/u/Library/Caches/Foo', 2 * GB + 200 * 1024)
check('small growth keeps the ruling', d is not None)
d, why = decisions.lookup('/home/u/Library/Caches/Foo', 4 * GB + 1)
check('doubling and +1 GB lapses it, with a reason', d is None and 'grew' in (why or ''), str((d, why)))
d, why = decisions.lookup('/home/u/Library/Caches/Foo', 8 * GB)
check('+5 GB lapses it even when the ratio is smaller', d is None and 'grew' in (why or ''))

# 3 expiry lapses it
data = decisions.load()
data['/home/u/Library/Caches/Foo']['until'] = time.time() - 10
decisions.save(data)
d, why = decisions.lookup('/home/u/Library/Caches/Foo', 2 * GB)
check('an expired ruling lapses, with a reason', d is None and 'expired' in (why or ''), str((d, why)))

# 4 a malformed file fails loudly for callers that delete, and is only a note for the scanner
open(os.path.join(STATE, 'decisions.json'), 'w').write('{not json')
try:
    decisions.load()
    loud = False
except decisions.DecisionsError:
    loud = True
scan_system.NOTES.clear()
quiet = scan_system.load_rulings() == {} and any('ignored' in n for n in scan_system.NOTES)
check('a malformed file raises for deleters and is noted (not fatal) for the scanner', loud and quiet)

# 5 a failed write is loud, never swallowed
reset()
os.chmod(STATE, 0o500)
try:
    decisions.keep('/home/u/a/b', 'b', GB)
    raised = False
except OSError:
    raised = True
finally:
    os.chmod(STATE, 0o700)
check('a write that cannot happen raises instead of losing the keep silently', raised)

# 6 only keep exists: no command stores a removal, and a hand-edited one is ignored
r = cli('remove', '--key', '/home/u/a/b')
check('there is no remove command', r.returncode != 0)
json.dump({'/home/u/a/b': {'decision': 'remove', 'kb': GB, 'time': time.time(), 'until': None}}, open(os.path.join(STATE, 'decisions.json'), 'w'))
d, _ = decisions.lookup('/home/u/a/b', GB)
check('a stored removal is never honoured', d is None)

# 7 the scanner turns a live ruling into a KEEP demotion with a visible gate, and never the reverse
reset()
decisions.keep('/home/u/Library/Caches/Foo', 'Foo cache', 2 * GB, 'still used')
decisions.keep('/home/u/Library/Caches/Grew', 'Grew', 1 * GB)
a, b, c = item('/home/u/Library/Caches/Foo'), item('/home/u/Library/Caches/Grew', kb=9 * GB), item('/home/u/Library/Caches/Other')
s = item('/home/u/.cache/safe', tier='SAFE')
decisions.keep('/home/u/.cache/safe', 'safe', 2 * GB)
scan_system.apply_decisions([a, b, c, s])
check('a live ruling demotes the item to KEEP and records a gate', a['tier'] == 'KEEP' and any(g['name'] == 'decision' and g['result'] == 'hold' for g in a['gates']), str(a))
check('a SAFE item can be kept too', s['tier'] == 'KEEP')
check('a lapsed ruling leaves the tier alone and says why', b['tier'] == 'VERIFY' and any('grew' in e for e in b['evidence']), str(b))
check('an item with no ruling is untouched', c['tier'] == 'VERIFY' and not c['gates'])

# 8 group items: the CLI rules on each member, and an item with no identity cannot be remembered
reset()
scan = os.path.join(STATE, 'scan.json')
members = [{'key': 'docker-volume:aaa', 'name': 'aaa', 'kb': GB}, {'key': 'docker-volume:bbb', 'name': 'bbb', 'kb': 2 * GB}]
json.dump({'items': [item('docker-volumes:123', kb=3 * GB, id='010-group', label='2 volumes', members=members), item(None, id='011-nokey', label='anon')]}, open(scan, 'w'))
r = cli('keep', '--scan', scan, '--id', '010-group')
data = decisions.load()
check('keeping a group records each member by its own key', r.returncode == 0 and set(data) == {'docker-volume:aaa', 'docker-volume:bbb'} and data['docker-volume:bbb']['kb'] == 2 * GB, r.stdout + r.stderr)
r = cli('keep', '--scan', scan, '--id', '011-nokey')
check('an item with no stable key is refused', r.returncode != 0 and 'stable identity' in r.stderr, r.stdout + r.stderr)

# 9 forget drops a ruling
r = cli('forget', 'docker-volume:aaa')
check('forget drops a ruling', 'docker-volume:aaa' not in decisions.load() and 'docker-volume:bbb' in decisions.load(), r.stdout + r.stderr)

print('\nALL PASS' if not FAILS else f'\n{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)
