#!/usr/bin/env python3
"""Read-only storage report: where the space goes and what can be reclaimed safely.

Never deletes, moves or modifies anything. Standard library only; needs `du`,
and `git` for repository-aware checks. Works on macOS and Linux.

Usage: scan.py [DIR] [--top N] [--max-depth N] [--min-mb N] [--large-mb N]
                      [--max-seconds N] [--json]
"""
import argparse, json, os, shlex, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

# Directory names that are always derived output / caches, with how to rebuild them.
REGEN = {
    'node_modules': ('dependency cache', 'reinstall (npm/pnpm/yarn install)', ('package.json',)),
    '__pycache__': ('build cache', 'recreated by Python', ()),
    '.pytest_cache': ('build cache', 'recreated by pytest', ()),
    '.mypy_cache': ('build cache', 'recreated by mypy', ()),
    '.ruff_cache': ('build cache', 'recreated by ruff', ()),
    '.tox': ('build cache', 'recreated by tox', ()),
    '.gradle': ('build cache', 'recreated by Gradle', ()),
    '.kotlin': ('build cache', 'recreated by the Kotlin compiler', ()),
    '.next': ('build output', 'rebuild (next build/dev)', ()),
    '.nuxt': ('build output', 'rebuild (nuxt build/dev)', ()),
    '.angular': ('build cache', 'recreated by the Angular CLI', ()),
    '.turbo': ('build cache', 'recreated by turbo', ()),
    '.parcel-cache': ('build cache', 'recreated by parcel', ()),
    'DerivedData': ('build output', 'recreated by Xcode', ()),
}
# Names that are build output in some projects and source in others: only safe when
# git-ignored with no tracked files inside, and a build manifest sits next to them.
AMBIG = {
    'build': ('build output', ('build.gradle', 'build.gradle.kts', 'pom.xml', 'package.json', 'CMakeLists.txt', 'Makefile', 'setup.py')),
    'dist': ('build output', ('package.json', 'setup.py', 'pyproject.toml')),
    'out': ('build output', ('package.json', 'build.gradle', 'pom.xml')),
    'target': ('build output', ('pom.xml', 'Cargo.toml', 'build.sbt')),
    'bin': ('build output', ('build.gradle', 'pom.xml', '.project', '.classpath')),
    'obj': ('build output', ('*.csproj',)),
    'coverage': ('build output', ('package.json',)),
    'Pods': ('dependency cache', ('Podfile',)),
    'vendor': ('dependency cache', ('go.mod', 'composer.json', 'Gemfile')),
    'venv': ('dependency cache', ('requirements.txt', 'pyproject.toml', 'setup.py')),
    '.venv': ('dependency cache', ('requirements.txt', 'pyproject.toml', 'uv.lock', 'setup.py')),
}
LOG_DIRS = {'logs', 'log'}
KEY_EXT = ('.p12', '.pem', '.key', '.jks', '.keystore', '.pfx')
KEY_NAMES = ('.env', '.npmrc', '.netrc', 'credentials')


