#!/usr/bin/env python3
"""Delete SAFE, regenerable directories found by scan.py. Dry run unless --execute.

Every item is re-verified at this moment with the same rules scan.py uses (still SAFE:
git-ignored, no tracked files inside, no key material, manifest beside it), so a stale
scan cannot cause a deletion. Only directories with known derived-output names are removed.

Default policy keeps build output in high-traffic checkouts: a checkout whose .git is a
directory (a normal clone) and any worktree whose folder is named `main` are protected, so
only linked worktrees and other low-traffic copies are purged. Override with --no-protect.

Usage: reclaim.py SCAN.json [--categories "build cache,build output,dependency cache"]
                            [--exclude SUBSTR ...] [--protect-name NAME ...] [--no-protect]
                            [--min-mb N] [--execute]
Produce SCAN.json with: scan.py DIR --json > SCAN.json
"""
import argparse, json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan  # noqa: E402


def checkout_root(path):
    d = os.path.dirname(path)
    while d and d != os.sep:
        if os.path.exists(os.path.join(d, '.git')):
            return d
        d = os.path.dirname(d)
    return None


def protection(path, extra_names):
    root = checkout_root(path)
    if root is None:
        return None
    if os.path.isdir(os.path.join(root, '.git')):
        return 'normal clone (kept ready to build)'
    if os.path.basename(root) in ('main', 'master') or os.path.basename(root) in extra_names:
        return f"worktree named {os.path.basename(root)!r} (kept ready to build)"
    return None


def busy_checkouts(roots):
    out = scan.run(['lsof', '-a', '-d', 'cwd', '-Fn'], timeout=60)
    cwds = [l[1:] for l in out.splitlines() if l.startswith('n')]
    return {r for r in roots if any(c == r or c.startswith(r + os.sep) for c in cwds)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scan_json')
    ap.add_argument('--categories', default='build cache,build output,dependency cache')
    ap.add_argument('--exclude', action='append', default=[])
    ap.add_argument('--protect-name', action='append', default=[])
    ap.add_argument('--no-protect', action='store_true')
    ap.add_argument('--min-mb', type=float, default=0)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--log', default='reclaim-log.tsv')
    a = ap.parse_args()

    data = json.load(open(a.scan_json))
    target = data['target']
    cats = {c.strip() for c in a.categories.split(',')}
    allowed = set(scan.REGEN) | set(scan.AMBIG)

    plan, skipped = [], {}
    def skip(why, c=None):
        skipped.setdefault(why, []).append(c)
    pre = [c for c in data['candidates'] if c['tier'] == 'SAFE' and c['category'] in cats and c['kb'] >= a.min_mb * 1024]
    roots = {checkout_root(c['path']) for c in pre} - {None}
    busy = busy_checkouts(roots)
    repos = {r: scan.Repo(r) for r in roots}
    for c in pre:
        p = c['path']
        if not p.startswith(target + os.sep) or not os.path.isdir(p) or os.path.islink(p):
            skip('missing or not a plain directory', c); continue
        if os.path.basename(p) not in allowed:
            skip('name is not a known derived-output name', c); continue
        if any(x in p for x in a.exclude):
            skip('excluded by --exclude', c); continue
        why = None if a.no_protect else protection(p, set(a.protect_name))
        if why:
            skip(f'protected: {why}', c); continue
        root = checkout_root(p)
        if root in busy:
            skip('a process is running inside this checkout', c); continue
        tier, reason = scan.classify(dict(c), repos)
        if tier != 'SAFE':
            skip(f're-check no longer SAFE: {reason}', c); continue
        plan.append(c)

    total = sum(c['kb'] for c in plan)
    areas = {}
    for c in plan:
        top = os.path.relpath(c['path'], target).split(os.sep)
        key = '/'.join(top[:2]) if len(top) > 2 else top[0]
        n, kb = areas.get(key, (0, 0)); areas[key] = (n + 1, kb + c['kb'])
    print(f"{'EXECUTING' if a.execute else 'DRY RUN'}: {len(plan)} directories, {scan.human(total)} to reclaim (categories: {', '.join(sorted(cats))})\n")
    print('| Area | Dirs | Size |\n|---|---:|---:|')
    for k, (n, kb) in sorted(areas.items(), key=lambda kv: -kv[1][1])[:15]:
        print(f'| {k} | {n} | {scan.human(kb)} |')
    print('\nSkipped, by reason:')
    for why, items in sorted(skipped.items(), key=lambda kv: -sum(i['kb'] for i in kv[1] if i)):
        print(f"- {len(items)} item(s), {scan.human(sum(i['kb'] for i in items))}: {why}")

    if not a.execute:
        print('\nNothing was deleted. Re-run with --execute to apply.')
        return
    free0 = int(scan.run(['df', '-k', target]).splitlines()[-1].split()[3])
    done = failed = 0
    with open(a.log, 'w') as log:
        log.write('path\tkb\tstatus\n')
        for c in plan:
            r = subprocess.run(['rm', '-rf', '--', c['path']], capture_output=True, text=True)
            ok = r.returncode == 0 and not os.path.exists(c['path'])
            done += ok; failed += (not ok)
            log.write(f"{c['path']}\t{c['kb']}\t{'deleted' if ok else 'FAILED'}\n")
    free1 = int(scan.run(['df', '-k', target]).splitlines()[-1].split()[3])
    print(f"\nDeleted {done} directories ({failed} failed). Free space on the volume: {scan.human(free0)} -> {scan.human(free1)} (+{scan.human(max(free1 - free0, 0))}). Log: {a.log}")


if __name__ == '__main__':
    main()
