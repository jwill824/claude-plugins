#!/usr/bin/env python3
"""Move orphan folders and broken links to the Trash. Typed-only; a dry run first, then the same items again, re-checked.

    python3 fix.py AUDIT.json --key KEY [--key KEY ...]              dry run: prints the table, writes a plan, moves nothing
    python3 fix.py AUDIT.json --plan PLAN --execute                  moves exactly what the plan lists, if nothing has changed

The only thing this does to the disk is rename an item into the Trash folder (`~/.Trash/<name> <time>`), on the same volume,
so it can be put back with the `mv` it prints. It never deletes, copies, uses elevated rights or reads file contents. Only items
the audit calls orphaned, of kind dotitem or broken-link, inside the home folder, can move; manager removals stay printed-only in
`tidy.py`. Every gate fails closed: when it cannot tell, the item stays where it is.

The Trash folder is `~/.Trash` on macOS. On any other system `--trash-dir DIR` must name one (a folder on the same volume).
"""
import argparse, datetime, hashlib, json, os, re, shlex, shutil, stat, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accepted, gates
from audit import newest_write, under
from common import human, tilde
from refs import Refs

PLAN_MAX_AGE = 3600
WALK_LIMIT = 20000
KEY_NAMES = re.compile(r'^(id_(rsa|dsa|ecdsa|ed25519)(\..*)?|.*\.(pem|key|p12|pfx|ppk|kdbx)|\.env(\..*)?|credentials(\.[a-z]+)?|\.netrc|\.?tokens?(\.[a-z]+)?)$', re.I)
MOVABLE = ('dotitem', 'broken-link')


def trash_dest(trash, path):
    base, stamp = os.path.basename(path.rstrip('/')), time.strftime('%Y-%m-%d %H.%M.%S')
    dest, n = os.path.join(trash, f'{base} {stamp}'), 1
    while os.path.lexists(dest):
        n += 1
        dest = os.path.join(trash, f'{base} {stamp} {n}')
    return dest