def run(cmd, cwd=None, timeout=60, inp=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode in (0, 1) else ''
    except Exception:
        return ''


def du_kb(path):
    out = run(['du', '-sk', '--', path], timeout=300)
    try:
        return int(out.split()[0])
    except Exception:
        return 0


def human(kb):
    v = float(kb)
    for unit in ('KB', 'MB', 'GB', 'TB'):
        if v < 1024 or unit == 'TB':
            return f'{v:.0f} {unit}' if unit == 'KB' else f'{v:.1f} {unit}'
        v /= 1024


class Repo:
    """Lazy per-repository facts (tracked directories, ignore checks)."""
    def __init__(self, root):
        self.root = root
        self._tracked = None

    def tracked_dirs(self):
        if self._tracked is None:
            s = set()
            out = run(['git', '-C', self.root, 'ls-files', '-z'], timeout=120)
            for f in out.split('\0'):
                if not f:
                    continue
                d = os.path.dirname(f)
                while d and d not in s:
                    s.add(d)
                    d = os.path.dirname(d)
            self._tracked = s
            self._files = None
        return self._tracked

    def tracked_files(self):
        if getattr(self, '_files', None) is None:
            self.tracked_dirs()
            out = run(['git', '-C', self.root, 'ls-files', '-z'], timeout=120)
            self._files = set(x for x in out.split('\0') if x)
        return self._files

    def ignored(self, rels):
        if not rels:
            return set()
        out = run(['git', '-C', self.root, 'check-ignore', '--stdin', '-z'], inp='\0'.join(rels) + '\0', timeout=120)
        return set(x for x in out.split('\0') if x)


def has_key_material(path, depth=2):
    base = path.rstrip('/').count(os.sep)
    for dp, dns, fns in os.walk(path):
        if dp.count(os.sep) - base >= depth:
            dns[:] = []
        for fn in fns:
            low = fn.lower()
            if low.endswith(KEY_EXT) or low in KEY_NAMES or low.startswith('.env'):
                return os.path.join(dp, fn)
    return None


def manifest_nearby(parent, names):
    if not names:
        return True
    try:
        entries = os.listdir(parent)
    except OSError:
        return False
    for n in names:
        if n.startswith('*'):
            if any(e.endswith(n[1:]) for e in entries):
                return True
        elif n in entries:
            return True
    return False


def walk(target, max_depth, deadline):
    """Find candidate directories and repository roots without descending into them."""
    cands, repos, notes = [], {}, []
    tbase = target.rstrip(os.sep).count(os.sep)
    for dp, dns, fns in os.walk(target, followlinks=False):
        if time.time() > deadline:
            notes.append('Time budget reached: the scan is partial (raise --max-seconds for the rest).')
            dns[:] = []
            break
        depth = dp.count(os.sep) - tbase
        if '.git' in dns or '.git' in fns:
            repos[dp] = Repo(dp)
        dns[:] = [d for d in dns if d != '.git']
        keep = []
        for d in dns:
            full = os.path.join(dp, d)
            if os.path.islink(full):
                continue
            if d in REGEN:
                cands.append({'path': full, 'name': d, 'kind': 'regen', 'category': REGEN[d][0], 'hint': REGEN[d][1], 'manifest': REGEN[d][2]})
            elif d in AMBIG:
                cands.append({'path': full, 'name': d, 'kind': 'ambig', 'category': AMBIG[d][0], 'hint': 'rebuild from source', 'manifest': AMBIG[d][1]})
            elif d in LOG_DIRS:
                cands.append({'path': full, 'name': d, 'kind': 'logs', 'category': 'logs', 'hint': 'logs are rarely needed after the run', 'manifest': ()})
            elif depth < max_depth:
                keep.append(d)
        dns[:] = keep
    return cands, repos, notes


def nearest_repo(path, repos):
    best = None
    for root in repos:
        if (path == root or path.startswith(root + os.sep)) and (best is None or len(root) > len(best)):
            best = root
    return best


def classify(c, repos):
    path, parent = c['path'], os.path.dirname(c['path'])
    root = nearest_repo(path, repos)
    c['repo'] = root
    key = has_key_material(path)
    if key:
        return 'KEEP', f'contains possible credentials/key material ({os.path.basename(key)})'
    if root:
        repo, rel = repos[root], os.path.relpath(path, root)
        if rel in repo.tracked_dirs():
            return 'KEEP', 'contains files tracked by git'
        ign = repo.ignored([rel])
        c['ignored'] = rel in ign
        if c['kind'] == 'logs':
            return 'VERIFY', 'logs: check nothing is being debugged before removing'
        if not c['ignored']:
            return 'VERIFY', 'not git-ignored, so it may be intentional (untracked)'
        if c['kind'] == 'ambig' and not manifest_nearby(parent, c['manifest']):
            return 'VERIFY', 'git-ignored but no build manifest next to it, cannot confirm it is rebuildable'
        return 'SAFE', f"git-ignored, no tracked files inside; {c['hint']}"
    if c['kind'] == 'logs':
        return 'VERIFY', 'logs outside git: check nothing is being debugged'
    if not manifest_nearby(parent, c['manifest']):
        return 'VERIFY', 'outside git and no manifest next to it, cannot confirm it is rebuildable'
    if c['kind'] == 'ambig':
        return 'VERIFY', 'outside git: may be hand-made, check before removing'
    return 'SAFE', f"outside git, derived output; {c['hint']}"


def git_out(root, *args):
    return run(['git', '-C', root, *args], timeout=120).strip()


def worktrees(repos, target):
    """Describe every linked worktree of repos found under target."""
    seen, rows = set(), []
    for root in repos:
        common = git_out(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if not common or common in seen:
            continue
        seen.add(common)
        porc = run(['git', '--git-dir', common, 'worktree', 'list', '--porcelain'], timeout=60)
        entries, cur = [], {}
        for line in porc.splitlines() + ['']:
            if not line:
                if cur:
                    entries.append(cur); cur = {}
                continue
            k, _, v = line.partition(' ')
            cur[k] = v
        main_path = None
        for e in entries:
            if 'bare' in e:
                continue
            p = e.get('worktree')
            if not p or not os.path.isdir(p):
                continue
            if main_path is None and os.path.isdir(os.path.join(p, '.git')):
                main_path = p
            if not (p.startswith(target.rstrip(os.sep) + os.sep) or p == target):
                continue
            is_main = os.path.isdir(os.path.join(p, '.git'))
            br = e.get('branch', '').replace('refs/heads/', '') or '(detached)'
            mods = len([l for l in run(['git', '-C', p, 'status', '--porcelain', '--untracked-files=no'], timeout=120).splitlines() if l])
            unt = len([l for l in run(['git', '-C', p, 'ls-files', '--others', '--exclude-standard'], timeout=120).splitlines() if l])
            unp = git_out(p, 'rev-list', '--count', 'HEAD', '--not', '--remotes') or '?'
            locked = 'locked' in e
            reasons = []
            if is_main: reasons.append('main checkout')
            if mods: reasons.append(f'{mods} modified tracked file(s)')
            if unt: reasons.append(f'{unt} untracked file(s)')
            if unp not in ('0', '?'): reasons.append(f'{unp} commit(s) on no remote branch')
            if unp == '?': reasons.append('cannot tell if commits are pushed (no remote?)')
            if locked: reasons.append('locked')
            tier = 'KEEP' if reasons else 'VERIFY'
            rows.append({'path': p, 'branch': br, 'common': common, 'is_main': is_main, 'kb': 0, 'tier': tier,
                         'why': '; '.join(reasons) if reasons else 'clean and every commit is on a remote branch; removing keeps the branch',
                         'cmd': None if reasons else f"git --git-dir {shlex.quote(common)} worktree remove {shlex.quote(p)}"})
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r, kb in zip(rows, ex.map(lambda r: du_kb(r['path']), rows)):
            r['kb'] = kb
    return rows


def large_files(target, mb, cands, repos, limit):
    skip = [c['path'] + os.sep for c in cands]
    out = run(['find', target, '-type', 'f', '-size', f'+{mb}M', '-not', '-path', '*/.git/*'], timeout=300)
    rows = []
    for f in out.splitlines():
        if any(f.startswith(s) for s in skip):
            continue
        try:
            kb = os.path.getsize(f) // 1024
        except OSError:
            continue
        root = nearest_repo(f, repos)
        status = 'not in a git repo'
        if root:
            rel = os.path.relpath(f, root)
            status = 'tracked' if rel in repos[root].tracked_files() else ('git-ignored' if repos[root].ignored([rel]) else 'untracked')
        rows.append({'path': f, 'kb': kb, 'status': status, 'tier': 'KEEP' if status == 'tracked' else 'VERIFY'})
    rows.sort(key=lambda r: -r['kb'])
    return rows[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', nargs='?', default='.')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--max-depth', type=int, default=6)
    ap.add_argument('--min-mb', type=float, default=20, help='list candidates at least this big individually')
    ap.add_argument('--large-mb', type=int, default=100)
    ap.add_argument('--max-seconds', type=int, default=180)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    target = os.path.realpath(a.dir)
    if not os.path.isdir(target):
        sys.exit(f'not a directory: {target}')
    if not shutil.which('du'):
        sys.exit('`du` is required')
    have_git = bool(shutil.which('git'))
    t0 = time.time()

    cands, repos, notes = walk(target, a.max_depth, t0 + a.max_seconds)
    if not have_git:
        repos = {}
        notes.append('git not found: repository checks skipped, so everything is at most VERIFY.')
    with ThreadPoolExecutor(max_workers=8) as ex:
        sizes = list(ex.map(lambda c: du_kb(c['path']), cands))
    for c, kb in zip(cands, sizes):
        c['kb'] = kb
        c['tier'], c['why'] = classify(c, repos)
        if not have_git and c['tier'] == 'SAFE':
            c['tier'], c['why'] = 'VERIFY', 'git unavailable, could not check for tracked files'
        c['cmd'] = None if c['tier'] == 'KEEP' else f"rm -rf -- {shlex.quote(c['path'])}"
    cands.sort(key=lambda c: -c['kb'])
    wts = worktrees(repos, target) if have_git else []
    big = large_files(target, a.large_mb, cands, repos, a.top)

    total = du_kb(target)
    top = []
    for line in run(['du', '-k', '-d', '1', target], timeout=300).splitlines():
        kb, _, p = line.partition('\t')
        if p and os.path.realpath(p) != target:
            top.append((int(kb), p))
    top.sort(reverse=True)
    df = run(['df', '-k', target]).splitlines()
    free = int(df[-1].split()[3]) if len(df) > 1 else 0

    if a.json:
        print(json.dumps({'target': target, 'total_kb': total, 'free_kb': free, 'top': top[:a.top], 'candidates': cands,
                          'worktrees': wts, 'large_files': big, 'notes': notes}, indent=1))
        return

    rel = lambda p: os.path.relpath(p, target) if p != target else '.'
    print(f'# Storage report: {target}\n')
    print(f'Total {human(total)} in this directory; {human(free)} free on its volume. Scan took {time.time() - t0:.0f}s.\n')
    print(f'## Largest subdirectories (top {a.top})\n\n| Size | Path |\n|---:|---|')
    for kb, p in top[:a.top]:
        print(f'| {human(kb)} | {rel(p)} |')

    print('\n## Reclaimable by safety tier\n')
    print('SAFE = derived output, git-ignored, rebuildable. VERIFY = probably fine but needs a look. KEEP = do not remove.\n')
    print('| Tier | Category | Items | Size |\n|---|---|---:|---:|')
    agg = {}
    for c in cands:
        k = (c['tier'], c['category'])
        n, kb = agg.get(k, (0, 0)); agg[k] = (n + 1, kb + c['kb'])
    for r in wts:
        if r['is_main']:
            continue  # the main checkout contains the candidates above; counting it would double-count
        k = (r['tier'], 'git worktree'); n, kb = agg.get(k, (0, 0)); agg[k] = (n + 1, kb + r['kb'])
    order = {'SAFE': 0, 'VERIFY': 1, 'KEEP': 2}
    for (tier, cat), (n, kb) in sorted(agg.items(), key=lambda kv: (order[kv[0][0]], -kv[1][1])):
        print(f'| {tier} | {cat} | {n} | {human(kb)} |')
    safe_kb = sum(c['kb'] for c in cands if c['tier'] == 'SAFE')
    ver_kb = sum(c['kb'] for c in cands if c['tier'] == 'VERIFY') + sum(r['kb'] for r in wts if r['tier'] == 'VERIFY')
    print(f'\nSAFE total {human(safe_kb)}; VERIFY total {human(ver_kb)}.')

    shown = [c for c in cands if c['kb'] >= a.min_mb * 1024][:a.top]
    print(f'\n## Largest candidates (>= {a.min_mb:g} MB, top {a.top})\n\n| Size | Tier | Path | Why |\n|---:|---|---|---|')
    for c in shown:
        print(f"| {human(c['kb'])} | {c['tier']} | {rel(c['path'])} | {c['why']} |")
    rest = [c for c in cands if c not in shown]
    if rest:
        print(f'\n{len(rest)} smaller candidate(s) not listed, {human(sum(c["kb"] for c in rest))} combined (use --min-mb 0 --top 200 to see them).')

    if wts:
        print('\n## Git worktrees under this directory\n\n| Size | Tier | Worktree | Branch | Why |\n|---:|---|---|---|---|')
        for r in sorted(wts, key=lambda r: -r['kb']):
            print(f"| {human(r['kb'])} | {r['tier']} | {rel(r['path'])} | {r['branch']} | {r['why']} |")
    if big:
        print(f'\n## Large files (>= {a.large_mb} MB, outside the candidates above)\n\n| Size | Tier | Path | Git status |\n|---:|---|---|---|')
        for r in big:
            print(f"| {human(r['kb'])} | {r['tier']} | {rel(r['path'])} | {r['status']} |")

    cmds = [c for c in cands if c['cmd'] and c['tier'] == 'SAFE'][:a.top]
    if cmds or any(r['cmd'] for r in wts):
        print('\n## Proposed commands (NOT run; confirm first)\n')
        for c in cmds:
            print(f"- {human(c['kb'])}: `{c['cmd']}`")
        for r in sorted(wts, key=lambda r: -r['kb']):
            if r['cmd']:
                print(f"- {human(r['kb'])} (worktree, verify first): `{r['cmd']}`")
    print('\n## Notes\n')
    for n in notes + ['Sizes come from `du` (blocks used); hard-linked or copy-on-write files (pnpm store, APFS clones) may make real savings smaller than shown.',
                      'Run the scan again right before deleting: another process may have changed things.']:
        print(f'- {n}')


if __name__ == '__main__':
    main()
