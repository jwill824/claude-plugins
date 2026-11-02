#!/usr/bin/env python3
"""Run SAFE items from a `scan_system.py --json` report, re-checking every gate immediately before each command.

Dry run by default: every check runs and the command each item WOULD run is printed, but nothing is changed.
`--execute` runs them, and writes a log.

The scan is a snapshot. This script trusts none of it except the plan itself (which items, which command):
tier, blocks, path sanity, open files, process command lines, the size/mtime fingerprint, recent writes, git
state and key material are all checked again. A check can only hold an item back, never release one the scan
held. By bucket or --id only SAFE items run (Docker items there are unsupported); blocked and KEEP items are
refused, and VERIFY items run only through --approve, below.

  scan_system.py --json > scan.json
  execute_system.py scan.json                      # dry run of the stale group
  execute_system.py scan.json --execute --log run.tsv

VERIFY items (the ones that need a human) run only when named with --approve KEY, never by bucket or --id. The key is
the item's identity from the scan, shown in the dry run. Supported kinds: app, AVD and npx cache folders (moved to the
Trash, not deleted), simulators (`simctl delete`) and Docker volumes (`docker volume rm`, no -f). The last two are
permanent. Anything with a live keep ruling is refused even when approved.
"""
import argparse, json, os, re, shlex, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import run, du_kb, human, has_key_material
from scan_system import git_state, volume_newest, volumes_key
import decisions

H = os.path.expanduser('~')
RULINGS = {}  # filled by main; an unreadable keep list stops the run rather than being read as "nothing is kept"

# Tool commands choose their own target at run time, so the target is asked for again and must equal the scanned path.
# argv -> (command that prints the tool's cache location, suffix appended to its output)
TOOL_TARGET = {
    ('uv', 'cache', 'clean', '--no-wait'): (['uv', 'cache', 'dir'], ''),
    ('pnpm', 'store', 'prune'): (['pnpm', 'store', 'path'], ''),
    ('pip3', 'cache', 'purge'): (['pip3', 'cache', 'dir'], ''),
    # npm refuses to clean without --force; the flag is part of the allow-listed command, not a bypass of any check here
    ('npm', 'cache', 'clean', '--force'): (['npm', 'config', 'get', 'cache'], '_cacache'),
}
BREW = ('brew', 'cleanup', '--prune=all')


def real(p):
    return os.path.realpath(p)


def free_kb():
    vol = '/System/Volumes/Data' if os.path.isdir('/System/Volumes/Data') else '/'
    return shutil.disk_usage(vol).free // 1024


def settle(f0, expect_kb, timeout=30, steady=4):
    """Free-space change since f0, after waiting for a lagging free to land. Deletes can finish in the background
    (`simctl delete` hands the work to a service) and APFS releases blocks a little after the command returns, so one read
    taken right away under-reports. Stops when the change reaches 95% of what the folder shrank, when the reading has not
    moved for `steady` polls (shared blocks really do make it smaller), or at the timeout."""
    t0, last, still = time.time(), free_kb(), 0
    while last - f0 < 0.95 * expect_kb and expect_kb > 1024 and time.time() - t0 < timeout and still < steady:
        time.sleep(1)
        cur = free_kb()
        still = still + 1 if cur == last else 0
        last = cur
    return last - f0


def sane(target):
    """A real (non-symlink) directory inside the home folder, at least two levels down."""
    if not target or os.path.islink(target) or not os.path.isdir(target):
        return False
    rp, rh = real(target), real(H)
    return rp.startswith(rh + os.sep) and rp.count(os.sep) - rh.count(os.sep) >= 2