def device_of(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    return os.stat(path or '/').st_dev


def fingerprint(i):
    p = i['path']
    st = os.lstat(p)
    link = stat.S_ISLNK(st.st_mode)
    newest = st.st_mtime
    if i['kind'] == 'dotitem' and not link:
        newest, _ = newest_write(p, float('inf'))
    return {'key': i['key'], 'path': p, 'name': i['name'], 'kind': i['kind'], 'kb': i.get('kb'), 'dev': st.st_dev, 'ino': st.st_ino, 'islink': link,
            'link': os.readlink(p) if link else None, 'real_parent': os.path.realpath(os.path.dirname(p)), 'newest': int(newest or 0), 'why': i['evidence'][:3]}


def drifted(planned, now):
    for k in ('dev', 'ino', 'islink', 'link', 'real_parent'):
        if planned[k] != now[k]:
            return k
    return 'a newer write inside it' if now['newest'] > planned['newest'] else None


def in_use(path):
    """None when nothing has it open, else why it is held. A tool that cannot answer is a hold, never a pass."""
    if shutil.which('lsof') is None:
        return 'could not tell whether it is in use (lsof is not available)'
    try:
        p = subprocess.run(['lsof', '-t', '+D', path], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 'could not tell whether it is in use (lsof timed out)'
    except OSError as e:
        return f'could not tell whether it is in use ({e.strerror})'
    pids = sorted({x for x in p.stdout.split() if x.isdigit()})
    if pids:
        return f"in use: process {', '.join(pids[:5])} has files open in it"
    if p.returncode not in (0, 1) or (p.returncode == 1 and p.stderr.strip()):
        return f'could not tell whether it is in use (lsof exit {p.returncode})'
    return None


def key_material(path):
    """Names only, bounded. A hit or an unfinished walk is a hold: whether something is a credential is the user's call."""
    seen = 0
    for dp, dns, fns in os.walk(path):
        for n in dns + fns:
            seen += 1
            if KEY_NAMES.match(n):
                return f'possible key material ({n}, by name only): move it by hand if you are sure'
            if seen >= WALK_LIMIT:
                return 'too large to look through for key material names'
    return None


def check_item(i, ctx):
    """None if this item may move now, else the reason it is held. Used by the dry run and again by the executor."""
    key, p, home = i['key'], i['path'], ctx['home']
    if accepted.has_control(key) or accepted.has_control(p or ''):
        return 'the name has control characters; move it by hand'
    why = gates.recheck(i, home, ctx['refs'], ctx['records'], ctx['active_days'])
    if why:
        return why
    if i['kind'] not in MOVABLE:
        return f"not movable here ({i['kind']}); manager removals are printed by /dotfiles:tidy"
    real_home = os.path.realpath(home)
    parent = os.path.dirname(p)
    if os.path.normpath(p) == os.path.normpath(home) or not under(os.path.realpath(parent), real_home):
        return 'lives outside your home folder (it may need administrator rights): use /dotfiles:tidy and run its command yourself'
    if under(os.path.realpath(p), os.path.realpath(ctx['trash'])) or os.path.ismount(p):
        return 'is the Trash or a mount point'
    if not os.access(parent, os.W_OK | os.X_OK):
        return 'its folder is not writable, so it cannot be moved'
    try:
        if os.lstat(p).st_dev != device_of(ctx['trash']):
            return 'is on a different volume than the Trash; moving it would mean copying, and this tool never copies'
    except OSError as e:
        return f'could not be examined ({e.strerror})'
    probe = ctx['trash'] if os.path.isdir(ctx['trash']) else os.path.dirname(ctx['trash'])
    if not os.access(probe, os.W_OK):
        return 'the Trash folder is not writable (on macOS the terminal may need Full Disk Access)'
    if i['kind'] == 'dotitem' and not os.path.islink(p):
        if os.path.isdir(p):
            for gate in (in_use, key_material):
                why = gate(p)
                if why:
                    return why
        elif KEY_NAMES.match(os.path.basename(p)):
            return f'possible key material ({os.path.basename(p)}, by name only): move it by hand if you are sure'
    return None


def plan_hash(plan):
    body = {k: plan[k] for k in ('version', 'created', 'home', 'trash', 'items')}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def log_path():
    return os.path.join(os.path.dirname(accepted.state_path()), 'fix-log.jsonl')


def carried_out(plan_id):
    try:
        return any(json.loads(l).get('plan') == plan_id for l in open(log_path()))
    except (OSError, ValueError):
        return False


def write_log(entries):
    path = log_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a') as f:
        for e in entries:
            f.write(json.dumps(e, sort_keys=True) + '\n')


def main():
    ap = argparse.ArgumentParser(description='Move orphan folders and broken links to the Trash (dry run unless --execute).')
    ap.add_argument('audit')
    ap.add_argument('--key', action='append', default=[], help='an item key from the audit (dry run; repeatable)')
    ap.add_argument('--plan', metavar='FILE', help='dry run: where to write the plan. With --execute: the plan to carry out')
    ap.add_argument('--execute', action='store_true', help='carry out a plan from a dry run (nothing moves without it)')
    ap.add_argument('--trash-dir', metavar='DIR', help='the Trash folder (default on macOS: ~/.Trash; required elsewhere)')
    ap.add_argument('--state', metavar='FILE', help='accepted-state file (default: $DOTFILES_STATE, else the XDG state folder)')
    ap.add_argument('--active-days', type=int, default=60)
    a = ap.parse_args()
    if a.trash_dir:
        trash = os.path.abspath(a.trash_dir)
    elif sys.platform == 'darwin':
        trash = os.path.join(os.path.expanduser('~'), '.Trash')
    else:
        sys.exit('this tool moves things to the macOS Trash. On this system pass --trash-dir DIR, or use /dotfiles:tidy and run its commands yourself.')
    if a.execute and a.key:
        sys.exit('--execute carries out a plan: pass --plan, not --key (what you reviewed is what moves)')
    if a.execute and not a.plan:
        sys.exit('--execute needs the plan from a dry run (--plan FILE); nothing moves without one')
    if not a.execute and not a.key:
        sys.exit('name at least one --key for the dry run')
    rep = json.load(open(a.audit))
    home = rep['home']
    items = {i['key']: i for i in rep['items']}
    records, warn = accepted.load(a.state or accepted.state_path())
    ctx = {'home': home, 'trash': trash, 'refs': Refs(home), 'records': records, 'active_days': a.active_days}
    if a.execute:
        sys.exit(execute(a, items, ctx))
    sys.exit(dry_run(a, items, ctx, warn))


def dry_run(a, items, ctx, warn):
    ok, held, planned = [], [], []
    for key in a.key:
        i = items.get(key)
        if i is None:
            held.append((key, 'not in this audit'))
            continue
        why = check_item(i, ctx)
        if why:
            held.append((key, why))
        else:
            ok.append(i)
            planned.append(fingerprint(i))
    L = ['# Dry run: nothing has been moved', '']
    if warn:
        L += [f'**Accepted state:** {warn}', '']
    if ok:
        L += [f'Would move {len(ok)} item(s) to the Trash (`{tilde(ctx["trash"], ctx["home"])}`), reversible with the `mv` printed afterwards:', '', '| Key | What | Size | Why it is an orphan candidate |', '|---|---|---:|---|']
        L += [f"| `{i['key']}` | `{tilde(i['path'], ctx['home'])}` | {human(i['kb']) if i.get('kb') else ''} | {'; '.join(i['evidence'])[:200]} |" for i in ok]
    else:
        L.append('Nothing can be moved.')
    if held:
        L += ['', '## Held', ''] + [f'- `{k}`: {w}' for k, w in held]
    code = 1
    if planned:
        plan = {'version': 1, 'created': time.time(), 'home': ctx['home'], 'trash': ctx['trash'], 'items': planned}
        plan['sha256'] = plan_hash(plan)
        path = a.plan or os.path.join(tempfile.mkdtemp(prefix='dotfiles-fix-'), 'plan.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(plan, f, indent=1, sort_keys=True)
        L += ['', f'Plan written to `{path}` (valid for {PLAN_MAX_AGE // 60} minutes). To carry it out, after you have read the table above:', '',
              f'    python3 {shlex.quote(os.path.abspath(__file__))} {shlex.quote(a.audit)} --plan {shlex.quote(path)} --execute' + (f' --trash-dir {shlex.quote(a.trash_dir)}' if a.trash_dir else '')]
        code = 0
    print('\n'.join(L))
    return code


def execute(a, items, ctx):
    try:
        plan = json.load(open(a.plan))
        assert plan['sha256'] == plan_hash(plan) and plan['version'] == 1
    except (OSError, ValueError, KeyError, AssertionError, TypeError):
        print('# Refused\n\nThe plan is missing, unreadable or has been changed since the dry run. Nothing was moved. Run the dry run again.')
        return 1
    if time.time() - plan['created'] > PLAN_MAX_AGE:
        print(f'# Refused\n\nThe plan is older than {PLAN_MAX_AGE // 60} minutes, and things change. Nothing was moved. Run the dry run again.')
        return 1
    if plan['home'] != ctx['home'] or plan['trash'] != ctx['trash']:
        print('# Refused\n\nThe plan was made for a different home folder or Trash folder. Nothing was moved. Run the dry run again.')
        return 1
    if carried_out(plan['sha256']):
        print('# Refused\n\nThis plan has already been carried out. A plan is used once: if you put something back, that was your choice. Nothing was moved. Run the dry run again to make a new plan.')
        return 1
    moved, held, failed, log = [], [], [], []
    for fp in plan['items']:
        key = fp['key']
        i = items.get(key)
        if i is None or i['path'] != fp['path']:
            held.append((key, 'is not what the plan was made from'))
            continue
        why = check_item(i, ctx)
        if not why:
            try:
                changed = drifted(fp, fingerprint(i))
            except OSError as e:
                changed = f'could not be examined ({e.strerror})'
            why = f'changed since the plan was made ({changed})' if changed else None
        if why:
            held.append((key, why))
            continue
        dest = trash_dest(ctx['trash'], i['path'])
        try:
            os.makedirs(ctx['trash'], exist_ok=True)
            os.rename(i['path'], dest)
            assert os.path.lexists(dest) and not os.path.lexists(i['path'])
        except (OSError, AssertionError) as e:
            hint = ' (macOS may require Full Disk Access for this terminal to write to ~/.Trash)' if isinstance(e, PermissionError) else ''
            failed.append((key, f'{getattr(e, "strerror", None) or e}{hint}; nothing was deleted'))
            log.append({'time': time.time(), 'key': key, 'src': i['path'], 'dest': dest, 'kb': i.get('kb'), 'status': 'FAILED', 'plan': plan['sha256']})
            continue
        moved.append((i, dest))
        log.append({'time': time.time(), 'key': key, 'src': i['path'], 'dest': dest, 'kb': i.get('kb'), 'status': 'moved', 'plan': plan['sha256']})
    if log:
        write_log(log)
    L = [f'# Moved {len(moved)} item(s) to the Trash', '']
    if moved:
        L += ['| What | Size | Now at |', '|---|---:|---|'] + [f"| `{tilde(i['path'], ctx['home'])}` | {human(i['kb']) if i.get('kb') else ''} | `{tilde(d, ctx['home'])}` |" for i, d in moved]
        L += ['', 'To put one back:', '', '```sh'] + [f"mv -- {shlex.quote(d)} {shlex.quote(i['path'])}" for i, d in moved] + ['```',
              '', 'Space returns only when the Trash is emptied. Nothing was deleted.']
    for title, rows in (('Held (left where it is)', held), ('FAILED (nothing was deleted)', failed)):
        if rows:
            L += ['', f'## {title}', ''] + [f'- `{k}`: {w}' for k, w in rows]
    L += ['', f'Log: `{tilde(log_path(), ctx["home"])}`' if log else 'Nothing was logged: nothing was attempted.']
    print('\n'.join(L))
    return 0 if moved and not failed else 1


if __name__ == '__main__':
    main()
