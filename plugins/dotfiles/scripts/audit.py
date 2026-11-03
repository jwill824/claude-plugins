#!/usr/bin/env python3
"""Read-only audit of what is installed and who owns it.

For every command on PATH, every application and every dotfile it asks one question: which package manager owns this?
Whatever nothing claims is reported, with the evidence for the claim. It never installs, removes or modifies anything.

Classes:
  managed       a package manager claims it (brew, mise, npm, uv, cargo, rustup, a cask, the App Store)
  owned         a dot-folder that belongs to an installed tool
  unmanaged     installed, but no manager claims it (a downloaded binary, an app dragged into /Applications). Neutral:
                it says how it got here, not that it should go.
  orphaned      the owner is gone and the leftovers remain: a manager's own orphan verdict, a broken link, or a tool
                folder whose tool is installed nowhere and that no config file mentions. A candidate, never a verdict.
  referenced    not installed, but a config file mentions it, so something may still use it
  unknown       we could not rule out an owner (an adapter failed, or it lives in a manager this version does not read)
  unattributed  a dot-item the knowledge table has no entry for
  config        personal config, state, secrets and system files. Listed, never candidates.
  system        part of the operating system

    python3 audit.py [--json] [--save FILE] [--html FILE] [--all] [--home DIR] [--apps-dir DIR ...] [--max-seconds N]
"""
import argparse, datetime, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters, knowledge
from common import human, sizes, tilde
from refs import Refs

SYSTEM_PREFIXES = ('/usr/bin', '/bin', '/usr/sbin', '/sbin', '/usr/libexec', '/System', '/Library/Apple', '/var/run/com.apple',
                   '/Library/Developer/CommandLineTools')
EPHEMERAL = ('/var/folders/', '/private/var/folders/', '/tmp/', '/private/tmp/')
SECRET_SENTINEL_NOTE = 'file contents are never read or printed'


class Ctx:
    def __init__(self, home, path_dirs, app_dirs):
        self.home, self.path_dirs, self.app_dirs = home, path_dirs, app_dirs


def under(path, prefix):
    return path == prefix or path.startswith(prefix.rstrip(os.sep) + os.sep)


def owner_class(owner):
    if owner in ('app:manual',):
        return 'unmanaged'
    if owner in ('app:apple', 'apple', 'system'):
        return 'system'
    if owner in ('app:unknown', 'unknown'):
        return 'unknown'
    return 'managed'


def item(kind, name, path, owner, cls, evidence, flags=(), kb=None, refs=(), tier=None, suggestions=()):
    return {'key': f'{kind}:{path or name}', 'kind': kind, 'name': name, 'path': path, 'owner': owner, 'cls': cls,
            'evidence': list(evidence), 'flags': list(flags), 'kb': kb, 'referenced_by': list(refs),
            'tier': tier, 'suggestions': list(suggestions)}


def uncertain_why(results, refs):
    why = [f"adapter {n} failed ({r['detail']})" for n, r in results.items() if r['status'] == 'failed']
    why += [f'could not read {u}' for u in refs.unreadable]
    return why


def chain(path, limit=8):
    """The path, then each place a symlink sends it, then the final real path."""
    hops, cur = [], path
    for _ in range(limit):
        hops.append(cur)
        if not os.path.islink(cur):
            break
        t = os.readlink(cur)
        cur = t if os.path.isabs(t) else os.path.normpath(os.path.join(os.path.dirname(cur), t))
    real = os.path.realpath(path)
    return hops + ([real] if real not in hops else [])