def open_holders(target, timeout):
    """Commands holding files open under target, via a targeted lsof. None = could not tell (callers hold)."""
    try:
        r = subprocess.run(['lsof', '-nP', '-Fpc', '+D', real(target)], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    names, pid = set(), None
    for line in r.stdout.splitlines():
        if line[:1] == 'p':
            pid = int(line[1:])
        elif line[:1] == 'c' and pid != os.getpid() and line[1:] != 'lsof':
            names.add(line[1:])
    return names


def cmdline_holders(target):
    """Processes whose command line names the path (raw or resolved form)."""
    out = run(['ps', '-axww', '-o', 'pid=,command='])
    forms = {target, real(target)}
    rx = re.compile('(?:' + '|'.join(re.escape(f) for f in forms) + r')(?=$|[/\s:=])')
    names = set()
    for line in out.splitlines():
        t = line.strip().split(None, 1)
        if len(t) == 2 and int(t[0]) != os.getpid() and rx.search(t[1]):
            names.add(os.path.basename(t[1].split()[0]))
    return names


def written_within(target, days, timeout):
    """True/False: a file under target was written within `days`. None = timed out (callers hold)."""
    try:
        r = subprocess.run(['find', real(target), '-type', 'f', '-mtime', f'-{days}', '-print', '-quit'], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return bool(r.stdout.strip())


def plan(it):
    """(argv, target, error). The argv is rebuilt from what was checked, never taken from the stored string."""
    if len(it['cmds']) != 1:
        return None, None, 'unsupported: the item has more than one command'
    try:
        argv = shlex.split(it['cmds'][0])
    except ValueError:
        return None, None, 'unsupported: the command could not be parsed'
    path, probe = it.get('path'), it.get('probe')
    if argv[:1] == ['docker']:
        return None, None, 'unsupported in this version: Docker items need their own re-check; run the listed command yourself'
    if argv[:3] == ['rm', '-rf', '--'] and len(argv) == 4 and path and argv[3] == path:
        return ['rm', '-rf', '--', path], path, None
    if tuple(argv) in TOOL_TARGET and path:
        return argv, path, None
    if tuple(argv) == BREW and probe:
        return argv, probe, None
    if probe and argv == ['find', probe, '-name', '*.log', '-type', 'f', '-mtime', '+7', '-delete']:
        return argv, probe, None
    return None, None, 'unsupported: the command is not one of the allow-listed shapes'


def reverify(it, a, stale_days):
    """(status, reason). status 'ok' means every gate passed just now."""
    if it['tier'] != 'SAFE':
        how = '; name it with --approve KEY after the user has reviewed it' if it['tier'] == 'VERIFY' else ''
        return 'refused', f'tier is {it["tier"]}: only SAFE items are run by bucket or --id{how}'
    kept = kept_ruling(it)
    if kept:
        return 'refused', kept
    if it.get('blocked'):
        return 'refused', f'blocked at scan time ({it["blocked"]}); rescan after it clears'
    argv, target, err = plan(it)
    if err:
        return 'unsupported', err
    if not shutil.which(argv[0]):
        return 'held', f'{argv[0]} is not on PATH'
    if not os.path.exists(target):
        return 'already-reclaimed', 'the path no longer exists (an earlier command or another process removed it)'
    if not sane(target):
        return 'held', 'path sanity: not a real directory inside the home folder, or a symlink'

    fp = it.get('fingerprint') if it.get('path') else None
    if fp:
        kb = du_kb(target)
        if kb < fp['kb']:
            return 'already-reclaimed', f'smaller than at scan time ({human(fp["kb"])} -> {human(kb)}); an earlier command or another process shrank it'
        if it['bucket'] == 'SAFE-STALE' and (int(os.stat(target).st_mtime) != fp['mtime'] or kb != fp['kb']):
            return 'held', 'changed since the scan (mtime or size); it is no longer proven stale'

    tool = TOOL_TARGET.get(tuple(argv))
    if tool:
        said = run(tool[0], timeout=60).strip()
        got = os.path.join(said, tool[1]) if said and tool[1] else said
        if not said or real(got) != real(it['path']):
            return 'held', f'`{" ".join(tool[0])}` now reports {got or "nothing"}, not the scanned path'
    if tuple(argv) == BREW and not run(['brew', 'cleanup', '--prune=all', '-n'], timeout=120).strip():
        return 'already-reclaimed', 'brew reports nothing left to clean'

    holders = open_holders(target, a.lsof_timeout)
    if holders is None:
        return 'held', 'lsof could not answer, so open files cannot be ruled out'
    holders |= cmdline_holders(target)
    if holders:
        return 'held', 'in use by ' + ', '.join(sorted(holders))

    if it['bucket'] == 'SAFE-STALE':
        w = written_within(target, stale_days, a.find_timeout)
        if w is None:
            return 'held', 'the recent-write check timed out'
        if w:
            return 'held', f'a file was written within {stale_days} days; no longer stale'

    if os.path.isdir(target) and it.get('path'):
        bad = [r for r in git_state(target) if r[1] or (r[2] or 0) > 0]
        if bad:
            return 'held', 'a git repository inside has uncommitted or unpushed work: ' + ', '.join(os.path.relpath(r[0], target) for r in bad)
        key = has_key_material(target)
        if key:
            return 'held', f'possible key material ({os.path.basename(key)})'
    return 'ok', 'all gates passed' + ('' if fp else ' (no fingerprint for this item; gates re-run only)')


# ---------------------------------------------------------------- VERIFY items (named with --approve)
VOLUME_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


def kept_ruling(it):
    """A reason string when the user ruled keep on this item and the ruling is still live, else None."""
    d, _ = decisions.lookup(it.get('key'), it.get('kb'), RULINGS)
    if d:
        return (f'you ruled keep on {time.strftime("%Y-%m-%d", time.localtime(d["time"]))}; '
                f'`decisions.py forget {it["key"]}` first if you changed your mind')
    return None


def verify_plan(it):
    """(kind, info, error). Rebuilt from the verified fields, never from the stored string alone."""
    if it.get('group') == 'Claude data':
        return None, None, 'refused: authored working notes and transcripts cannot be regenerated; archive or remove them by hand'
    if len(it['cmds']) != 1:
        return None, None, 'unsupported: the item has no single command to rebuild it from'
    try:
        argv = shlex.split(it['cmds'][0])
    except ValueError:
        return None, None, 'unsupported: the command could not be parsed'
    path, key = it.get('path'), it.get('key')
    if argv[:3] == ['rm', '-rf', '--'] and len(argv) == 4 and path and argv[3] == path and key and key == real(path):
        rp = real(path)
        parent = os.path.dirname(rp)
        if parent == real(f'{H}/Library/Caches') or rp == real(f'{H}/.npm/_npx') or (parent == real(f'{H}/.android/avd') and rp.endswith('.avd')):
            return 'trash', {'path': path}, None
        return None, None, 'unsupported: that location is not one the executor will remove'
    if argv[:3] == ['xcrun', 'simctl', 'delete'] and len(argv) == 4:
        udid = argv[3]
        if key == f'simulator:{udid}' and path == f'{H}/Library/Developer/CoreSimulator/Devices/{udid}':
            return 'simulator', {'udid': udid, 'path': path}, None
    if argv[:3] == ['docker', 'volume', 'rm'] and len(argv) >= 4:
        names = argv[3:]
        if all(VOLUME_NAME.match(n) for n in names) and len(set(names)) == len(names):
            if len(names) == 1 and not it.get('members') and key == f'docker-volume:{names[0]}':
                return 'volumes', {'members': [{'key': key, 'name': names[0], 'kb': it['kb'] or 0,
                                                'newest_epoch': (it.get('fingerprint') or {}).get('newest_epoch')}]}, None
            if it.get('members') and sorted(m['name'] for m in it['members']) == sorted(names) and key == volumes_key(names):
                return 'volumes', {'members': it['members']}, None
    return None, None, 'unsupported: the command is not one of the allow-listed shapes for a VERIFY item'


def app_running(name):
    """Heuristic: a running .app whose name contains a word from the cache folder's name. Quitting the app is still the user's job;
    this only catches the obvious case that has no file open inside its cache at this instant."""
    words = [w for w in re.split(r'[.\s_-]+', name) if len(w) >= 4 and w.lower() not in ('com', 'org', 'client', 'cache', 'caches', 'updater')]
    out = run(['ps', '-axww', '-o', 'command='])
    for w in words:
        m = re.search(r'(?i)/' + re.escape(w) + r'[^/]*\.app/', out)
        if m:
            return m.group(0).strip('/')
    return None


def emulator_running():
    return bool(re.search(r'qemu-system-\S+|/emulator(64\S*)?( |$)', run(['ps', '-axww', '-o', 'command='])))


def written_minutes(target, minutes, timeout):
    try:
        r = subprocess.run(['find', real(target), '-type', 'f', '-mmin', f'-{minutes}', '-print', '-quit'], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return bool(r.stdout.strip())


def folder_checks(it, target, a):
    """Checks shared by the folder kinds. None when everything passes, else (status, reason). The approval was for one state of
    the folder, so any change since the scan holds it."""
    if not os.path.exists(target):
        return 'already-reclaimed', 'the path no longer exists'
    if not sane(target):
        return 'held', 'path sanity: not a real directory inside the home folder, or a symlink'
    fp = it.get('fingerprint')
    if not fp:
        return 'held', 'the scan recorded no fingerprint, so a change since the scan could not be detected'
    kb = du_kb(target)
    if int(os.stat(target).st_mtime) != fp['mtime'] or kb != fp['kb']:
        return 'held', 'changed since the scan (mtime or size); the approval was for the state that was reviewed. Rescan'
    holders = open_holders(target, a.lsof_timeout)
    if holders is None:
        return 'held', 'lsof could not answer, so open files cannot be ruled out'
    holders |= cmdline_holders(target)
    if holders:
        return 'held', 'in use by ' + ', '.join(sorted(holders))
    w = written_minutes(target, 10, a.find_timeout)
    if w is None or w:
        return 'held', 'a file was written in the last 10 minutes' if w else 'the recent-write check timed out'
    bad = [r for r in git_state(target) if r[1] or (r[2] or 0) > 0]
    if bad:
        return 'held', 'a git repository inside has uncommitted or unpushed work: ' + ', '.join(os.path.relpath(r[0], target) for r in bad)
    key = has_key_material(target)
    if key:
        return 'held', f'possible key material ({os.path.basename(key)}): remove it by hand if you are sure'
    return None


def trash_dest(path):
    base, stamp = os.path.basename(path.rstrip('/')), time.strftime('%Y-%m-%d %H.%M.%S')
    dest, n = os.path.join(H, '.Trash', f'{base} {stamp}'), 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(H, '.Trash', f'{base} {stamp} {n}')
    return dest


def simulators():
    try:
        devs = json.loads(run(['xcrun', 'simctl', 'list', 'devices', '-j'], timeout=60) or '{}').get('devices', {})
    except ValueError:
        return None
    return {d['udid']: d for ds in devs.values() for d in ds}


def run_verify(it, a):
    """Re-verify an approved VERIFY item and, with --execute, perform it. Returns a dict for the caller to print and log:
    status, reason, cmd (what ran or would run), kb0/kb1 (folder size before/after), df (free-space change), kind and removed_kb."""
    out = {'status': 'held', 'reason': '', 'cmd': '', 'kb0': 0, 'kb1': 0, 'df': 0, 'kind': None, 'removed_kb': 0}

    def done(status, reason, **kw):
        out.update(status=status, reason=reason, **kw)
        return out

    if it['tier'] != 'VERIFY':
        return done('refused', f'--approve is for VERIFY items; this one is {it["tier"]}')
    if it.get('blocked'):
        return done('refused', f'blocked at scan time ({it["blocked"]}); rescan after it clears')
    kept = kept_ruling(it)
    if kept:
        return done('refused', kept)
    kind, info, err = verify_plan(it)
    if err:
        return done('refused' if err.startswith('refused') else 'unsupported', err)
    out['kind'] = kind

    if kind == 'trash':
        target = info['path']
        bad = folder_checks(it, target, a)
        if bad:
            return done(*bad)
        if it['group'] == 'App caches' and app_running(os.path.basename(target)):
            return done('held', f'the app looks to be running ({app_running(os.path.basename(target))}); quit it first')
        if it['group'] == 'Android' and emulator_running():
            return done('held', 'an Android emulator is running')
        dest = trash_dest(target)
        out['cmd'] = f'mv -- {shlex.quote(target)} {shlex.quote(dest)}'
        out['kb0'] = out['kb1'] = it['fingerprint']['kb']
        if not a.execute:
            return done('would-run', 'all gates passed; moves to the Trash (reversible; space returns when the Trash is emptied)', removed_kb=out['kb0'])
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.rename(target, dest)
        except OSError as e:
            hint = ' (macOS may require Full Disk Access for this terminal to write to ~/.Trash)' if isinstance(e, PermissionError) else ''
            return done('FAILED', f'{e}{hint}; nothing was deleted')
        return done('done', f'moved to the Trash as {os.path.basename(dest)}', removed_kb=out['kb0'])

    if kind == 'simulator':
        udid, target = info['udid'], info['path']
        if not shutil.which('xcrun'):
            return done('held', 'xcrun is not on PATH')
        devs = simulators()
        if devs is None:
            return done('held', 'simctl list could not be read')
        d = devs.get(udid)
        if not d or not os.path.exists(target):
            return done('already-reclaimed', 'the simulator no longer exists')
        if d.get('state') != 'Shutdown':
            return done('held', f'simulator state is {d.get("state")}, not Shutdown')
        seen = re.search(r'lastUsedAt (\d{4}-\d\d-\d\d)', (it.get('activity') or {}).get('source') or '')
        if seen and (d.get('lastUsedAt') or '')[:10] > seen.group(1):
            return done('held', f'used since the scan (lastUsedAt {d["lastUsedAt"][:10]}, scan saw {seen.group(1)})')
        bad = folder_checks(it, target, a)
        if bad:
            return done(*bad)
        argv = ['xcrun', 'simctl', 'delete', udid]
        out['cmd'] = shlex.join(argv)
        out['kb0'] = it['fingerprint']['kb']
        if not a.execute:
            return done('would-run', 'all gates passed; PERMANENT (a simulator cannot be moved to the Trash)', removed_kb=out['kb0'])
        f0 = free_kb()
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=a.cmd_timeout)
            ok, tail = r.returncode == 0, ((r.stderr or r.stdout).strip().splitlines() or [''])[-1]
        except (OSError, subprocess.SubprocessError) as e:
            ok, tail = False, str(e)
        out['kb1'] = du_kb(target) if os.path.exists(target) else 0
        out['df'] = settle(f0, max(out['kb0'] - out['kb1'], 0))
        return done('done' if ok else 'FAILED', f'ok, free space {"+" if out["df"] >= 0 else "-"}{human(abs(out["df"]))}' if ok else tail[:200],
                    removed_kb=max(out['kb0'] - out['kb1'], 0))

    # Docker volumes: every member is re-checked and any that changed is dropped, so one stale member never blocks the rest
    # and one changed member is never removed on the strength of the old scan.
    if not shutil.which('docker') or subprocess.run(['docker', 'info'], capture_output=True).returncode != 0:
        return done('held', 'docker is not reachable')
    if subprocess.run(['colima', 'status'], capture_output=True).returncode != 0:
        return done('held', 'colima is not running, so volume contents cannot be re-checked')
    members = info['members']
    present = set(run(['docker', 'volume', 'ls', '-q'], timeout=60).split())
    newest = volume_newest([m['name'] for m in members if m['name'] in present])
    survivors, dropped = [], []
    for m in members:
        n = m['name']
        d, _ = decisions.lookup(m['key'], m['kb'], RULINGS)
        if d:
            dropped.append(f'{n}: you ruled keep')
        elif n not in present:
            dropped.append(f'{n}: already gone')
        elif run(['docker', 'ps', '-a', '--filter', f'volume={n}', '-q'], timeout=60).strip():
            dropped.append(f'{n}: a container mounts it now')
        elif m.get('newest_epoch') is not None and (n not in newest or newest[n] > m['newest_epoch'] + 1):
            dropped.append(f'{n}: ' + ('its contents could not be read' if n not in newest else 'written since the scan'))
        elif m.get('newest_epoch') is None and n in newest:
            dropped.append(f'{n}: has files now that the scan did not see')
        else:
            survivors.append(m)
    out['dropped'] = dropped
    note = (' Dropped: ' + '; '.join(dropped[:4]) + (f' and {len(dropped) - 4} more' if len(dropped) > 4 else '')) if dropped else ''
    if not survivors:
        return done('already-reclaimed' if all(x.endswith('already gone') for x in dropped) else 'held', 'no member passed its checks.' + note)
    argv = ['docker', 'volume', 'rm'] + [m['name'] for m in survivors]
    out['cmd'] = f'docker volume rm <{len(survivors)} volume(s)>' if len(survivors) > 3 else shlex.join(argv)
    out['kb0'] = sum(m['kb'] for m in survivors)
    if not a.execute:
        return done('would-run', f'{len(survivors)} of {len(members)} volume(s) pass; PERMANENT, no -f so docker refuses any in use.' + note, removed_kb=out['kb0'])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=a.cmd_timeout)
        removed = {l.strip() for l in r.stdout.splitlines()} & {m['name'] for m in survivors}
        tail = ((r.stderr or '').strip().splitlines() or [''])[-1]
    except (OSError, subprocess.SubprocessError) as e:
        removed, tail = set(), str(e)
    gone_kb = sum(m['kb'] for m in survivors if m['name'] in removed)
    if not removed:
        return done('FAILED', (tail or 'nothing was removed')[:200] + note)
    full = len(removed) == len(members)
    return done('done' if full else 'partial', f'{len(removed)} of {len(members)} removed inside the Colima VM; free space on the Mac returns after `colima ssh -- sudo fstrim -av`.' + note + (f' docker said: {tail[:120]}' if len(removed) < len(survivors) else ''), removed_kb=gone_kb)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scan_json', help='output of scan_system.py --json')
    ap.add_argument('--bucket', action='append', choices=['SAFE-STALE', 'SAFE-USED'], help='bucket to run (repeatable; default SAFE-STALE). SAFE-USED caches are in recent use: expect a rebuild')
    ap.add_argument('--id', action='append', default=[], help='run only this item id (repeatable); it must still be SAFE and unblocked')
    ap.add_argument('--approve', action='append', default=[], metavar='KEY', help='run this VERIFY item, by its key from the scan (repeatable). '
                    'Only after the user has reviewed that item; a bucket or --id never selects one')
    ap.add_argument('--execute', action='store_true', help='run the commands (default is a dry run)')
    ap.add_argument('--log', default='system-reclaim-log.tsv')
    ap.add_argument('--max-age-minutes', type=int, default=240, help='refuse a scan file older than this')
    ap.add_argument('--lsof-timeout', type=int, default=120)
    ap.add_argument('--find-timeout', type=int, default=60)
    ap.add_argument('--cmd-timeout', type=int, default=900)
    a = ap.parse_args()
    if sys.platform != 'darwin':
        sys.exit('execute_system.py runs scans from scan_system.py, which supports macOS only.')

    age = (time.time() - os.stat(a.scan_json).st_mtime) / 60
    if age > a.max_age_minutes:
        sys.exit(f'The scan is {age:.0f} minutes old (limit {a.max_age_minutes}). Rescan first: the gates compare against it.')
    data = json.load(open(a.scan_json))
    thr = data.get('thresholds') or {}
    stale_days = int(thr.get('stale') or thr.get('stale_days') or 30)
    try:
        RULINGS.update(decisions.load())
    except decisions.DecisionsError as e:
        sys.exit(f'Stopping: {e}. Keep rulings protect items from removal, so they must be readable before anything runs.')
    # The default bucket applies only when nothing was named: --id or --approve alone mean exactly those items, never "those plus everything stale".
    buckets = set(a.bucket) if a.bucket else (set() if (a.id or a.approve) else {'SAFE-STALE'})
    approved = set(a.approve)
    by_key = {i.get('key'): i for i in data['items'] if i.get('key')}
    chosen = [i for i in data['items'] if i['id'] in a.id or i.get('bucket') in buckets or (i.get('key') in approved and i['tier'] == 'VERIFY')]
    for missing in sorted(set(a.id) - {i['id'] for i in data['items']}):
        print(f'unknown id: {missing}')
    for missing in sorted(approved - set(by_key)):
        print(f'unknown key (not in this scan, so not approved for anything): {missing}')
    for k in sorted(approved & set(by_key)):
        if by_key[k]['tier'] != 'VERIFY':
            print(f'{k} is {by_key[k]["tier"]}, not VERIFY: --approve does not apply (SAFE items run by bucket or --id)')
    chosen.sort(key=lambda i: (i.get('order', 0), -(i['kb'] or 0)))

    mode = 'EXECUTING' if a.execute else 'DRY RUN (nothing is changed; add --execute to run)'
    print(f'{mode}: {len(chosen)} item(s), scan is {age:.0f} min old\n')
    log = open(a.log, 'w') if a.execute else None
    if log:
        log.write('id\tlabel\tbucket\tstatus\treason\tpredicted_kb\tdu_before_kb\tdu_after_kb\tdu_freed_kb\tdf_delta_kb\tcommand\n')
    tot = {'pred': 0, 'du': 0, 'df': 0, 'trash': 0, 'vm': 0}
    counts = {}
    run_f0 = free_kb()

    def record(it, status, reason, kb0=0, kb1=0, df=0, cmd=None):
        counts[status] = counts.get(status, 0) + 1
        if log:
            cmd = cmd if cmd is not None else (it['cmds'][0] if it['cmds'] else '')
            log.write('\t'.join(str(x).replace('\t', ' ').replace('\n', ' ') for x in (it['id'], it['label'], it.get('bucket') or it['tier'], status, reason, it['kb'] or 0, kb0, kb1, max(kb0 - kb1, 0), df, cmd)) + '\n')
            log.flush()

    for it in chosen:
        if it['tier'] == 'VERIFY' and it.get('key') in approved:
            r = run_verify(it, a)
            label = f'{it["label"][:46]:46} {human(it["kb"] or 0):>9}'
            print(f'  [{r["status"]}] {label}  {r["reason"]}')
            if r['cmd']:
                print(f'      {r["cmd"]}')
            print(f'      key: {it["key"]}')
            record(it, r['status'], r['reason'], r['kb0'], r['kb1'], r['df'], r['cmd'])
            if r['status'] in ('would-run', 'done', 'partial'):
                tot['pred'] += r['removed_kb'] if r['status'] == 'would-run' else 0
                if r['status'] != 'would-run':
                    if r['kind'] == 'trash':
                        tot['trash'] += r['removed_kb']
                    elif r['kind'] == 'volumes':
                        tot['vm'] += r['removed_kb']
                    else:
                        tot['du'] += r['removed_kb']
                        tot['df'] += r['df']
            continue
        status, reason = reverify(it, a, stale_days)
        label = f'{it["label"][:46]:46} {human(it["kb"] or 0):>9}'
        if status != 'ok':
            print(f'  [{status}] {label}  {reason}')
            record(it, status, reason)
            continue
        argv, target, _ = plan(it)
        if not a.execute:
            print(f'  [would-run] {label}  {shlex.join(argv)}')
            record(it, 'would-run', reason)
            tot['pred'] += it['kb'] or 0
            continue
        k0, f0 = du_kb(target), free_kb()
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=a.cmd_timeout)
            ok, tail = r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1:] or ['']
        except (OSError, subprocess.SubprocessError) as e:
            ok, tail = False, [str(e)]
        k1 = du_kb(target) if os.path.exists(target) else 0
        freed = max(k0 - k1, 0)
        df = settle(f0, freed)
        if not ok:
            print(f'  [FAILED] {label}  {tail[0][:90]}')
            record(it, 'FAILED', tail[0][:200], k0, k1, df)
            continue
        note = ''
        if freed > 1024 and df < 0.5 * freed:
            note = '  (free space moved much less than the folder shrank: shared blocks or other disk activity)'
        print(f'  [done] {label}  {human(k0)} -> {human(k1)}, free space {"+" if df >= 0 else "-"}{human(abs(df))}{note}')
        record(it, 'done', 'ok' + note.strip(), k0, k1, df)
        tot['pred'] += it['kb'] or 0
        tot['du'] += freed
        tot['df'] += df
    print('\n' + ', '.join(f'{n} {s}' for s, n in sorted(counts.items())))
    if a.execute:
        # The per-item figures are approximate (several items overlap in time and other processes write too); the whole-run
        # change, read after any lagging free has landed, is the one to report.
        tot['df'] = settle(run_f0, tot['du'])
        if tot['du']:
            print(f'Folders shrank by {human(tot["du"])}.')
        print(f'Free space changed by {"+" if tot["df"] >= 0 else "-"}{human(abs(tot["df"]))} over the whole run (includes any other disk activity). '
              f'Items skipped as already-reclaimed are not counted. Log: {a.log}')
        if tot['trash']:
            print(f'Moved to the Trash: {human(tot["trash"])}. That space returns only when the Trash is emptied.')
        if tot['vm']:
            print(f'Removed inside the Colima VM: {human(tot["vm"])}. It reaches the Mac only after `colima ssh -- sudo fstrim -av`.')
    else:
        print(f'Would run {counts.get("would-run", 0)} item(s); the scan estimates up to {human(tot["pred"])} (an upper bound).')
    if log:
        log.close()
        from disk_status import save_run
        save_run(counts.get('done', 0) + counts.get('partial', 0), tot['du'], tot['df'], os.path.abspath(a.log), tot['trash'], tot['vm'])


if __name__ == '__main__':
    main()
