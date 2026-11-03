#!/usr/bin/env python3
"""Read-only report on machine-level storage that lives outside any project tree.

Covers tool caches, Xcode and iOS simulators, Android emulators, Docker/Colima and
Claude working data. Every item gets a tier (SAFE / VERIFY / KEEP), an activity class
(ACTIVE / RECENT / STALE / UNKNOWN), the evidence behind both, and the exact commands
that would reclaim it. Safety gates run on every item and can only make a tier more
cautious, never less. Never deletes or modifies anything. Standard library only;
macOS-oriented, and any tool that is not installed is skipped.

Usage: scan_system.py [--min-mb N] [--max-seconds N] [--active-days N] [--stale-days N]
                      [--trim-min-gb N] [--overview] [--html FILE] [--json]
"""
import argparse, bisect, glob, hashlib, json, os, re, shlex, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from shutil import which

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import run, du_kb, human, has_key_material  # reuse the project scanner's helpers
import decisions

H = os.path.expanduser('~')
DAY = 86400
GROUP_ORDER = ['Tool caches', 'Xcode and simulators', 'Android', 'Docker / Colima', 'Claude data', 'App caches']
ITEMS, NOTES = [], []
CFG = {'active': 7, 'stale': 30, 'deadline': 0.0}
DOCKER_AGE = '72h'  # docker items younger than this are never proposed
# ~/Library/Caches folders that are developer-tool caches with no state of their own.
DEV_CACHES = {'node-gyp', 'go-build', 'pnpm', 'electron', 'electron-builder', 'org.swift.swiftpm'}
# Tool caches that do NOT come back by themselves: the tool fails until a manual step, or the folder holds session state.
NEEDS_MANUAL_STEP = {
    'ms-playwright': 'browser binaries; Playwright runs fail until `npx playwright install` is run again',
    'Cypress': 'the Cypress binary; runs fail until `npx cypress install` is run again',
    'ms-playwright-mcp': 'browser profiles used by the Playwright MCP server; may hold logged-in sessions',
}


def add(group, label, tier, why, path=None, kb=None, cmd=None, blocked=None, order=0, probe=None,
        evidence=None, activity=None, estimate=False, key=None, members=None, fingerprint=None):
    """`key` is the item's identity across scans (ids are positions and shift). It defaults to the real path; items with no
    path pass one ('simulator:UDID', 'docker-volume:NAME'). `members` lists what a group item covers, each with its own key."""
    cmds = [cmd] if isinstance(cmd, str) else list(cmd or [])
    ITEMS.append({'group': group, 'label': label, 'tier': tier, 'base_tier': tier, 'why': why, 'path': path, 'probe': probe,
                  'kb': kb, 'cmds': cmds, 'blocked': blocked, 'order': order, 'evidence': list(evidence or []),
                  'activity': activity, 'estimate': estimate, 'gates': [], 'fingerprint': fingerprint,
                  'key': key or (os.path.realpath(path) if path else None), 'members': members})


def rm_cmd(path):
    return f'rm -rf -- {shlex.quote(path)}'