def scan_bins(ctx, results, refs, uncertain):
    """Walk every bin folder (on PATH or not) and decide who owns each command."""
    roots = sorted((r for res in results.values() for r in res['roots']), key=lambda r: -len(r['prefix']))
    claims = {p: c for res in results.values() for p, c in res['claims'].items()}
    shims = results.get('mise', {}).get('extra', {}).get('shims')
    brew_prefix = results.get('brew', {}).get('extra', {}).get('prefix')
    home = ctx.home
    extras = [os.path.join(home, p) for p in ('.local/bin', '.cargo/bin', 'bin', 'go/bin', '.bun/bin', '.deno/bin')] + ['/usr/local/bin']
    if brew_prefix:
        extras += [os.path.join(brew_prefix, 'bin'), os.path.join(brew_prefix, 'sbin')]
    seen, dirs = set(), []
    for d in list(ctx.path_dirs) + extras:
        r = os.path.realpath(d)
        if os.path.isdir(d) and r not in seen:
            seen.add(r)
            dirs.append((d, r))
    home_caches = [os.path.join(home, '.npm', '_npx'), os.path.join(home, '.cache'), os.path.join(home, 'Library', 'Caches')]
    tmp_roots = ['/tmp', '/private/tmp', '/var/folders', '/private/var/folders']

    def in_cache(real):
        return any(under(real, c) for c in home_caches) or (not under(real, home) and any(under(real, t) for t in tmp_roots))

    on_path = {os.path.realpath(d) for d in ctx.path_dirs}
    items, system_names, ephemeral = [], set(), []
    for d, rd in dirs:
        if not under(rd, home) and any((d + '/').startswith(e) or (rd + '/').startswith(e) for e in EPHEMERAL):
            ephemeral.append(d)
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        if any(under(rd, s) for s in SYSTEM_PREFIXES):
            system_names.update(names)
            continue
        for n in names:
            p = os.path.join(d, n)
            link = os.path.islink(p)
            if os.path.isdir(p):
                continue
            if link and not os.path.exists(p):
                try:
                    target = os.readlink(p)
                except OSError:
                    target = '(unreadable)'
                cls, ev = ('unknown', [f'symlink target missing: {tilde(target, home)}; but ' + '; '.join(uncertain)]) if uncertain else \
                          ('orphaned', [f'symlink target missing: {tilde(target, home)}'])
                items.append(item('broken-link', n, p, 'none', cls, ev, ['on-path'] if rd in on_path else []))
                continue
            if not os.access(p, os.X_OK):
                continue
            real = os.path.realpath(p)
            flags = (['on-path'] if rd in on_path else []) + (['fragile'] if in_cache(real) else [])
            try:
                hops = chain(p)
            except OSError as e:
                items.append(item('binary', n, p, 'unknown', 'unknown', [f'could not read the link ({e.strerror}); ownership cannot be ruled out'], flags))
                continue
            owner, ev = None, []
            if shims and os.path.dirname(p) == shims:
                owner, ev = 'mise', ['mise shim']
            elif p in claims:
                owner, ev = claims[p][0], [claims[p][1]]
            else:
                for hop in hops:
                    r = next((r for r in roots if under(hop, r['prefix'])), None)
                    if r:
                        owner, ev = r['owner'], [f"link passes through {tilde(hop, home)}, inside {r['owner']}'s folder {tilde(r['prefix'], home)}"]
                        break
            if owner is None:
                for label, subs in adapters.NOT_COVERED.items():
                    if any(s in real + '/' for s in subs):
                        owner, ev = f'unknown:{label}', [f'resolves into {label}\'s folder ({tilde(real, home)}), which this version does not read']
                        break
            if 'fragile' in flags:
                ev = ev + [f'points into a cache ({tilde(real, home)}); tools that clean caches will remove it']
            if owner is None:
                cls, owner = ('unknown', 'unknown') if uncertain else ('unmanaged', 'none')
                ev = ev + (['no manager claims it' + ('' if not uncertain else '; but ' + '; '.join(uncertain))])
                try:
                    kb = os.path.getsize(real) // 1024
                except OSError:
                    kb = None
                items.append(item('binary', n, p, owner, cls, ev, flags, kb, refs.find_name(n)))
                continue
            cls = 'unknown' if owner.startswith('unknown') else owner_class(owner)
            items.append(item('binary', n, p, owner, cls, ev, flags))
    return items, system_names, ephemeral


