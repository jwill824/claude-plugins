#!/usr/bin/env python3
"""Tests for VERIFY items in execute_system.py (--approve). A fake home folder, a throwaway state folder, and fake
`docker`, `colima` and `xcrun` programs on PATH that only log their arguments: nothing real is touched.

    python3 -B tests/test_verify.py
"""
import json, os, shutil, stat, subprocess, sys, tempfile, time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'execute_system.py')
ROOT = tempfile.mkdtemp(prefix='verify-test-')
HOME, FAKE, BIN, STATE = (os.path.join(ROOT, d) for d in ('home', 'fake', 'bin', 'state'))
CACHES = os.path.join(HOME, 'Library', 'Caches')
DEVICES = os.path.join(HOME, 'Library', 'Developer', 'CoreSimulator', 'Devices')
for d in (CACHES, DEVICES, FAKE, BIN, STATE, os.path.join(HOME, '.Trash')):
    os.makedirs(d)
ENV = dict(os.environ, HOME=HOME, FAKE=FAKE, DISK_RECLAIM_STATE=STATE, PATH=BIN + os.pathsep + os.environ['PATH'])
FAILS, N = [], [0]
GB = 1048576

SCRIPTS = {
    'docker': '''#!/bin/sh
echo "docker $*" >> "$FAKE/calls.log"
[ "$1" = info ] && exit 0
if [ "$1" = volume ] && [ "$2" = ls ]; then cat "$FAKE/volumes"; exit 0; fi
if [ "$1" = ps ]; then
  for a in "$@"; do case "$a" in volume=*) n=${a#volume=}; grep -qx "$n" "$FAKE/mounted" 2>/dev/null && echo abc123;; esac; done
  exit 0
fi
if [ "$1" = volume ] && [ "$2" = rm ]; then
  shift 2; rc=0
  for n in "$@"; do
    if grep -qx "$n" "$FAKE/inuse" 2>/dev/null; then echo "Error response from daemon: remove $n: volume is in use" >&2; rc=1
    else echo "$n"; grep -vx "$n" "$FAKE/volumes" > "$FAKE/volumes.tmp"; mv "$FAKE/volumes.tmp" "$FAKE/volumes"; fi
  done
  exit $rc
fi
exit 0
''',
    'colima': '''#!/bin/sh
echo "colima $*" >> "$FAKE/calls.log"
if [ "$1" = status ]; then [ -e "$FAKE/colima_down" ] && exit 1; exit 0; fi
if [ "$1" = ssh ]; then cat "$FAKE/newest"; exit 0; fi
exit 0
''',
    'xcrun': '''#!/bin/sh
echo "xcrun $*" >> "$FAKE/calls.log"
if [ "$1 $2 $3" = "simctl list devices" ]; then cat "$FAKE/devices.json"; exit 0; fi
if [ "$1 $2" = "simctl delete" ]; then rm -rf "$HOME/Library/Developer/CoreSimulator/Devices/$3"; exit 0; fi
exit 0
''',
}
for name, body in SCRIPTS.items():
    p = os.path.join(BIN, name)
    open(p, 'w').write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {detail}'))
    if not cond:
        FAILS.append(name)


def du(p):
    return int(subprocess.run(['du', '-sk', p], capture_output=True, text=True).stdout.split()[0])


def calls():
    p = os.path.join(FAKE, 'calls.log')
    return open(p).read().splitlines() if os.path.exists(p) else []


def reset_fake(volumes=(), mounted=(), inuse=(), newest=None, devices=None):
    for f in os.listdir(FAKE):
        os.remove(os.path.join(FAKE, f))
    open(os.path.join(FAKE, 'volumes'), 'w').write('\n'.join(volumes) + '\n')
    open(os.path.join(FAKE, 'mounted'), 'w').write('\n'.join(mounted) + '\n')
    open(os.path.join(FAKE, 'inuse'), 'w').write('\n'.join(inuse) + '\n')
    open(os.path.join(FAKE, 'newest'), 'w').write(''.join(f'{n} {e}\n' for n, e in (newest or {}).items()))
    open(os.path.join(FAKE, 'devices.json'), 'w').write(json.dumps(devices or {'devices': {}}))


def age_tree(p, days=60):
    t = time.time() - days * 86400
    for r, ds, fs in os.walk(p, topdown=False):
        for n in fs + ds:
            os.utime(os.path.join(r, n), (t, t), follow_symlinks=False)
    os.utime(p, (t, t))


def make(parent, name, files=None):
    p = os.path.join(parent, name)
    os.makedirs(p)
    for f, c in (files or {'blob.bin': 'x' * 200_000}).items():
        os.makedirs(os.path.dirname(os.path.join(p, f)), exist_ok=True)
        open(os.path.join(p, f), 'w').write(c)
    age_tree(p)
    return p