def succeeds(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except Exception:
        return False


def running(pattern):
    """Describe running processes whose command line matches an anchored regex, or None."""
    out = run(['pgrep', '-f', pattern])
    n = len([x for x in out.split() if x and int(x) != os.getpid()])
    return f'{n} matching process(es) running' if n else None


def age_days(path):
    try:
        return (time.time() - os.stat(path).st_mtime) / DAY
    except OSError:
        return None


def children(path, min_kb, skip=()):
    """Immediate children of path at least min_kb big, sized in parallel."""
    try:
        names = [n for n in os.listdir(path) if n not in skip]
    except OSError:
        return []
    full = [os.path.join(path, n) for n in names]
    with ThreadPoolExecutor(8) as ex:
        sizes = list(ex.map(du_kb, full))
    return sorted(((kb, p) for kb, p in zip(sizes, full) if kb >= min_kb), reverse=True)


def docker_kb(s):
    """'4.664GB' or '7.21GB (100%)' -> KB (docker prints decimal units)."""
    m = re.match(r'\s*([\d.]+)\s*([kKMGT]?)B', s or '')
    if not m:
        return 0
    return int(float(m.group(1)) * {'': 1, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12}[m.group(2)] / 1024)


def classify(days):
    if days is None:
        return 'UNKNOWN'
    return 'ACTIVE' if days < CFG['active'] else 'RECENT' if days < CFG['stale'] else 'STALE'


def act(days, source):
    return {'class': classify(days), 'days': None if days is None else round(days, 1), 'source': source}


def iso_days(ts):
    """Days since an ISO-ish timestamp ('2026-10-08T16:41:16Z' or docker's '2026-07-16 10:06:48 -0400 EDT')."""
    from datetime import datetime, timezone
    m = re.match(r'(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)', ts or '')
    if not m:
        return None
    try:
        t = datetime.strptime(f'{m.group(1)} {m.group(2)}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / DAY)


# ---------------------------------------------------------------- tool caches
def tool_caches(min_kb):
    g = 'Tool caches'
    add(g, 'npm cache (~/.npm/_cacache)', 'SAFE', 'the only part `npm cache clean` clears; re-downloaded on next install',
        f'{H}/.npm/_cacache', cmd='npm cache clean --force', blocked=running(r'(^|/)npm (install|ci|i|add|update|up)( |$)'))
    add(g, '~/.npm/_npx', 'VERIFY', 'tools fetched by npx; MCP servers launched with `npx` run from here, so removing it breaks any that are running',
        f'{H}/.npm/_npx', cmd=rm_cmd(f'{H}/.npm/_npx'))
    uvdir = (run(['uv', 'cache', 'dir']).strip() if which('uv') else '') or f'{H}/.cache/uv'
    add(g, 'uv cache', 'SAFE', 're-downloaded on next use; `uv cache clean` cannot run while any uv process holds the cache lock',
        uvdir, cmd='uv cache clean --no-wait')
    store = run(['pnpm', 'store', 'path']).strip() if which('pnpm') else ''
    if store:
        add(g, 'pnpm store', 'SAFE', 'prune removes only packages no project references, so this size is an upper bound',
            store, cmd='pnpm store prune', estimate=True)
    pipdir = run(['pip3', 'cache', 'dir']).strip() if which('pip3') else ''
    if pipdir:
        add(g, 'pip cache', 'SAFE', 're-downloaded on next install', pipdir, cmd='pip3 cache purge')
    if which('brew'):
        dry = run(['brew', 'cleanup', '--prune=all', '-n'], timeout=120)
        m = re.search(r'free approximately ([\d.,]+)\s*([kKMGT]?B)', dry)
        if m:
            kb = docker_kb(m.group(1).replace(',', '') + m.group(2))
            add(g, 'Homebrew cleanup (old downloads and old versions)', 'SAFE',
                'size is brew\'s own dry run (`brew cleanup -n`): stale downloads plus superseded versions; current versions stay',
                kb=kb, cmd='brew cleanup --prune=all', probe=run(['brew', '--cache']).strip() or None)
    for name in ('puppeteer', 'chrome-devtools-mcp'):
        add(g, f'~/.cache/{name}', 'SAFE', 'browser downloads and profiles; fetched again on demand', f'{H}/.cache/{name}',
            cmd=rm_cmd(f'{H}/.cache/{name}'))
    daemon = f'{H}/.gradle/daemon'
    if os.path.isdir(daemon):
        out = run(['find', daemon, '-name', '*.log', '-type', 'f', '-mtime', '+7', '-exec', 'du', '-k', '{}', '+'], timeout=120)
        kb = sum(int(l.split()[0]) for l in out.splitlines() if l.split())
        add(g, 'Gradle daemon logs older than 7 days', 'SAFE', 'only log files; running daemons are untouched', kb=kb,
            probe=daemon, activity=act(7.0, 'selected by age: older than 7 days'),
            cmd=f"find {shlex.quote(daemon)} -name '*.log' -type f -mtime +7 -delete")
    add(g, '~/.gradle/caches', 'VERIFY', 'redownload is slow and breaks offline builds; may hold artifacts you cannot fetch again',
        f'{H}/.gradle/caches', cmd=rm_cmd(f'{H}/.gradle/caches'))
    add(g, '~/.m2/repository', 'VERIFY', 'can contain artifacts you mvn-installed locally that exist nowhere else',
        f'{H}/.m2/repository', cmd=rm_cmd(f'{H}/.m2/repository'))
    for kb, p in children(f'{H}/.cache', min_kb, skip=('uv', 'puppeteer', 'chrome-devtools-mcp')):
        add(g, f'~/.cache/{os.path.basename(p)}', 'VERIFY', 'tool-owned cache not covered by a known clean command',
            p, kb, cmd=rm_cmd(p))
    add(g, 'Trash', 'VERIFY', 'look before emptying: it is the last copy of anything in it', f'{H}/.Trash')


# ---------------------------------------------------------------- xcode
def xcode(min_kb):
    g = 'Xcode and simulators'
    dev = f'{H}/Library/Developer'
    xb = running(r'/Xcode\.app/Contents/MacOS/Xcode( |$)')
    dd = f'{dev}/Xcode/DerivedData'
    add(g, 'Xcode DerivedData', 'SAFE', 'rebuilt by Xcode; the next build is a full one', dd,
        cmd=f"find {shlex.quote(dd)} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +", blocked=xb)
    sim_booted = len(re.findall(r'\(Booted\)', run(['xcrun', 'simctl', 'list', 'devices']))) if which('xcrun') else 0
    add(g, 'CoreSimulator caches', 'SAFE', 'dyld and runtime caches, regenerated at next boot', f'{dev}/CoreSimulator/Caches',
        cmd=rm_cmd(f'{dev}/CoreSimulator/Caches'), blocked=f'{sim_booted} simulator(s) booted' if sim_booted else None)
    for kb, p in children(f'{dev}/Xcode/iOS DeviceSupport', min_kb):
        add(g, f'DeviceSupport: {os.path.basename(p)}', 'SAFE', 'device symbols, re-created (several minutes) the next time that device attaches',
            p, kb, cmd=rm_cmd(p), blocked=xb)
    add(g, 'Xcode Archives', 'VERIFY', 'may be the only copy of a shipped build and its dSYMs', f'{dev}/Xcode/Archives')
    if not which('xcrun'):
        NOTES.append('xcrun not found: simulators skipped')
        return
    try:
        devs = json.loads(run(['xcrun', 'simctl', 'list', 'devices', '-j']) or '{}').get('devices', {})
    except ValueError:
        devs = {}
    flat = [(d, rt.replace('com.apple.CoreSimulator.SimRuntime.', '')) for rt, ds in devs.items() for d in ds]
    paths = {d['udid']: f'{dev}/CoreSimulator/Devices/{d["udid"]}' for d, _ in flat}
    with ThreadPoolExecutor(8) as ex:
        sizes = dict(zip(paths, ex.map(du_kb, paths.values())))
    for d, rt in sorted(flat, key=lambda x: -sizes[x[0]['udid']]):
        kb, p = sizes[d['udid']], paths[d['udid']]
        if kb < min_kb:
            continue
        label = f'Simulator "{d["name"]}" ({rt})'
        used = d.get('lastUsedAt')
        a = act(iso_days(used), f'simctl lastUsedAt {used[:10]}') if used and iso_days(used) is not None else None
        if d.get('state') == 'Booted':
            add(g, label, 'KEEP', 'currently booted', p, kb, activity=a)
        elif d.get('isAvailable') is False:
            add(g, label, 'SAFE', 'its runtime is gone, so it cannot boot', p, kb, cmd=f'xcrun simctl delete {d["udid"]}', activity=a,
                key=f'simulator:{d["udid"]}')
        else:
            add(g, label, 'VERIFY', 'shut down; holds installed apps and their data', p, kb,
                cmd=f'xcrun simctl delete {d["udid"]}', activity=a, key=f'simulator:{d["udid"]}')
    try:
        rts = json.loads(run(['xcrun', 'simctl', 'runtime', 'list', '-j']) or '{}')
    except ValueError:
        rts = {}
    for rid, r in rts.items():
        kb = int(r.get('sizeBytes', 0) / 1024)
        if kb < min_kb or not r.get('deletable'):
            continue
        name = f'{r.get("platformIdentifier", "").split(".")[-1]} {r.get("version", "")} ({r.get("build", "")})'.strip()
        used = r.get('lastUsedAt')
        a = act(iso_days(used), f'runtime lastUsedAt {used[:10]}') if used and iso_days(used) is not None else None
        add(g, f'Simulator runtime {name}', 'VERIFY', 'every simulator of this version stops booting without it; re-download is several GB',
            kb=kb, cmd=f'xcrun simctl runtime delete {rid}', activity=a, key=f'sim-runtime:{rid}')


# ---------------------------------------------------------------- android
def android(min_kb):
    g = 'Android'
    emu = running(r'^\S*(qemu-system-\S+|/emulator|/emulator64\S*)( |$)')
    for avd in sorted(glob.glob(f'{H}/.android/avd/*.avd')):
        name = os.path.basename(avd)[:-4]
        snap = f'{avd}/snapshots'
        if os.path.isdir(snap):
            add(g, f'AVD snapshots: {name}', 'SAFE', 'quick-boot snapshots; the AVD cold-boots without them', snap,
                cmd=rm_cmd(snap), blocked=emu)
        kb = du_kb(avd)
        if kb >= min_kb:
            add(g, f'AVD: {name}', 'VERIFY', 'userdata image holds installed apps and data; recreate through the AVD manager',
                avd, kb, cmd=rm_cmd(avd))
    for kb, p in children(f'{H}/Library/Android', min_kb):
        add(g, f'~/Library/Android/{os.path.basename(p)}', 'VERIFY', 'SDK and system images; re-downloaded through sdkmanager', p, kb)


# ---------------------------------------------------------------- docker / colima
def docker(min_kb, trim_min_kb):
    g = 'Docker / Colima'
    if not which('docker') or not succeeds(['docker', 'info'], 15):
        NOTES.append('docker not reachable: Docker/Colima skipped (start Colima to include it)')
        return
    try:
        df = json.loads(run(['docker', 'system', 'df', '-v', '--format', '{{json .}}'], timeout=120) or '{}')
    except ValueError:
        df = {}
    summ = {}
    for line in run(['docker', 'system', 'df', '--format', '{{.Type}}|{{.Size}}|{{.Reclaimable}}']).splitlines():
        t = line.split('|')
        if len(t) == 3:
            summ[t[0]] = docker_kb(t[2])
    old_img = set(run(['docker', 'image', 'ls', '-a', '--no-trunc', '--filter', f'until={DOCKER_AGE}', '-q']).split())
    # Explicit lists captured now, so what is reviewed is what would run (no re-selection at execution time).
    rows = [i for i in df.get('Images', []) if i.get('Containers') == '0' and i.get('ID') in old_img]
    if rows:
        refs = [f'{i["Repository"]}:{i["Tag"]}' if i['Repository'] != '<none>' and i['Tag'] != '<none>' else i['ID'] for i in rows]
        ages = [iso_days(i.get('CreatedAt')) for i in rows if iso_days(i.get('CreatedAt')) is not None]
        names = ', '.join(sorted({i['Repository'] for i in rows if i['Repository'] != '<none>'})[:6])
        add(g, f'{len(rows)} unused images older than {DOCKER_AGE}', 'SAFE',
            'no container references them (checked per image); re-pulled or rebuilt on next use, and locally built ones need their source. '
            'The command has no -f, so docker itself refuses any image that is in use',
            kb=sum(docker_kb(i.get('UniqueSize')) for i in rows), cmd='docker image rm ' + ' '.join(shlex.quote(r) for r in refs),
            activity=act(min(ages), 'newest image created') if ages else None, evidence=[f'images: {names}'] if names else [])
    exited = [l.split('|') for l in run(['docker', 'ps', '-a', '-s', '--filter', 'status=exited', '--filter', 'status=created',
                                         '--format', '{{.ID}}|{{.Size}}']).splitlines() if '|' in l]
    if exited:
        fin = run(['docker', 'inspect', '--format', '{{.Id}}|{{.State.FinishedAt}}'] + [e[0] for e in exited]).splitlines()
        age = {l.split('|')[0][:12]: iso_days(l.split('|')[1]) for l in fin if '|' in l}
        old = [e for e in exited if (age.get(e[0][:12]) or 0) * 24 >= int(DOCKER_AGE[:-1])]
        if old:
            add(g, f'{len(old)} stopped containers older than {DOCKER_AGE}', 'SAFE', 'writable layers only; named volumes are not touched',
                kb=sum(docker_kb(e[1].split(' (')[0]) for e in old), cmd='docker container rm ' + ' '.join(e[0] for e in old),
                activity=act(min(age[e[0][:12]] for e in old), 'newest container finished'))
    add(g, 'Build cache', 'SAFE', 'layer cache older than the age floor; the next build is slower. Size is docker\'s upper bound',
        kb=summ.get('Build Cache', 0), cmd=f'docker builder prune -f --filter until={DOCKER_AGE}', estimate=True,
        activity=act(None, f'docker does not record last use; the filter only keeps layers younger than {DOCKER_AGE}'))
    volumes(g, df.get('Volumes') or [], min_kb)
    # Space freed inside the VM only reaches the Mac after the sparse disk image is trimmed.
    for disk in glob.glob(f'{H}/.colima/_lima/_disks/*/datadisk'):
        if not succeeds(['colima', 'status'], 20):
            continue
        host = du_kb(disk)
        dfl = [l.split() for l in run(['colima', 'ssh', '--', 'df', '-k', '/var/lib/docker'], timeout=30).splitlines() if '/var/lib/docker' in l]
        vm_used = int(dfl[0][2]) if dfl and dfl[0][2].isdigit() else None
        if vm_used is None:
            continue
        gap = max(0, host - vm_used)
        if gap < trim_min_kb:
            NOTES.append(f'Colima image uses {human(host)} on the Mac vs {human(vm_used)} inside: the {human(gap)} gap is filesystem overhead, '
                         f'not worth a trim. Run `colima ssh -- sudo fstrim -av` after pruning volumes or images.')
            continue
        add(g, 'Colima disk image trim (fstrim)', 'SAFE',
            f'image uses {human(host)} on the Mac but Docker only uses {human(vm_used)} inside; run last, after any prune, '
            'because space freed inside the VM only returns to the Mac when the image is trimmed',
            kb=gap, cmd='colima ssh -- sudo fstrim -av', order=9, activity=act(0.0, 'maintenance command, touches no data'))


def volume_newest(names):
    """{volume name: epoch of its newest file}, read inside the Colima VM. A volume with no files, or any failure, is absent."""
    out = {}
    if not names or not which('colima'):
        return out
    script = 'for v in %s; do p=/var/lib/docker/volumes/$v/_data; echo $v $(find $p -type f -printf "%%T@\\n" 2>/dev/null | sort -n | tail -1); done' % ' '.join(map(shlex.quote, names))
    for line in run(['colima', 'ssh', '--', 'sudo', 'sh', '-c', script], timeout=180).splitlines():
        t = line.split()
        if len(t) == 2:
            try:
                out[t[0]] = float(t[1])
            except ValueError:
                pass
    return out


def volumes_key(names):
    """Identity of a group of volumes: the same members give the same key, a changed membership gives a different one."""
    return 'docker-volumes:' + hashlib.sha1('\n'.join(sorted(names)).encode()).hexdigest()[:12]


def load_rulings():
    """The user's keep rulings. A bad file only makes kept items show up again, which is the safe direction for a scan."""
    try:
        return decisions.load()
    except decisions.DecisionsError as e:
        NOTES.append(f'keep rulings ignored for this scan: {e}')
        return {}


def volumes(g, vols, min_kb):
    """Unused volumes: never SAFE. Add staleness (newest write inside the VM), creation date and compose-project state."""
    used = [x for x in vols if x.get('Links') != '0']
    unused = [x for x in vols if x.get('Links') == '0']
    if used:
        add(g, f'{len(used)} volumes mounted by containers', 'KEEP', 'in use by a container (running or stopped)',
            kb=sum(docker_kb(x['Size']) for x in used))
    if not unused:
        return
    names = [x['Name'] for x in unused]
    created, project = {}, {}
    try:
        for v in json.loads(run(['docker', 'volume', 'inspect'] + names, timeout=60) or '[]'):
            created[v['Name']] = v.get('CreatedAt')
            project[v['Name']] = (v.get('Labels') or {}).get('com.docker.compose.project')
    except ValueError:
        pass
    live = {p for p in run(['docker', 'ps', '-a', '--format', '{{.Label "com.docker.compose.project"}}']).split() if p}
    epochs = volume_newest(names)
    rulings = load_rulings()
    caution = 'no container mounts it, but it can hold database data that nothing else has; inspect before removing'
    anon = {'stale': [], 'recent': []}
    small, kept = [], []

    def member(n, kb):
        return {'key': f'docker-volume:{n}', 'name': n, 'kb': kb, 'newest_epoch': epochs.get(n)}

    def group_key(ms):
        return volumes_key([m['name'] for m in ms])

    def newest_days(ms):
        ds = [days_of(m['name']) for m in ms if days_of(m['name']) is not None]
        return act(min(ds) if ds else None, 'newest write across the group')

    def days_of(n):
        return max(0.0, (time.time() - epochs[n]) / DAY) if n in epochs else iso_days(created.get(n))

    for x in unused:
        n, kb = x['Name'], docker_kb(x['Size'])
        days = days_of(n)
        src = 'newest file written in the volume' if n in epochs else 'volume creation date (no file times)'
        a = act(days, src)
        proj = project.get(n)
        ev = [f'created {str(created.get(n))[:10]}'] if created.get(n) else []
        if proj:
            ev.append(f'compose project "{proj}": ' + ('stack still has containers' if proj in live else 'no container from this project exists (orphaned)'))
        grouped = 'anonymous' in (x.get('Labels') or '') or kb < min_kb
        if grouped and decisions.lookup(f'docker-volume:{n}', kb, rulings)[0]:
            kept.append(member(n, kb))  # ruled keep: left out of the group so the group never lists it for removal
        elif 'anonymous' in (x.get('Labels') or ''):
            anon['stale' if a['class'] == 'STALE' else 'recent'].append(member(n, kb))
        elif kb >= min_kb:
            add(g, f'Named volume {n[:26]}{"…" if len(n) > 26 else ""}', 'VERIFY', caution, kb=kb, cmd=f'docker volume rm {n}',
                activity=a, evidence=ev, key=f'docker-volume:{n}', fingerprint={'newest_epoch': epochs.get(n)})
        else:
            small.append(member(n, kb))
    if small:
        add(g, f'{len(small)} more unused named volumes under {min_kb // 1024} MB', 'VERIFY', 'same caution, individually small',
            kb=sum(m['kb'] for m in small), cmd='docker volume rm ' + ' '.join(m['name'] for m in small),
            activity=newest_days(small), key=group_key(small), members=small)
    for slot, title in (('stale', f'unused anonymous volumes not written in {CFG["stale"]}+ days'), ('recent', 'unused anonymous volumes written more recently')):
        grp = anon[slot]
        if grp:
            add(g, f'{len(grp)} {title}', 'VERIFY',
                'left behind by `docker run` or compose without a named volume, often database data dirs; sample a few with '
                '`docker run --rm -v <name>:/v alpine ls /v` before deciding',
                kb=sum(m['kb'] for m in grp), cmd='docker volume rm ' + ' '.join(m['name'] for m in grp),
                activity=newest_days(grp), key=group_key(grp), members=grp,
                evidence=[f'sizes {human(min(m["kb"] for m in grp))} to {human(max(m["kb"] for m in grp))} each'])
    if kept:
        add(g, f'{len(kept)} unused volumes you chose to keep', 'KEEP', 'you ruled keep on these; they come back for review if they grow a lot or the ruling expires',
            kb=sum(m['kb'] for m in kept), key=group_key(kept), members=kept)


# ---------------------------------------------------------------- claude data + app caches
def claude_data(min_kb):
    g = 'Claude data'
    add(g, '~/.claude/projects (session transcripts)', 'VERIFY', 'removing it loses /resume history and saved memory', f'{H}/.claude/projects')


def app_caches(min_kb):
    g = 'App caches'
    for kb, p in children(f'{H}/Library/Caches', min_kb, skip=('Homebrew', 'pip')):
        n = os.path.basename(p)
        if n.startswith('com.apple.'):
            add(g, f'~/Library/Caches/{n}', 'KEEP', 'managed by macOS', p, kb)
        elif n in NEEDS_MANUAL_STEP:
            add(g, f'~/Library/Caches/{n}', 'VERIFY', NEEDS_MANUAL_STEP[n], p, kb, cmd=rm_cmd(p))
        elif n in DEV_CACHES:
            add(g, f'~/Library/Caches/{n}', 'SAFE', 'developer-tool download or compile cache, fetched or rebuilt on demand', p, kb, cmd=rm_cmd(p))
        else:
            add(g, f'~/Library/Caches/{n}', 'VERIFY', 'app-owned cache; quit the app first, it rebuilds on launch', p, kb, cmd=rm_cmd(p))


def overview(min_kb):
    rows = []
    for base in (H, f'{H}/Library'):
        rows += [(kb, p) for kb, p in children(base, max(min_kb, 1024 * 1024))]
    return sorted(rows, reverse=True)


# ---------------------------------------------------------------- gates
def demote(it, tier, reason):
    """Gates only move a tier toward caution: SAFE -> VERIFY -> KEEP."""
    rank = {'SAFE': 0, 'VERIFY': 1, 'KEEP': 2}
    if rank[tier] > rank[it['tier']]:
        it['tier'] = tier
    if reason and reason not in it['evidence']:
        it['evidence'].append(reason)


def gate(it, name, ok, detail):
    it['gates'].append({'name': name, 'result': {True: 'pass', False: 'hold', None: 'unknown'}[ok], 'detail': detail})


def open_file_snapshot():
    """One system-wide lsof, parsed to sorted (path, command, pid, fd). None if lsof is unusable."""
    try:
        r = subprocess.run(['lsof', '-nP', '-Fpcfn'], capture_output=True, text=True, timeout=90)
    except Exception:
        return None
    if not r.stdout:
        return None
    rows, pid, cmd, fd = [], None, None, None
    for line in r.stdout.splitlines():
        k, v = line[:1], line[1:]
        if k == 'p':
            pid = int(v)
        elif k == 'c':
            cmd = v
        elif k == 'f':
            fd = v
        elif k == 'n' and v.startswith('/') and pid != os.getpid() and cmd not in ('lsof', 'ps'):
            rows.append((v, cmd, pid, fd))
    rows.sort()
    return rows


def process_snapshot():
    out = run(['ps', '-axww', '-o', 'pid=,command='])
    procs = []
    for line in out.splitlines():
        t = line.strip().split(None, 1)
        if len(t) == 2 and int(t[0]) != os.getpid():
            procs.append((int(t[0]), os.path.basename(t[1].split()[0]), t[1]))
    return procs


def holders(path, snap):
    found, cwd = {}, set()
    i, j = bisect.bisect_left(snap, (path + '/',)), bisect.bisect_left(snap, (path + '0',))
    k = bisect.bisect_left(snap, (path,))
    for n, cmd, pid, fd in snap[i:j] + (snap[k:k + 1] if k < len(snap) and snap[k][0] == path else []):
        found.setdefault(cmd, set()).add(pid)
        if fd == 'cwd':
            cwd.add(cmd)
    return found, cwd


def find_activity(path):
    """Newest-write class by bounded find with early exit: (days, source)."""
    left = CFG['deadline'] - time.time()
    if left <= 1:
        return None, 'scan time budget reached'
    t = min(20, left)

    def newer(days):
        try:
            r = subprocess.run(['find', path, '-type', 'f', '-mtime', f'-{days}', '-print', '-quit'], capture_output=True, text=True, timeout=t)
            return bool(r.stdout.strip())
        except subprocess.TimeoutExpired:
            return None
    s = newer(CFG['stale'])
    if s is None:
        return None, f'walk timed out after {int(t)}s'
    if not s:
        own = age_days(path)
        if own is not None and own < CFG['stale']:
            return own, 'directory changed recently (entries added or removed)'
        return float(CFG['stale']), f'no file written in {CFG["stale"]}+ days'
    a = newer(CFG['active'])
    if a is None:
        return None, f'walk timed out after {int(t)}s'
    return (0.0, f'a file was written within {CFG["active"]} days') if a else (float(CFG['active']), f'newest write {CFG["active"]} to {CFG["stale"]} days ago')


def git_state(path):
    """Repositories under path (depth <= 3): [(root, dirty, unpushed)]."""
    out = run(['find', path, '-maxdepth', '3', '-name', '.git', '-prune', '-print'], timeout=30)
    repos = []
    for g in out.splitlines()[:5]:
        root = os.path.dirname(g)
        dirty = len([x for x in run(['git', '-C', root, 'status', '--porcelain'], timeout=30).splitlines() if x])
        un = run(['git', '-C', root, 'rev-list', '--count', 'HEAD', '--not', '--remotes'], timeout=30).strip()
        repos.append((root, dirty, int(un) if un.isdigit() else None))
    return repos


def apply_gates(items):
    snap, procs = open_file_snapshot(), process_snapshot()
    if snap is None:
        NOTES.append('lsof unavailable: the open-file gate could not run, so every SAFE item is held (fail closed)')

    def activity_of(it):
        if it['activity']:
            return it['activity']
        target = it['path'] or it['probe']
        if target and os.path.exists(target):
            days, src = find_activity(target)
            return act(days, src)
        return act(None, 'no timestamp available')
    with ThreadPoolExecutor(8) as ex:
        acts = list(ex.map(activity_of, items))
    for it, a in zip(items, acts):
        it['activity'] = a
        p = it['path']
        target = os.path.realpath(p or it['probe']) if (p or it['probe']) else None
        # G1: the path must be a real directory inside the home folder, not a symlink or a shallow path
        if p and it['cmds']:
            ok = not os.path.islink(p) and os.path.realpath(p).startswith(H + os.sep) and os.path.realpath(p).count(os.sep) - H.count(os.sep) >= 2
            gate(it, 'path-sanity', ok, 'real path inside the home folder, not a symlink' if ok else 'symlink, outside home, or too shallow')
            if not ok:
                demote(it, 'KEEP', 'path failed the sanity gate')
        # G2: open files and processes that reference the path
        if target:
            if snap is None:
                gate(it, 'in-use', None, 'lsof unavailable')
                if it['tier'] == 'SAFE':
                    it['blocked'] = it['blocked'] or 'open-file check unavailable'
            else:
                held, cwd = holders(target, snap)
                rx = re.compile(re.escape(target) + r'(?=$|[/\s:=])')
                cmdline = {c for _, c, line in procs if rx.search(line)}
                names = {f'{c}×{len(v)}' for c, v in held.items()} | {f'{c} (command line)' for c in cmdline if c not in held}
                if names:
                    msg = 'in use by ' + ', '.join(sorted(names)) + (f'; working directory of {", ".join(sorted(cwd))}' if cwd else '')
                    gate(it, 'in-use', False, msg)
                    if it['tier'] == 'SAFE':
                        it['blocked'] = it['blocked'] or msg
                    else:
                        it['evidence'].append(msg)
                else:
                    gate(it, 'in-use', True, 'no open files or process command lines reference it')
        if it['blocked'] and not any(g['name'] == 'in-use' and g['result'] == 'hold' for g in it['gates']):
            gate(it, 'process-check', False, it['blocked'])
        # G3: a git repository inside must not hold uncommitted or unpushed work. "Cannot tell" is recorded but does not
        # demote (tool caches hold odd .git folders); definite work does: KEEP for authored data, otherwise VERIFY.
        if p and os.path.isdir(p) and it['tier'] != 'KEEP':
            repos = git_state(p)
            bad = [r for r in repos if r[1] or (r[2] or 0) > 0]
            unknown = [r for r in repos if not r[1] and r[2] is None]
            if repos:
                gate(it, 'git-state', None if (unknown and not bad) else not bad,
                     '; '.join(f'{os.path.relpath(r, p)}: {d} changed, {"?" if u is None else u} unpushed' for r, d, u in repos))
            if bad:
                demote(it, 'KEEP' if it['group'] == 'Claude data' else 'VERIFY',
                       'contains a git repository with uncommitted or unpushed work: ' + ', '.join(os.path.relpath(r[0], p) for r in bad))
        # G4: key material means a human decides
        if p and os.path.isdir(p) and it['tier'] != 'KEEP':
            key = has_key_material(p)
            gate(it, 'key-material', key is None, f'found {os.path.basename(key)}' if key else 'none in the first two levels')
            if key:
                demote(it, 'VERIFY', f'contains possible key material ({os.path.basename(key)}): never auto-reclaimed')
        # G5: activity can hold a SAFE item back, it never promotes anything
        gate(it, 'activity', None if a['class'] == 'UNKNOWN' else a['class'] != 'ACTIVE', f'{a["class"].lower()}: {a["source"]}')
        # fingerprint so a later executor can tell the item changed after this scan
        if p and os.path.exists(p):
            try:
                it['fingerprint'] = {'mtime': int(os.stat(p).st_mtime), 'kb': it['kb']}
            except OSError:
                pass


def apply_decisions(items):
    """A live keep ruling demotes the item to KEEP, like any other gate: toward caution only. A ruling that has lapsed
    (expired, or the item grew a lot) leaves the item as it was and says why it is back."""
    rulings = load_rulings()
    for it in items:
        if not it.get('key') or it.get('members'):  # group members were ruled on while the groups were built
            continue
        d, lapsed = decisions.lookup(it['key'], it['kb'], rulings)
        if d:
            when = time.strftime('%Y-%m-%d', time.localtime(d['time']))
            msg = f'you chose to keep this on {when}' + (f': {d["note"]}' if d.get('note') else '')
            gate(it, 'decision', False, msg)
            demote(it, 'KEEP', msg)
        elif lapsed:
            gate(it, 'decision', True, lapsed)
            it['evidence'].append(lapsed)


# ---------------------------------------------------------------- report
def free_kb():
    out = run(['df', '-k', '/System/Volumes/Data' if os.path.isdir('/System/Volumes/Data') else '/']).splitlines()
    try:
        f = out[-1].split()
        return int(f[1]), int(f[3])
    except Exception:
        return 0, 0


def bucket(it):
    """SAFE items split three ways; the others report their tier."""
    if it['tier'] != 'SAFE':
        return it['tier']
    if it['blocked']:
        return 'SAFE-BLOCKED'
    return 'SAFE-STALE' if it['activity']['class'] == 'STALE' else 'SAFE-USED'


def bars(parts, total, width):
    """Block bar: SAFE solid, VERIFY medium, KEEP light, so tiers survive without colour."""
    out = ''
    n = max(1, round(width * total / max(1, parts['max']))) if total else 0
    for tier, ch in (('SAFE', '█'), ('VERIFY', '▒'), ('KEEP', '░')):
        k = parts.get(tier, 0)
        out += ch * (round(n * k / total) if total else 0)
    return out.ljust(1, '·')


def print_markdown(items, free, total, over, a):
    sums = {t: sum(i['kb'] or 0 for i in items if i['tier'] == t) for t in ('SAFE', 'VERIFY', 'KEEP')}
    b = {k: sum(i['kb'] or 0 for i in items if bucket(i) == k) for k in ('SAFE-STALE', 'SAFE-USED', 'SAFE-BLOCKED')}
    print(f'# System storage report\n\nFree: **{human(free)}** of {human(total)}. Read-only: nothing was changed.\n')
    print(f'| Tier | Items | Size |\n|---|---|---|')
    for t in ('SAFE', 'VERIFY', 'KEEP'):
        print(f'| {t} | {len([i for i in items if i["tier"] == t])} | {human(sums[t])} |')
    print(f'\nSAFE splits into **{human(b["SAFE-STALE"])} stale** (reclaim freely), **{human(b["SAFE-USED"])} recently used** '
          f'(reclaimable, but you will feel the rebuild) and {human(b["SAFE-BLOCKED"])} blocked by something in use right now.')
    groups = [g for g in GROUP_ORDER if any(i['group'] == g for i in items)]
    gt = {g: {t: sum(i['kb'] or 0 for i in items if i['group'] == g and i['tier'] == t) for t in ('SAFE', 'VERIFY', 'KEEP')} for g in groups}
    mx = max((sum(v.values()) for v in gt.values()), default=1)
    print('\n## Where the space is\n\n```')
    for g in groups:
        tot = sum(gt[g].values())
        print(f'{g:<22} {bars(dict(gt[g], max=mx), tot, 34):<34} {human(tot)}')
    print('\n█ SAFE   ▒ needs review   ░ leave alone\n```')
    cls = ('ACTIVE', 'RECENT', 'STALE', 'UNKNOWN')
    print(f'\n## How much is in use (size by tier and activity)\n\n| Tier | Active (<{a.active_days}d) | Recent (<{a.stale_days}d) | Stale ({a.stale_days}d+) | Unknown |\n|---|---|---|---|---|')
    for t in ('SAFE', 'VERIFY', 'KEEP'):
        print(f'| {t} | ' + ' | '.join(human(sum(i['kb'] or 0 for i in items if i['tier'] == t and i['activity']['class'] == c)) for c in cls) + ' |')
    top = sorted(items, key=lambda i: -(i['kb'] or 0))[:8]
    if top:
        print('\n## Biggest single items\n\n```')
        mk = top[0]['kb'] or 1
        for i in top:
            ch = {'SAFE': '█', 'VERIFY': '▒', 'KEEP': '░'}[i['tier']]
            print(f'{i["label"][:46]:<46} {ch * max(1, round(24 * (i["kb"] or 0) / mk)):<24} {human(i["kb"] or 0)}  {i["activity"]["class"].lower()}')
        print('```')
    for key, title in (('SAFE-STALE', 'Safe and stale: reclaim freely'), ('SAFE-USED', 'Safe but recently used: you will feel the rebuild'),
                       ('SAFE-BLOCKED', 'Safe, but blocked right now')):
        rows = [i for i in items if bucket(i) == key]
        if rows:
            print(f'\n## {title}\n\n| Size | Item | Activity | Why | {"Blocked by" if key == "SAFE-BLOCKED" else "Gates"} |\n|---|---|---|---|---|')
            for i in rows:
                gates = i['blocked'] if key == 'SAFE-BLOCKED' else ', '.join(f'{g["name"]} {g["result"]}' for g in i['gates'])
                print(f'| {human(i["kb"] or 0)}{"~" if i["estimate"] else ""} | {i["label"]} | {i["activity"]["class"].lower()} ({i["activity"]["source"]}) | {i["why"]} | {gates} |')
    for tier, title in (('VERIFY', 'Needs your review first'), ('KEEP', 'Leave alone')):
        rows = [i for i in items if i['tier'] == tier]
        if rows:
            print(f'\n## {title}\n\n| Size | Item | Activity | Evidence | Why |\n|---|---|---|---|---|')
            for i in rows:
                ev = '; '.join(i['evidence'] + ([i['blocked']] if i['blocked'] else []))
                print(f'| {human(i["kb"] or 0)} | {i["label"]} | {i["activity"]["class"].lower()} ({i["activity"]["source"]}) | {ev} | {i["why"]} |')
    if over:
        print('\n## Biggest folders (context only, not proposals)\n')
        for kb, p in over:
            print(f'- {human(kb)}  {p.replace(H, "~")}')
    ready = sorted((i for i in items if bucket(i) in ('SAFE-STALE', 'SAFE-USED') and i['cmds']), key=lambda i: (i['order'], -(i['kb'] or 0)))
    if ready:
        print('\n## Proposed commands for the SAFE items (not executed)\n\n```bash')
        for i in ready:
            for c in i['cmds']:
                print(f'{c if len(c) < 170 else c[:150] + " … (full list in --json)"}   # {human(i["kb"] or 0)}  {i["label"]}')
        print('```\n\nRun them with `execute_system.py` (dry run by default): it re-runs the gates immediately before each command, because the open-file and process checks describe this moment only.')
    for n in NOTES:
        print(f'\n> Note: {n}')


def env_num(name, default):
    """A number from the environment (so a hook's thresholds can be set in settings.json), else the default."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--min-mb', type=float, default=100, help='list individual items at least this big')
    ap.add_argument('--max-seconds', type=int, default=240)
    ap.add_argument('--active-days', type=int, default=7, help='written within this many days = ACTIVE')
    ap.add_argument('--stale-days', type=int, default=30, help='not written for this many days = STALE')
    ap.add_argument('--trim-min-gb', type=float, default=2, help='list the Colima trim only if it would return at least this much')
    ap.add_argument('--overview', action='store_true', help='also list the biggest folders in ~ and ~/Library (slow)')
    ap.add_argument('--html', metavar='FILE', help='also write a self-contained HTML report with a treemap')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--status', action='store_true', help='fast status only (free space, trend, last scan and cleanup); no scan. Exit 1 if attention is needed')
    ap.add_argument('--quiet-if-ok', action='store_true', help='with --status: print nothing unless something needs attention (for hooks)')
    ap.add_argument('--hook', action='store_true', help='with --status: print the SessionStart hook JSON (a one-line summary for the user, and model context only when space is low). Never fails')
    ap.add_argument('--warn-free-gb', type=float, default=env_num('DISK_RECLAIM_WARN_FREE_GB', 50),
                    help='with --status: warn below this much free space (default 50, or $DISK_RECLAIM_WARN_FREE_GB)')
    ap.add_argument('--warn-free-pct', type=float, default=env_num('DISK_RECLAIM_WARN_FREE_PCT', 10),
                    help='with --status: warn below this percentage free (default 10, or $DISK_RECLAIM_WARN_FREE_PCT)')
    ap.add_argument('--max-scan-age-days', type=int, default=14, help='with --status: warn when the last saved scan is older')
    ap.add_argument('--save-state', action='store_true', help='record a summary of this scan so --status can report it (use for scheduled scans)')
    a = ap.parse_args()
    if a.status:
        from disk_status import status
        source = None
        if a.hook and not sys.stdin.isatty():
            try:
                source = json.load(sys.stdin).get('source')
            except Exception:
                pass
        try:
            sys.exit(status(a.warn_free_gb, a.warn_free_pct, a.max_scan_age_days, a.quiet_if_ok, a.hook, source))
        except SystemExit:
            raise
        except Exception:
            if not a.hook:
                raise
            sys.exit(0)
    if sys.platform != 'darwin':
        sys.exit('scan_system.py knows macOS locations only (~/Library, Xcode, Colima). '
                 'Use scan.py for a directory on any system, or --status for free space and trend.')
    min_kb = int(a.min_mb * 1024)
    CFG.update(active=a.active_days, stale=a.stale_days, deadline=time.time() + a.max_seconds)
    for fn in (tool_caches, xcode, android, claude_data, app_caches):
        fn(min_kb)
    docker(min_kb, int(a.trim_min_gb * 1048576))
    todo = [i for i in ITEMS if i['kb'] is None and i['path']]
    with ThreadPoolExecutor(8) as ex:
        for i, kb in zip(todo, ex.map(lambda i: du_kb(i['path']) if os.path.exists(i['path']) else 0, todo)):
            i['kb'] = kb
    items = [i for i in ITEMS if (i['kb'] or 0) >= (min_kb if i['tier'] != 'SAFE' else 1024)]
    apply_gates(items)
    apply_decisions(items)
    items.sort(key=lambda i: (GROUP_ORDER.index(i['group']), i['order'], -(i['kb'] or 0)))
    for n, i in enumerate(items):
        i['id'] = f'{n:03d}-' + re.sub(r'[^a-z0-9]+', '-', i['label'].lower())[:40].strip('-')
        i['bucket'] = bucket(i)
    total, free = free_kb()
    over = overview(min_kb) if a.overview else []
    data = {'total_kb': total, 'free_kb': free, 'items': items, 'notes': NOTES, 'overview': over,
            'thresholds': {'active_days': a.active_days, 'stale_days': a.stale_days}}
    if a.save_state:
        from disk_status import save_scan
        save_scan(data)
    if a.html:
        from system_html import render
        with open(a.html, 'w') as f:
            f.write(render(data))
    if a.json:
        print(json.dumps(data, indent=1))
        return
    print_markdown(items, free, total, over, a)
    if a.html:
        print(f'\nHTML report written to {a.html}')


if __name__ == '__main__':
    main()