def build_index(results, bins, system_names):
    """identifier (lower case) -> where it is installed from, so a dot-folder can be matched to its tool."""
    idx = {}

    def add(name, source):
        if name:
            idx.setdefault(name.lower(), []).append(source)
    for n in system_names:
        add(n, 'the operating system')
    for b in bins:
        if b['kind'] == 'binary' and b['cls'] != 'orphaned':
            add(b['name'], f"{b['owner']}" if b['cls'] == 'managed' else 'an unmanaged binary')
    for res in results.values():
        for p in res['packages']:
            who = f"{p['owner']} {p['kind']}"
            names = [p['name']] + ([p['name'].split('/')[-1]] if '/' in p['name'] else []) + list(p.get('exes') or []) + list(p.get('bins') or [])
            if p['kind'] == 'cask':
                names += [a[:-4] for a in p.get('apps', []) if a.endswith('.app')]
            if p['kind'] == 'app':
                names += [p.get('bundle_id', '')]
            for n in names:
                add(n, who)
    return idx


def newest_write(path, stop_after, limit=60000):
    """(newest modification time seen, truncated). Stops as soon as something newer than stop_after is found."""
    try:
        newest = os.lstat(path).st_mtime
    except OSError:
        return None, False
    if newest >= stop_after or not os.path.isdir(path):
        return newest, False
    seen = 0
    for dp, dns, fns in os.walk(path):
        for n in dns + fns:
            seen += 1
            try:
                newest = max(newest, os.lstat(os.path.join(dp, n)).st_mtime)
            except OSError:
                continue
            if newest >= stop_after:
                return newest, False
            if seen >= limit:
                return newest, True
    return newest, False