def verify_item(p, group='App caches', **over):
    N[0] += 1
    it = {'id': f'{N[0]:03d}-{os.path.basename(p)}', 'label': os.path.basename(p), 'group': group, 'tier': 'VERIFY', 'bucket': 'VERIFY', 'blocked': None,
          'path': p, 'probe': None, 'kb': du(p), 'cmds': [f'rm -rf -- {p}'], 'order': 0, 'evidence': [], 'gates': [], 'key': os.path.realpath(p),
          'members': None, 'activity': {'class': 'STALE', 'days': 40, 'source': 'x'}, 'fingerprint': {'mtime': int(os.stat(p).st_mtime), 'kb': du(p)}}
    it.update(over)
    return it


def volume_item(names, kb=GB, newest=None, group_key=True, **over):
    sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT)))
    from scan_system import volumes_key
    N[0] += 1
    ms = [{'key': f'docker-volume:{n}', 'name': n, 'kb': kb, 'newest_epoch': (newest or {}).get(n)} for n in names]
    it = {'id': f'{N[0]:03d}-vols', 'label': f'{len(names)} volumes', 'group': 'Docker / Colima', 'tier': 'VERIFY', 'bucket': 'VERIFY', 'blocked': None, 'path': None,
          'probe': None, 'kb': kb * len(names), 'cmds': ['docker volume rm ' + ' '.join(names)], 'order': 0, 'evidence': [], 'gates': [],
          'key': volumes_key(names) if group_key else 'docker-volumes:wrong', 'members': ms, 'activity': {'class': 'STALE', 'days': 40, 'source': 'x'}, 'fingerprint': None}
    it.update(over)
    return it


def execute(items, *args, execute=True, env=None):
    f = os.path.join(ROOT, 'scan.json')
    json.dump({'items': items, 'thresholds': {'active_days': 7, 'stale_days': 30}}, open(f, 'w'))
    log = os.path.join(ROOT, 'log.tsv')
    if os.path.exists(log):
        os.remove(log)
    cmd = [sys.executable, '-B', SCRIPT, f, '--log', log, '--lsof-timeout', '60'] + (['--execute'] if execute else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env or ENV, timeout=300)
    rows = {}
    if execute and os.path.exists(log):
        for line in open(log).read().splitlines()[1:]:
            c = line.split('\t')
            rows[c[0]] = {'status': c[3], 'reason': c[4], 'du_freed': int(c[8]), 'cmd': c[10]}
    return r, rows


def trash():
    return os.listdir(os.path.join(HOME, '.Trash'))


def keep(key, kb=GB):
    p = os.path.join(STATE, 'decisions.json')
    data = json.load(open(p)) if os.path.exists(p) else {}
    data[key] = {'decision': 'keep', 'label': key, 'kb': kb, 'time': time.time(), 'until': None, 'note': ''}
    json.dump(data, open(p, 'w'))


def forget_all():
    p = os.path.join(STATE, 'decisions.json')
    if os.path.exists(p):
        os.remove(p)


# ---- folders
reset_fake()
a = verify_item(make(CACHES, 'plain'))
r, rows = execute([a])
check('VERIFY is not run without --approve', os.path.isdir(a['path']) and a['id'] not in rows, r.stdout + r.stderr)
r, rows = execute([a], '--id', a['id'])
check('--id never selects a VERIFY item', os.path.isdir(a['path']) and rows.get(a['id'], {}).get('status') == 'refused', str(rows))

a = verify_item(make(CACHES, 'dryrun'))
r, _ = execute([a], '--approve', a['key'], execute=False)
check('an approved dry run reports would-run, shows the key, and moves nothing', 'would-run' in r.stdout and a['key'] in r.stdout and os.path.isdir(a['path']), r.stdout + r.stderr)

a = verify_item(make(CACHES, 'goes-to-trash'))
r, rows = execute([a], '--approve', a['key'])
st = rows.get(a['id'], {})
check('an approved app cache is moved to the Trash, not deleted', st.get('status') == 'done' and not os.path.exists(a['path']) and any(t.startswith('goes-to-trash') for t in trash())
      and st.get('du_freed') == 0, r.stdout + r.stderr + str(rows))

a = verify_item(make(CACHES, 'changed'))
open(os.path.join(a['path'], 'new.bin'), 'w').write('y' * 5000)
r, rows = execute([a], '--approve', a['key'])
check('a change since the scan holds an approved item', rows.get(a['id'], {}).get('status') == 'held' and os.path.isdir(a['path']), str(rows))

