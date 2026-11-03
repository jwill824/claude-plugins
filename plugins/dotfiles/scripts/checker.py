"""The item checker: for what no manager owns, which manager could.

It prints suggestions. It never installs anything and never changes an item's class: a name match is a lead to verify, not
proof that two things are the same product. Ownership stays with the exact evidence in adapters.py (a cask's `app` artifact,
the packages it installs, a Mac App Store receipt).

Two `brew search` calls in total (casks for apps, formulae for commands) plus one `mise registry`, however many items there
are. Homebrew may refresh its own name index when it is stale (its decision, not ours; `--offline` forbids it), and the report
says whether it did. A search that failed is "could not check", never "no candidate".
"""
import os, re

import common

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
TYPED_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,60}$')
MAX_NAMES = 300


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def cask_variants(name):
    s = slug(name)
    return [v for v in (s, s + '-app') if NAME_RE.match(v)] if s else []


def command_name(name):
    n = name.lower()
    return n if NAME_RE.match(n) else None


def index_mtime(home):
    """Newest change time of Homebrew's own name index, or None when it cannot be found."""
    dirs = [os.path.join(os.environ['HOMEBREW_CACHE'], 'api')] if os.environ.get('HOMEBREW_CACHE') else []
    dirs += [os.path.join(home, 'Library', 'Caches', 'Homebrew', 'api'), os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.join(home, '.cache'), 'Homebrew', 'api')]
    times = []
    for d in dirs:
        for f in ('cask_names.txt', 'formula_names.txt'):
            try:
                times.append(os.stat(os.path.join(d, f)).st_mtime)
            except OSError:
                pass
    return max(times) if times else None


def search(flag, names):
    """(status, set of names Homebrew knows among those asked about)."""
    names = sorted(set(names))[:MAX_NAMES]
    if not names:
        return 'ok', set()
    st, out = common.run(['brew', 'search', flag, '/^(' + '|'.join(names) + ')$/'], timeout=180, ok_codes=(0, 1), no_match='No formulae or casks found')
    if st != 'ok':
        return st, set()
    return 'ok', {l.strip() for l in out.splitlines() if NAME_RE.match(l.strip())} & set(names)


def registry():
    """(status, {short name: [backends]}) from `mise registry`, parsed here."""
    st, out = common.run(['mise', 'registry'], timeout=60)
    if st != 'ok':
        return st, {}
    reg = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            reg[parts[0].lower()] = parts[1:]
    return 'ok', reg


def sug(manager, candidate, command, basis):
    return {'manager': manager, 'candidate': candidate, 'tier': 'candidate', 'command': command,
            'evidence': f'{basis}; a name match only, verify it is the same product first'}


def check(home, apps, commands, typed=(), installed_casks=(), installed_formulae=()):
    """apps / commands: {key: display name}. Returns (suggestions {key: [..]}, outcome {key: 'found'|'none'|'could-not-check'}, header)."""
    cask_for = {k: cask_variants(n) for k, n in {**apps, **{('typed:' + t): t for t in typed}}.items()}
    cmd_for = {k: command_name(n) for k, n in {**commands, **{('typed:' + t): t for t in typed}}.items()}
    want_casks = {v for vs in cask_for.values() for v in vs}
    want_cmds = {c for c in cmd_for.values() if c}
    header = {'ran': bool(want_casks or want_cmds), 'index': 'unknown', 'offline': common.OFFLINE, 'brew': 'not needed', 'mise': 'not needed', 'names': len(want_casks | want_cmds)}
    if not header['ran']:
        return {}, {}, header
    before = index_mtime(home)
    cst, casks = ('unavailable', set())
    fst, formulae = ('unavailable', set())
    if want_casks or want_cmds:
        cst, casks = search('--cask', want_casks)
        fst, formulae = search('--formula', want_cmds)
    after = index_mtime(home)
    header['brew'] = 'ok' if cst == fst == 'ok' else ('brew is not installed' if 'missing' in (cst, fst) else 'could not check')
    header['index'] = 'cached (offline)' if common.OFFLINE else 'unknown' if before is None or after is None else 'refreshed' if after > before else 'cached'
    mst, reg = registry() if want_cmds else ('ok', {})
    header['mise'] = 'ok' if mst == 'ok' else ('mise is not installed' if mst == 'missing' else 'could not check')
    out, outcome = {}, {}
    for key in set(cask_for) | set(cmd_for):
        found, tried_ok = [], True
        if cask_for.get(key):
            if cst != 'ok':
                tried_ok = False
            for v in cask_for[key]:
                if v in casks and v not in installed_casks:
                    found.append(sug('brew', v, f'brew install --cask {v}', 'a cask of this name exists in Homebrew'))
        c = cmd_for.get(key)
        if c:
            if fst != 'ok':
                tried_ok = False
            if c in formulae and c not in installed_formulae:
                found.append(sug('brew', c, f'brew install {c}', 'a formula of this name exists in Homebrew'))
            if mst != 'ok':
                tried_ok = False
            elif c in reg:
                found.append(sug('mise', reg[c][0], f'mise use -g {reg[c][0]}', f"`mise registry` lists {c} ({', '.join(reg[c][:2])})"))
        out[key] = found
        outcome[key] = 'found' if found else ('none' if tried_ok else 'could-not-check')
    return out, outcome, header