def scan_dots(ctx, results, refs, idx, uncertain, deadline, want_sizes, active_days):
    home = ctx.home
    rels = sorted(n for n in os.listdir(home) if n.startswith('.'))
    for sub in ('.config', '.local/share', '.local/state', '.cache'):
        d = os.path.join(home, sub)
        if os.path.isdir(d):
            try:
                rels += [f'{sub}/{n}' for n in sorted(os.listdir(d))]
            except OSError:
                pass
    paths = {rel: os.path.join(home, rel) for rel in rels}
    entries = {rel: knowledge.lookup(rel) for rel in rels}
    szs = {}
    if want_sizes:
        todo = [p for rel, p in paths.items() if os.path.isdir(p) and not os.path.islink(p) and not (entries[rel] and entries[rel].kind == 'system')]
        szs = sizes(todo, deadline)
    items = []
    for rel in rels:
        p, e = paths[rel], entries[rel]
        kb = szs.get(p)
        if kb is None and os.path.isfile(p):
            try:
                kb = max(1, os.path.getsize(p) // 1024)
            except OSError:
                pass
        if e is None:
            items.append(item('dotitem', rel, p, 'none', 'unattributed', ['not in the knowledge table: attribution is never guessed'], kb=kb, refs=refs.find_path(rel)))
        elif e.keep:
            items.append(item('dotitem', rel, p, e.tool, 'system' if e.kind == 'system' else 'config',
                              [f'{e.tool}: {e.kind}; listed, never a cleanup candidate'], kb=kb))
        elif not e.needles:
            items.append(item('dotitem', rel, p, e.tool, 'unmanaged', [f'{e.tool} installs itself; there is nothing to check it against'], kb=kb, refs=refs.find_path(rel)))
        else:
            hits = [(n, idx[n][0]) for n in e.needles if n in idx]
            if hits:
                n, src = hits[0]
                items.append(item('dotitem', rel, p, e.tool, 'owned', [f'{e.tool} is installed: `{n}` from {src}'], kb=kb))
                continue
            found = refs.find_path(rel)
            need = ', '.join(f'`{n}`' for n in e.needles)
            if found:
                items.append(item('dotitem', rel, p, e.tool, 'referenced',
                                  [f'{e.tool} is not installed (looked for {need}), but config files mention this folder'], kb=kb, refs=found))
                continue
            cut = time.time() - active_days * 86400
            newest, truncated = newest_write(p, cut)
            age = None if newest is None else (time.time() - newest) / 86400
            if newest is not None and newest >= cut:
                items.append(item('dotitem', rel, p, e.tool, 'referenced',
                                  [f'{e.tool} is not installed (looked for {need}), but something wrote to this folder within the last {active_days} days: a wrapper or an IDE may still use it'], kb=kb))
            elif uncertain:
                items.append(item('dotitem', rel, p, e.tool, 'unknown', [f'{e.tool} looks absent, but ' + '; '.join(uncertain)], kb=kb))
            elif truncated:
                items.append(item('dotitem', rel, p, e.tool, 'unknown', ['too large to check for recent writes within the limit'], kb=kb))
            else:
                items.append(item('dotitem', rel, p, e.tool, 'orphaned',
                                  [f'{e.tool}: none of {need} is installed through any adapter or found in a bin folder',
                                   'no config file mentions this folder',
                                   f'nothing inside was written in the last {active_days} days (newest: {age:.0f} days ago)'], kb=kb))
    return items


def scan_apps_and_signals(ctx, results, deadline, want_sizes):
    items = []
    apps = results['apps']['packages']
    szs = sizes([a['path'] for a in apps], deadline) if want_sizes else {}
    for a in apps:
        cls = {'brew': 'managed', 'mas': 'managed', 'apple': 'system', 'manual': 'unmanaged'}.get(a['owner'], 'unknown')
        owner = {'manual': 'none'}.get(a['owner'], a['owner'])
        items.append(item('app', a['name'], a['path'], owner, cls, [a['how']] + [s['evidence'] for s in a.get('suggestions', [])], kb=szs.get(a['path']),
                          tier=a.get('tier'), suggestions=a.get('suggestions', [])))
    for name, res in results.items():
        for s in res['signals']:
            items.append(item(s['kind'], s['name'], s['path'], name, 'orphaned', [s['evidence']]))
    return items


SIZED_KINDS = ('formula', 'tool', 'package', 'toolchain')


def audit(home, path_dirs, app_dirs, max_seconds=90, want_sizes=True, active_days=60, package_sizes=False):
    t0 = time.time()
    ctx = Ctx(home, path_dirs, app_dirs)
    results = adapters.collect(ctx)
    refs = Refs(home)
    uncertain = uncertain_why(results, refs)
    bins, system_names, ephemeral = scan_bins(ctx, results, refs, uncertain)
    idx = build_index(results, bins, system_names)
    deadline = t0 + max_seconds
    if package_sizes and want_sizes:
        todo = sorted({p['path'] for res in results.values() for p in res['packages'] if p['kind'] in SIZED_KINDS and p.get('path') and os.path.isdir(p['path'])})
        szs = sizes(todo, deadline)
        for res in results.values():
            for p in res['packages']:
                if p.get('path') in szs:
                    p['kb'] = szs[p['path']]
    items = bins + scan_apps_and_signals(ctx, results, deadline, want_sizes) + scan_dots(ctx, results, refs, idx, uncertain, deadline, want_sizes, active_days)
    inventory = {}
    for res in results.values():
        for p in res['packages']:
            inventory.setdefault(p['owner'], []).append({k: v for k, v in p.items() if k in ('kind', 'name', 'version', 'requested', 'bundled', 'node', 'bundle_id', 'how', 'path', 'kb')})
    return {
        'generated': datetime.datetime.now().isoformat(timespec='seconds'), 'home': home, 'seconds': round(time.time() - t0, 1),
        'adapters': [{'name': n, 'status': r['status'], 'detail': r['detail'], 'packages': len(r['packages'])} for n, r in results.items()],
        'not_covered': sorted(adapters.NOT_COVERED), 'uncertain': uncertain, 'ephemeral_path_dirs': ephemeral,
        'knowledge_entries': knowledge.TABLE_SIZE, 'items': items, 'inventory': inventory,
    }


# ---------------------------------------------------------------- report

def rows(items, home, cols):
    out = []
    for i in items:
        out.append('| ' + ' | '.join(c(i, home) for c in cols) + ' |')
    return out


def _name(i, home):
    return f"`{tilde(i['path'], home) if i['path'] else i['name']}`" if i['kind'] != 'mise-prunable' and i['path'] else f"`{i['name']}`"


def render(rep, show_all=False):
    home, items = rep['home'], rep['items']
    by = lambda cls, *kinds: [i for i in items if i['cls'] == cls and (not kinds or i['kind'] in kinds)]
    L = [f"# Dotfiles audit, {rep['generated'][:10]}", '']
    L += ['## Sources', '', '| Source | Status | Packages | Note |', '|---|---|---|---|']
    for a in rep['adapters']:
        L.append(f"| {a['name']} | {a['status']} | {a['packages']} | {a['detail'][:90]} |")
    L += ['', f"Not read by this version: {', '.join(rep['not_covered'])}. Anything that resolves into their folders is shown as **unknown**, never as unmanaged."]
    if rep['uncertain']:
        L += ['', '**Some sources could not be read, so every "unmanaged" and "orphaned" verdict below is shown as unknown:**'] + [f'- {u}' for u in rep['uncertain']]
    if rep['ephemeral_path_dirs']:
        L += ['', f"Skipped {len(rep['ephemeral_path_dirs'])} temporary folder(s) on PATH."]
    cnt = {}
    for i in items:
        cnt[(i['cls'], i['kind'])] = cnt.get((i['cls'], i['kind']), 0) + 1
    order = ['managed', 'owned', 'unmanaged', 'orphaned', 'referenced', 'unknown', 'unattributed', 'config', 'system']
    L += ['', '## Summary', '', '| Class | Count | Of |', '|---|---:|---|']
    for cls in order:
        parts = {k: v for (c, k), v in cnt.items() if c == cls}
        if parts:
            L.append(f"| {cls} | {sum(parts.values())} | {', '.join(f'{v} {k}' for k, v in sorted(parts.items()))} |")

    orph = sorted(by('orphaned'), key=lambda i: -(i['kb'] or 0))
    if orph:
        L += ['', f'## Orphan candidates ({len(orph)})', '',
              'Candidates, not verdicts. `/dotfiles:tidy` prints the commands for the ones you name.', '', '| Key | What | Size | Evidence |', '|---|---|---:|---|']
        for i in orph:
            L.append(f"| `{i['key']}` | {_name(i, home)} | {human(i['kb']) if i['kb'] else ''} | {'; '.join(i['evidence'])[:200]} |")
    ref = by('referenced')
    if ref:
        L += ['', f'## Referenced or recently used, but not installed ({len(ref)})', '',
              'Not installed anywhere, yet a config file mentions them or something wrote to them recently. They may still be in use.', '',
              '| What | Size | Why it is not an orphan |', '|---|---:|---|']
        for i in sorted(ref, key=lambda i: -(i['kb'] or 0)):
            why = ('mentioned by ' + ', '.join(i['referenced_by'])) if i['referenced_by'] else '; '.join(i['evidence'])[:170]
            L.append(f"| {_name(i, home)} | {human(i['kb']) if i['kb'] else ''} | {why} |")
    frag = [i for i in items if 'fragile' in i['flags']]
    if frag:
        L += ['', f'## Fragile links ({len(frag)})', '', 'Commands that resolve into a cache. A cache cleanup will break them.', '', '| Command | Owner | Evidence |', '|---|---|---|']
        for i in frag:
            L.append(f"| `{i['name']}` | {i['owner']} | {'; '.join(i['evidence'])[:160]} |")
    unm_b, unm_a, unm_d = by('unmanaged', 'binary'), by('unmanaged', 'app'), by('unmanaged', 'dotitem')
    if unm_b or unm_a or unm_d:
        L += ['', f'## Unmanaged ({len(unm_b) + len(unm_a) + len(unm_d)})', '', 'Installed, but no package manager claims them. Neutral: this says how they got here, not that they should go.']
    if unm_b:
        L += ['', '| Command | Size | Used by (config files) |', '|---|---:|---|']
        for i in unm_b:
            L.append(f"| `{tilde(i['path'], home)}` | {human(i['kb'])} | {', '.join(i['referenced_by']) or 'no config file mentions it'} |")
    if unm_a:
        L += ['', f"Applications installed by hand ({len(unm_a)}): " + ', '.join(f"{i['name']} ({human(i['kb'])})" + (f" [name match: cask `{i['suggestions'][0]['candidate']}`]" if i['suggestions'] else '')
                                                                  for i in sorted(unm_a, key=lambda i: -(i['kb'] or 0)))]
    if unm_d:
        L += ['', 'Folders of tools that install themselves: ' + ', '.join(f"`{i['name']}`" for i in unm_d)]
    unk = by('unknown')
    if unk:
        L += ['', f'## Unknown ({len(unk)})', '', '| What | Why |', '|---|---|']
        for i in unk[:40]:
            L.append(f"| {_name(i, home)} | {'; '.join(i['evidence'])[:200]} |")
        if len(unk) > 40:
            L.append(f'| (+{len(unk) - 40} more) | see --json |')
    una = sorted(by('unattributed'), key=lambda i: -(i['kb'] or 0))
    if una:
        L += ['', f"## Unattributed dot-items ({len(una)})", '', f"Not in the knowledge table ({rep['knowledge_entries']} entries). Largest 15:", '', '| Item | Size | Mentioned by |', '|---|---:|---|']
        for i in una[:15]:
            L.append(f"| `{i['name']}` | {human(i['kb']) if i['kb'] else ''} | {', '.join(i['referenced_by']) or ''} |")
    L += ['', '## Inventory by manager', '']
    for owner, pk in sorted(rep['inventory'].items()):
        kinds = {}
        for p in pk:
            kinds.setdefault(p['kind'], []).append(p)
        L.append(f"**{owner}**: " + ', '.join(f"{len(v)} {k}{'s' if len(v) != 1 else ''}" for k, v in sorted(kinds.items())))
        for k, v in sorted(kinds.items()):
            if k == 'formula':
                v = [p for p in v if p.get('requested')] if not show_all else v
                label = 'requested formulae' if not show_all else 'formulae'
            else:
                label = k + 's'
            names = ', '.join(sorted({p['name'] + (f" {p['version']}" if k in ('tool', 'toolchain') and p.get('version') else '') for p in v if not p.get('bundled')}))
            L.append(f"- {label}: {names}")
        L.append('')
    L += ['_References are searched in global config only (shell startup files, Claude settings and MCP configs under ~/.claude, LaunchAgents, cron), not in project folders._',
          f"_{SECRET_SENTINEL_NOTE}; {rep['seconds']}s_"]
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='Read-only audit of what is installed and who owns it.')
    ap.add_argument('--json', action='store_true', help='print the full result as JSON instead of the report')
    ap.add_argument('--save', metavar='FILE', help='also write the JSON here (for /dotfiles:tidy)')
    ap.add_argument('--html', metavar='FILE', help='also write a self-contained HTML map (sizes every package too, so it takes a little longer)')
    ap.add_argument('--all', action='store_true', help='list every Homebrew formula, not just the ones you asked for')
    ap.add_argument('--home', default=os.path.expanduser('~'))
    ap.add_argument('--apps-dir', action='append', help='application folder to scan (default /Applications and ~/Applications)')
    ap.add_argument('--max-seconds', type=int, default=90, help='time budget for sizing folders')
    ap.add_argument('--no-sizes', action='store_true')
    ap.add_argument('--active-days', type=int, default=60, help='a tool folder written to within this many days is never called an orphan')
    a = ap.parse_args()
    home = os.path.abspath(a.home)
    path_dirs, seen = [], set()
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if d and os.path.isdir(d) and os.path.realpath(d) not in seen:
            seen.add(os.path.realpath(d))
            path_dirs.append(d)
    app_dirs = a.apps_dir or ['/Applications', os.path.join(home, 'Applications')]
    rep = audit(home, path_dirs, app_dirs, a.max_seconds, not a.no_sizes, a.active_days, package_sizes=bool(a.html))
    if a.save:
        with open(a.save, 'w') as f:
            json.dump(rep, f, indent=1)
    if a.html:
        import htmlmap
        with open(a.html, 'w') as f:
            f.write(htmlmap.render(rep))
        print(f'wrote the map to {a.html}', file=sys.stderr)
    print(json.dumps(rep, indent=1) if a.json else render(rep, a.all))


if __name__ == '__main__':
    main()