a = verify_item(make(CACHES, 'unknownkey'))
r, rows = execute([a], '--approve', a['key'] + 'X')
check('an unknown key approves nothing', 'unknown key' in r.stdout and os.path.isdir(a['path']) and a['id'] not in rows, r.stdout + r.stderr)

s = verify_item(make(CACHES, 'safeone'), tier='SAFE', bucket='SAFE-USED')
r, rows = execute([s], '--approve', s['key'])
check('--approve does not apply to a SAFE item', 'not VERIFY' in r.stdout and os.path.isdir(s['path']), r.stdout + r.stderr)

k = verify_item(make(CACHES, 'kept'))
keep(k['key'])
r, rows = execute([k], '--approve', k['key'])
check('a live keep ruling refuses an approved item', rows.get(k['id'], {}).get('status') == 'refused' and os.path.isdir(k['path']), str(rows))
open(os.path.join(STATE, 'decisions.json'), 'w').write('{broken')
c = verify_item(make(CACHES, 'corrupt-rulings'))
r, rows = execute([c], '--approve', c['key'])
check('an unreadable keep list stops the whole run', r.returncode != 0 and os.path.isdir(c['path']) and 'Stopping' in (r.stdout + r.stderr), r.stdout + r.stderr)
forget_all()

d = verify_item(make(os.path.join(HOME), 'Documents-stuff'))
r, rows = execute([d], '--approve', d['key'])
check('a location outside the allow-list is unsupported', rows.get(d['id'], {}).get('status') == 'unsupported' and os.path.isdir(d['path']), str(rows))

cl = verify_item(make(HOME, 'notes'), group='Claude data')
r, rows = execute([cl], '--approve', cl['key'])
check('authored Claude data is refused even when approved', rows.get(cl['id'], {}).get('status') == 'refused' and os.path.isdir(cl['path']), str(rows))

# an app that looks to be running holds its cache
app = os.path.join(HOME, 'Applications', 'ZorbAlpha.app', 'Contents', 'MacOS')
os.makedirs(app)
open(os.path.join(app, 'zorb'), 'w').write('#!/bin/sh\nsleep 60\n')
os.chmod(os.path.join(app, 'zorb'), 0o755)
proc = subprocess.Popen([os.path.join(app, 'zorb')])
time.sleep(1)
z = verify_item(make(CACHES, 'ZorbAlpha'))
r, rows = execute([z], '--approve', z['key'])
proc.kill()
check('a cache whose app looks to be running is held', rows.get(z['id'], {}).get('status') == 'held' and 'running' in rows[z['id']]['reason'] and os.path.isdir(z['path']), str(rows))

# ---- simulators
UDID = '11111111-2222-3333-4444-555555555555'
sim_path = make(DEVICES, UDID)
dev = {'udid': UDID, 'name': 'iPhone', 'state': 'Shutdown', 'isAvailable': True, 'lastUsedAt': '2026-10-08T16:41:16Z'}
def sim(**over):
    kw = dict(group='Xcode and simulators', key=f'simulator:{UDID}', cmds=[f'xcrun simctl delete {UDID}'],
              activity={'class': 'STALE', 'days': 40, 'source': 'simctl lastUsedAt 2026-10-08'})
    kw.update(over)
    return verify_item(sim_path, **kw)
reset_fake(devices={'devices': {'rt': [dict(dev, state='Booted')]}})
r, rows = execute([sim()], '--approve', f'simulator:{UDID}')
check('a booted simulator is held and never deleted', os.path.isdir(sim_path) and not any('simctl delete' in c for c in calls()), r.stdout + r.stderr + str(calls()))
reset_fake(devices={'devices': {'rt': [dict(dev, lastUsedAt='2026-10-30T10:00:00Z')]}})
r, rows = execute([sim()], '--approve', f'simulator:{UDID}')
check('a simulator used since the scan is held', os.path.isdir(sim_path) and not any('simctl delete' in c for c in calls()), r.stdout + r.stderr)
reset_fake(devices={'devices': {'rt': [dev]}})
bad = sim(cmds=[f'xcrun simctl delete {UDID[:-1]}0'])
r, rows = execute([bad], '--approve', f'simulator:{UDID}')
check('a command naming another simulator is unsupported', rows.get(bad['id'], {}).get('status') == 'unsupported' and os.path.isdir(sim_path), str(rows))
good = sim()
r, rows = execute([good], '--approve', f'simulator:{UDID}')
check('a shut-down, unchanged simulator is deleted with the rebuilt command', rows.get(good['id'], {}).get('status') == 'done' and not os.path.exists(sim_path)
      and f'xcrun simctl delete {UDID}' in calls(), r.stdout + r.stderr + str(calls()))

# ---- docker volumes
now = time.time()
old = now - 90 * 86400
names = ['mounted_v', 'written_v', 'fine_v', 'kept_v']
newest = {n: old for n in names}
reset_fake(volumes=names, mounted=['mounted_v'], newest=dict(newest, written_v=now - 3600))
keep('docker-volume:kept_v')
g = volume_item(names, newest=newest)
r, rows = execute([g], '--approve', g['key'])
st = rows.get(g['id'], {})
rm = [c for c in calls() if c.startswith('docker volume rm')]
check('a group runs only the members that still pass: mounted, rewritten and kept ones are dropped',
      rm == ['docker volume rm fine_v'] and st.get('status') == 'partial' and all(w in st.get('reason', '') for w in ('mounted_v', 'written_v', 'kept_v')),
      r.stdout + r.stderr + str(calls()) + str(rows))
check('the volumes that were dropped are still there', open(os.path.join(FAKE, 'volumes')).read().split() == ['mounted_v', 'written_v', 'kept_v'])
forget_all()

reset_fake(volumes=['solo'], newest={'solo': old})
open(os.path.join(FAKE, 'colima_down'), 'w').close()
g = volume_item(['solo'], newest={'solo': old})
r, rows = execute([g], '--approve', g['key'])
check('with Colima down nothing is removed', rows.get(g['id'], {}).get('status') == 'held' and not any(c.startswith('docker volume rm') for c in calls()), r.stdout + r.stderr)

reset_fake(volumes=['busy'], inuse=['busy'], newest={'busy': old})
g = volume_item(['busy'], newest={'busy': old}, members=None, key='docker-volume:busy', cmds=['docker volume rm busy'], fingerprint={'newest_epoch': old})
r, rows = execute([g], '--approve', g['key'])
check('if docker refuses a volume the run reports FAILED and counts nothing', rows.get(g['id'], {}).get('status') == 'FAILED' and 'in use' in rows[g['id']]['reason'], str(rows) + r.stdout)

reset_fake(volumes=['a', 'b'], newest={'a': old, 'b': old})
g = volume_item(['a', 'b'], newest={'a': old, 'b': old}, group_key=False)
r, rows = execute([g], '--approve', g['key'])
check('a group whose members do not match its key is unsupported', rows.get(g['id'], {}).get('status') == 'unsupported' and not any(c.startswith('docker volume rm') for c in calls()), str(rows))

g = volume_item(['-f', 'a'], newest={})
r, rows = execute([g], '--approve', g['key'])
check('a volume name that looks like a flag is unsupported', rows.get(g['id'], {}).get('status') == 'unsupported' and not any(c.startswith('docker volume rm') for c in calls()), str(rows))

reset_fake(volumes=['fresh'], newest={'fresh': now - 3600})
g = volume_item(['fresh'], newest={'fresh': None})
r, rows = execute([g], '--approve', g['key'])
check('a volume that had no files at the scan but has some now is dropped', rows.get(g['id'], {}).get('status') == 'held' and not any(c.startswith('docker volume rm') for c in calls()), str(rows))

# ---- an explicit selector never drags in the default bucket
reset_fake()
forget_all()
stale = lambda name: verify_item(make(CACHES, name), tier='SAFE', bucket='SAFE-STALE', key=None)
s1, v1 = stale('stale-one'), verify_item(make(CACHES, 'approved-one'))
r, rows = execute([s1, v1], '--approve', v1['key'])
check('--approve runs only the approved item, not the stale bucket', os.path.isdir(s1['path']) and s1['id'] not in rows and rows.get(v1['id'], {}).get('status') == 'done', r.stdout + r.stderr + str(rows))
last = json.load(open(os.path.join(STATE, 'last-run.json')))
st = subprocess.run([sys.executable, '-B', os.path.join(os.path.dirname(SCRIPT), 'scan_system.py'), '--status'], capture_output=True, text=True, env=ENV)
check('the status line reports what went to the Trash', last.get('trashed_kb', 0) > 0 and 'in the Trash' in st.stdout, st.stdout + st.stderr + str(last))
s2, v2 = stale('stale-two'), verify_item(make(CACHES, 'unapproved-two'))
r, rows = execute([s2, v2], '--id', v2['id'])
check('--id alone runs only that item (a VERIFY one is refused), not the stale bucket', os.path.isdir(s2['path']) and s2['id'] not in rows and rows.get(v2['id'], {}).get('status') == 'refused', r.stdout + r.stderr + str(rows))
s3 = stale('stale-three')
r, rows = execute([s3])
check('with no selector the default stale bucket still runs (control for the two checks above)', not os.path.exists(s3['path']) and rows.get(s3['id'], {}).get('status') == 'done', r.stdout + r.stderr + str(rows))

shutil.rmtree(ROOT, ignore_errors=True)
print('\nALL PASS' if not FAILS else f'\n{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)
