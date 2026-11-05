#!/usr/bin/env python3
"""Bring an unmanaged app or command under a package manager. Typed-only; a dry run first, then the same items again, re-checked.

    python3 adopt.py AUDIT.json --key KEY [--key KEY ...] --plan FILE     dry run: prints the table, writes a plan, installs nothing
    python3 adopt.py AUDIT.json --plan FILE --execute                     installs exactly what the plan lists, if nothing has changed
    python3 adopt.py AUDIT.json --plan FILE --trash-old KEY [--trash-old KEY ...]    moves the old copy of an adopted command to the Trash

This is the one tool in the plugin that installs software, so it is narrow and every gate fails closed:

  apps      `brew install --cask --adopt NAME` when the installed app is the cask's version (Homebrew then checks the bundle is
            identical), else the old app is moved to the Trash and `brew install --cask NAME` runs (put back automatically if
            the install fails). Only casks that merely copy one app, named exactly like the installed one, with no dependencies.
  commands  `mise use -g BACKEND@VERSION` for a tool whose first registry backend is aqua or core, at the version
            `mise latest` reports, after a backup of the global mise config.

It never uses sudo or --force, never runs the unmanaged copy, never touches the audit's read-only allow-list (`common.ALLOWED`),
and adopts only items the audit calls unmanaged that the user has not accepted. The mise MCP is advisory: `--mise-effects FILE`
(the saved output of its `list_commands`) can only add a hold, never grant anything.
"""
import argparse, hashlib, json, os, re, shlex, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accepted, adoptgates as G
from common import ENV, tilde
from fix import device_of, drifted, fingerprint, trash_dest

PLAN_MAX_AGE = 3600
INSTALL_TIMEOUT = 900
READ_TIMEOUT = 60
MANAGERS = {'app': ('brew-cask', 'brew'), 'binary': ('mise', 'mise')}     # kind -> (adopter, the audit's adapter name)

# ---- the exact commands this tool may run. Reads first, then the only two kinds of write.
READS = (('brew', 'info', '--json=v2', '--installed'), ('mise', 'registry'), ('mise', 'ls', '--json'), ('mise', 'config', 'ls'), ('ps', '-axo', 'command'))


def allowed_read(argv):
    argv = tuple(argv)
    return (argv in READS
            or (len(argv) == 5 and argv[:4] == ('brew', 'info', '--cask', '--json=v2') and bool(G.NAME_RE.match(argv[4])))
            or (len(argv) == 3 and argv[:2] == ('mise', 'latest') and bool(G.NAME_RE.match(argv[2]))))


def allowed_write(argv):
    argv = tuple(argv)
    if len(argv) == 5 and argv[:4] == ('brew', 'install', '--cask', '--adopt'):
        return bool(G.NAME_RE.match(argv[4]))
    if len(argv) == 4 and argv[:3] == ('brew', 'install', '--cask'):
        return bool(G.NAME_RE.match(argv[3]))
    if len(argv) == 4 and argv[:3] == ('mise', 'use', '-g') and '@' in argv[3]:
        spec, _, ver = argv[3].rpartition('@')
        return bool(G.BACKEND_RE.match(spec) or G.NAME_RE.match(spec)) and bool(G.VERSION_RE.match(ver))
    return False


def _exec(argv, timeout, env_extra=None):
    if shutil.which(argv[0]) is None:
        return 'missing', ''
    env = {**os.environ, **ENV, **(env_extra or {})}
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return 'failed', f'timed out after {timeout}s'
    except OSError as e:
        return 'failed', str(e.strerror or e)
    if p.returncode != 0:
        return 'failed', f'exit {p.returncode}: {(p.stderr or p.stdout).strip()[-200:]}'
    return 'ok', p.stdout


def read(argv):
    if not allowed_read(argv):
        raise ValueError(f'not an allowed read: {list(argv)}')
    return _exec(argv, READ_TIMEOUT)


def write(argv):
    if not allowed_write(argv):
        raise ValueError(f'not an allowed install command: {list(argv)}')
    return _exec(argv, INSTALL_TIMEOUT, {'HOMEBREW_NO_AUTO_UPDATE': '1', 'HOMEBREW_NO_INSTALL_CLEANUP': '1'})


# ---- facts, each read through the allow-list; None means "could not tell"
def json_of(argv):
    st, out = read(argv)
    if st != 'ok':
        return None, out
    try:
        return json.loads(out), None
    except ValueError:
        return None, 'unreadable output'


def registry():
    st, out = read(['mise', 'registry'])
    if st != 'ok':
        return None
    reg = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            reg[parts[0].lower()] = parts[1:]
    return reg


def ps_lines():
    st, out = read(['ps', '-axo', 'command'])
    return out.splitlines() if st == 'ok' else None


def adapter_status(rep, name):
    return next((a['status'] for a in rep['adapters'] if a['name'] == name), 'missing')


def mise_effect_gate(path):
    """Advisory input from the mise MCP's list_commands: it can only add a hold. None when fine or not given."""
    if not path:
        return None
    try:
        cmds = {c['command']: c.get('effect') for c in json.load(open(path))['commands']}
    except (OSError, ValueError, KeyError, TypeError):
        return 'the mise effects file could not be read, so `mise use` was not confirmed as a write'
    return None if cmds.get('use') == 'write' else f"mise declares `use` as {cmds.get('use') or 'unclassified'}, not write"


def global_mise_config(home):
    """The global config file `mise use -g` writes, confirmed against `mise config ls`; (path or None, why not)."""
    cand = os.environ.get('MISE_GLOBAL_CONFIG_FILE') or os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(home, '.config'), 'mise', 'config.toml')
    st, out = read(['mise', 'config', 'ls'])
    if st != 'ok':
        return None, 'could not ask mise which config files it uses'
    listed = {tok for line in out.splitlines() for tok in line.split()[:1]}
    if cand in listed or tilde(cand, home) in listed:
        return cand, None
    return None, f'mise does not list {tilde(cand, home)} as a config file, so it is not clear which file `mise use -g` would change'


# ---- one item: everything that decides whether and how it is adopted
def assess(item, rep, ctx):
    """({'mode', 'argv', 'name', 'version', 'source', ...} or None, why-held or None). Used by the dry run and again by execute."""
    why = None
    kind = item['kind']
    if kind not in MANAGERS:
        return None, f'a {kind} is not adopted; only apps and commands are'
    adopter, adapter = MANAGERS[kind]
    why = G.common_gate(item, ctx['records'], adapter_status(rep, adapter))
    if why:
        return None, why
    if kind == 'app':
        return assess_app(item, ctx)
    return assess_command(item, ctx)


def assess_app(item, ctx):
    sugg = next((s for s in item.get('suggestions', []) if s.get('manager') == 'brew' and str(s.get('command', '')).startswith('brew install --cask ')), None)
    if not sugg:
        return None, 'no Homebrew cask of this name was found by the audit'
    token = sugg['candidate']
    if not G.NAME_RE.match(token or ''):
        return None, 'the cask name is not a plain name'
    data, err = json_of(['brew', 'info', '--cask', '--json=v2', token])
    casks = (data or {}).get('casks') if isinstance(data, dict) else None
    if not casks:
        return None, f'could not read cask `{token}` from Homebrew ({err or "no data"})'
    cask = casks[0]
    why = G.cask_gate(cask, os.path.basename(item['path'].rstrip('/')))
    if why:
        return None, why
    installed, err = json_of(['brew', 'info', '--json=v2', '--installed'])
    if installed is None:
        return None, f'could not read what Homebrew has installed ({err})'
    if any(c.get('token') == token for c in installed.get('casks', [])):
        return None, f'Homebrew already has cask `{token}` installed'
    why = G.running_gate(item['path'], ps_lines())
    if why:
        return None, why
    mode = G.mode_for(item['path'], cask)
    if mode == 'replace':
        why = G.replace_gate(item['path'], ctx['home'], device_of(ctx['trash']), device_of)
        if why:
            return None, why
    argv = ['brew', 'install', '--cask'] + (['--adopt'] if mode == 'adopt' else []) + [token]
    short, _ = G.installed_versions(item['path'])
    return {'mode': mode, 'argv': argv, 'name': token, 'version': str(cask.get('version', '')), 'source': f'Homebrew cask `{token}`',
            'installed': short or 'unknown'}, None


def assess_command(item, ctx):
    sugg = next((s for s in item.get('suggestions', []) if s.get('manager') == 'mise'), None)
    if not sugg:
        return None, 'the audit found no mise registry entry for this command'
    name, backend = os.path.basename(item['path']).lower(), sugg['candidate']
    why = mise_effect_gate(ctx.get('mise_effects'))
    if why:
        return None, why
    reg = registry()
    if reg is None:
        return None, 'could not read `mise registry`'
    why = G.registry_gate(name, backend, reg)
    if why:
        return None, why
    ls, err = json_of(['mise', 'ls', '--json'])
    if ls is None:
        return None, f'could not read what mise has installed ({err})'
    if G.tool_claimed(ls, name):
        return None, f'mise already has `{name}`'
    cfg, err = global_mise_config(ctx['home'])
    if not cfg:
        return None, err
    st, out = read(['mise', 'latest', name])
    version = out.strip().splitlines()[-1].strip() if st == 'ok' and out.strip() else ''
    if not G.VERSION_RE.match(version):
        return None, f'`mise latest {name}` gave no plain version ({version[:40] or err_text(st, out)})'
    spec = f'{backend}@{version}' if backend.startswith('aqua:') else f'{name}@{version}'
    return {'mode': 'mise', 'argv': ['mise', 'use', '-g', spec], 'name': name, 'version': version, 'source': f'mise registry `{backend}`',
            'config': cfg}, None


def err_text(st, out):
    return st if st != 'ok' else 'empty answer'


# ---- plan files, as in fix.py
def plan_hash(plan):
    return hashlib.sha256(json.dumps({k: plan[k] for k in ('version', 'created', 'home', 'trash', 'items')}, sort_keys=True).encode()).hexdigest()


def log_path():
    return os.path.join(os.path.dirname(accepted.state_path()), 'adopt-log.jsonl')


def log_entries():
    try:
        return [json.loads(l) for l in open(log_path())]
    except (OSError, ValueError):
        return []


def write_log(entries):
    path = log_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a') as f:
        for e in entries:
            f.write(json.dumps(e, sort_keys=True) + '\n')


def load_plan(a, ctx):
    """(plan, None) or (None, why)."""
    try:
        plan = json.load(open(a.plan))
        assert plan['sha256'] == plan_hash(plan) and plan['version'] == 1
    except (OSError, ValueError, KeyError, AssertionError, TypeError):
        return None, 'The plan is missing, unreadable or has been changed since the dry run.'
    if time.time() - plan['created'] > PLAN_MAX_AGE:
        return None, f'The plan is older than {PLAN_MAX_AGE // 60} minutes, and things change.'
    if plan['home'] != ctx['home'] or plan['trash'] != ctx['trash']:
        return None, 'The plan was made for a different home folder or Trash folder.'
    return plan, None


LIMITS = ['**What to know before you confirm**', '',
          '- This downloads and installs software from the internet. The source and version are in the table.',
          '- An app that is adopted or replaced has **no one-line undo**: `brew uninstall --cask NAME` removes the app. A replaced app stays recoverable only from the Trash until it is emptied.',
          '- A replace updates the app to the cask\'s latest version. Your settings in `~/Library` stay.',
          '- A command is added to the global mise config (a backup is made first). To undo it: restore the backup and run `mise uninstall NAME@VERSION`.',
          '- macOS may ask this terminal for App Management permission; if it is refused, nothing is changed.']


def dry_run(a, items, rep, ctx, warn):
    ok, held, planned = [], [], []
    for key in a.key:
        i = items.get(key)
        if i is None:
            held.append((key, 'not in this audit'))
            continue
        plan_item, why = assess(i, rep, ctx)
        if why:
            held.append((key, why))
            continue
        try:
            fp = fingerprint(i)
        except OSError as e:
            held.append((key, f'could not be examined ({e.strerror}); it may be gone'))
            continue
        entry = dict(plan_item, key=key, kind=i['kind'], path=i['path'], fp=fp, old_in_home=i['path'].startswith(ctx['home'] + os.sep))
        ok.append((i, plan_item))
        planned.append(entry)
    L = ['# Dry run: nothing has been installed', '']
    if warn:
        L += [f'**Accepted state:** {warn}', '']
    if ok:
        L += [f'Would adopt {len(ok)} item(s):', '', '| Key | What | How | Command | Version | Source |', '|---|---|---|---|---|---|']
        for i, p in ok:
            how = {'adopt': 'adopt in place', 'replace': 'replace (old app to the Trash first)', 'mise': 'mise use -g'}[p['mode']]
            ver = p['version'] + (f" (installed: {p['installed']})" if p.get('installed') else '')
            L.append(f"| `{i['key']}` | `{tilde(i['path'], ctx['home'])}` | {how} | `{' '.join(shlex.quote(x) for x in p['argv'])}` | {ver} | {p['source']} |")
        L += [''] + LIMITS
    else:
        L.append('Nothing can be adopted.')
    if held:
        L += ['', '## Held', ''] + [f'- `{k}`: {w}' for k, w in held]
    code = 1
    if planned:
        plan = {'version': 1, 'created': time.time(), 'home': ctx['home'], 'trash': ctx['trash'], 'items': planned}
        plan['sha256'] = plan_hash(plan)
        path = a.plan or os.path.join(tempfile.mkdtemp(prefix='dotfiles-adopt-'), 'plan.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(plan, f, indent=1, sort_keys=True)
        L += ['', f'Plan written to `{path}` (valid for {PLAN_MAX_AGE // 60} minutes). To carry it out, after you have read the table above:', '',
              f'    python3 {shlex.quote(os.path.abspath(__file__))} {shlex.quote(a.audit)} --plan {shlex.quote(path)} --execute' + (f' --trash-dir {shlex.quote(a.trash_dir)}' if a.trash_dir else '')]
        code = 0
    print('\n'.join(L))
    return code


def verify(entry):
    """True if the manager now claims this very name (and for commands, a binary of that name exists)."""
    if entry['mode'] == 'mise':
        ls, _ = json_of(['mise', 'ls', '--json'])
        for v in G.tool_claimed(ls, entry['name']) or []:
            if v.get('installed') and v.get('version') == entry['version']:
                base = v.get('install_path') or ''
                return any(os.path.lexists(os.path.join(base, *p)) for p in ((entry['name'],), ('bin', entry['name'])))
        return False
    inst, _ = json_of(['brew', 'info', '--json=v2', '--installed'])
    return bool(inst) and any(c.get('token') == entry['name'] for c in inst.get('casks', []))


def backup_config(path, home):
    real = os.path.realpath(path)
    if not os.path.exists(real):
        return None
    dest_dir = os.path.dirname(accepted.state_path())
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    dest = os.path.join(dest_dir, f"mise-config.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(real, dest)
    os.chmod(dest, 0o600)
    return dest


def execute(a, items, rep, ctx):
    plan, why = load_plan(a, ctx)
    if why:
        print(f'# Refused\n\n{why} Nothing was installed. Run the dry run again.')
        return 1
    if any(e.get('plan') == plan['sha256'] for e in log_entries()):
        print('# Refused\n\nThis plan has already been carried out. A plan is used once. Nothing was installed. Run the dry run again to make a new plan.')
        return 1
    done, held, failed, log = [], [], [], []
    for entry in plan['items']:
        key = entry['key']
        i = items.get(key)
        if i is None or i['path'] != entry['path']:
            held.append((key, 'is not what the plan was made from'))
            continue
        fresh, why = assess(i, rep, ctx)
        if not why and (fresh['argv'] != entry['argv'] or fresh['mode'] != entry['mode']):
            why = 'what would be run has changed since the plan was made (the version or the way it is installed)'
        if not why:
            try:
                changed = drifted(entry['fp'], fingerprint(i))
            except OSError as e:
                changed = f'could not be examined ({e.strerror})'
            why = f'changed since the plan was made ({changed})' if changed else None
        if why:
            held.append((key, why))
            continue
        moved_to, backup = None, None
        if entry['mode'] == 'replace':
            moved_to = trash_dest(ctx['trash'], i['path'])
            try:
                os.makedirs(ctx['trash'], exist_ok=True)
                os.rename(i['path'], moved_to)
            except OSError as e:
                hint = ' (macOS may require App Management or Full Disk Access for this terminal)' if isinstance(e, PermissionError) else ''
                failed.append((key, f'could not move the old app aside: {e.strerror}{hint}; nothing was installed'))
                log.append(rec(plan, entry, 'FAILED', 'could not move the old app aside'))
                continue
        elif entry['mode'] == 'mise':
            try:
                backup = backup_config(entry['config'], ctx['home'])
            except OSError as e:
                failed.append((key, f'could not back up the mise config ({e.strerror}); nothing was installed'))
                log.append(rec(plan, entry, 'FAILED', 'config backup failed'))
                continue
        try:
            st, out = write(entry['argv'])
        except Exception as e:      # whatever goes wrong, a moved app must be put back
            st, out = 'failed', f'unexpected error: {type(e).__name__}'
        if st != 'ok':
            note = out
            if moved_to:
                try:
                    os.rename(moved_to, i['path'])
                    note += '; the old app was put back where it was'
                except OSError as e:
                    note += f"; COULD NOT put the old app back ({e.strerror}): run `mv -- {shlex.quote(moved_to)} {shlex.quote(i['path'])}`"
            failed.append((key, note))
            log.append(rec(plan, entry, 'FAILED', out))
            continue
        if verify(entry):
            done.append((entry, i, moved_to, backup))
            log.append(rec(plan, entry, 'adopted', ''))
        else:
            failed.append((key, 'installed, but the manager does not claim it as expected (not counted as adopted); check with a new audit'))
            log.append(rec(plan, entry, 'installed-not-detected', ''))
    if log:
        write_log(log)
    L = [f'# Adopted {len(done)} item(s)', '']
    if done:
        L += ['| What | How | Version | Source |', '|---|---|---|---|']
        L += [f"| `{tilde(i['path'], ctx['home'])}` | {e['mode']} | {e['version']} | {e['source']} |" for e, i, _, _ in done]
        L += ['', 'To undo (nothing here has been run for you):', '']
        for e, i, moved_to, backup in done:
            if e['mode'] == 'mise':
                L.append(f"- `{e['name']}`: restore the config" + (f" from `{tilde(backup, ctx['home'])}`" if backup else '') + f" and run `mise uninstall {e['name']}@{e['version']}`")
                if e['old_in_home']:
                    L.append(f"  - the old copy at `{tilde(i['path'], ctx['home'])}` can now be moved to the Trash with `--trash-old {i['key']}`")
                else:
                    L.append(f"  - the old copy at `{i['path']}` is outside your home folder, so removing it needs admin rights: `sudo mv -- {shlex.quote(i['path'])} \"$HOME/.Trash/\"` (until then it may shadow the new one on PATH)")
            else:
                L.append(f"- `{e['name']}`: `brew uninstall --cask {e['name']}` removes the app" + (f"; the old app is in the Trash at `{tilde(moved_to, ctx['home'])}`" if moved_to else ''))
    for title, rows in (('Held (nothing was installed)', held), ('FAILED', failed)):
        if rows:
            L += ['', f'## {title}', ''] + [f'- `{k}`: {w}' for k, w in rows]
    L += ['', f'Log: `{tilde(log_path(), ctx["home"])}`' if log else 'Nothing was logged: nothing was attempted.', '', 'Run a new audit to see the result.']
    print('\n'.join(L))
    return 0 if done and not failed else 1


def rec(plan, entry, status, note):
    return {'time': time.time(), 'key': entry['key'], 'mode': entry['mode'], 'argv': entry['argv'], 'version': entry['version'], 'status': status, 'note': note[:200], 'plan': plan['sha256']}


def file_in_use(path):
    if shutil.which('lsof') is None:
        return 'could not tell whether it is in use (lsof is not available)'
    try:
        p = subprocess.run(['lsof', '-t', '--', path], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return 'could not tell whether it is in use'
    if p.stdout.split():
        return 'it is in use by a running process'
    if p.returncode not in (0, 1) or (p.returncode == 1 and p.stderr.strip()):
        return f'could not tell whether it is in use (lsof exit {p.returncode})'
    return None


def trash_old(a, items, rep, ctx):
    plan, why = load_plan(a, ctx)
    if why:
        print(f'# Refused\n\n{why} Nothing was moved.')
        return 1
    adopted = {e['key'] for e in log_entries() if e.get('plan') == plan['sha256'] and e.get('status') == 'adopted'}
    by_key = {e['key']: e for e in plan['items']}
    moved, held, printed, log = [], [], [], []
    for key in a.trash_old:
        entry = by_key.get(key)
        if entry is None or entry['mode'] != 'mise':
            held.append((key, 'is not a command adopted by this plan'))
            continue
        if key not in adopted:
            held.append((key, 'was not adopted by this plan (only a verified adoption allows removing the old copy)'))
            continue
        if not verify(entry):
            held.append((key, 'mise no longer claims the new copy, so the old one is kept'))
            continue
        p = entry['path']
        if not os.path.lexists(p):
            held.append((key, 'already gone'))
            continue
        if not entry['old_in_home']:
            printed.append((key, p))
            continue
        base = os.path.realpath(os.path.dirname(p))
        if not base.startswith(ctx['home'] + os.sep) or os.path.isdir(p) and not os.path.islink(p):
            held.append((key, 'is not a file inside your home folder'))
            continue
        busy = file_in_use(p)
        if busy:
            held.append((key, busy))
            continue
        if device_of(p) != device_of(ctx['trash']):
            held.append((key, 'is on another volume than the Trash'))
            continue
        dest = trash_dest(ctx['trash'], p)
        try:
            os.makedirs(ctx['trash'], exist_ok=True)
            os.rename(p, dest)
        except OSError as e:
            held.append((key, f'could not be moved ({e.strerror}); nothing was deleted'))
            continue
        moved.append((p, dest))
        log.append({'time': time.time(), 'key': key, 'status': 'old-copy-moved', 'src': p, 'dest': dest, 'plan': plan['sha256']})
    if log:
        write_log(log)
    L = [f'# Moved {len(moved)} old copy/copies to the Trash', '']
    if moved:
        L += ['To put one back:', '', '```sh'] + [f'mv -- {shlex.quote(d)} {shlex.quote(p)}' for p, d in moved] + ['```']
    if printed:
        L += ['', '## Outside your home folder (not run: needs admin rights)', ''] + [f'- `sudo mv -- {shlex.quote(p)} "$HOME/.Trash/"`' for _, p in printed]
    if held:
        L += ['', '## Held (left where it is)', ''] + [f'- `{k}`: {w}' for k, w in held]
    print('\n'.join(L))
    return 0 if moved or printed else 1


def main():
    ap = argparse.ArgumentParser(description='Adopt unmanaged apps and commands under a manager (dry run unless --execute).')
    ap.add_argument('audit')
    ap.add_argument('--key', action='append', default=[], help='an item key from the audit (dry run; repeatable)')
    ap.add_argument('--plan', metavar='FILE', help='dry run: where to write the plan. With --execute or --trash-old: the plan to use')
    ap.add_argument('--execute', action='store_true', help='carry out a plan from a dry run (nothing is installed without it)')
    ap.add_argument('--trash-old', action='append', default=[], metavar='KEY', help='after an adoption: move the old copy of that command to the Trash')
    ap.add_argument('--trash-dir', metavar='DIR', help='the Trash folder (default on macOS: ~/.Trash; required elsewhere)')
    ap.add_argument('--state', metavar='FILE', help='accepted-state file (default: $DOTFILES_STATE, else the XDG state folder)')
    ap.add_argument('--mise-effects', metavar='FILE', help="saved output of the mise MCP's list_commands; it can only add a hold")
    a = ap.parse_args()
    if a.trash_dir:
        trash = os.path.abspath(a.trash_dir)
    elif sys.platform == 'darwin':
        trash = os.path.join(os.path.expanduser('~'), '.Trash')
    else:
        sys.exit('this tool uses the macOS Trash. On this system pass --trash-dir DIR.')
    modes = [bool(a.execute), bool(a.trash_old), bool(a.key)]
    if sum(modes) != 1:
        sys.exit('use exactly one of: --key (dry run), --execute, --trash-old')
    if (a.execute or a.trash_old) and not a.plan:
        sys.exit('--execute and --trash-old need the plan from a dry run (--plan FILE); nothing changes without one')
    rep = json.load(open(a.audit))
    home = rep['home']
    items = {i['key']: i for i in rep['items']}
    records, warn = accepted.load(a.state or accepted.state_path())
    ctx = {'home': home, 'trash': trash, 'records': records, 'mise_effects': a.mise_effects}
    if a.execute:
        sys.exit(execute(a, items, rep, ctx))
    if a.trash_old:
        sys.exit(trash_old(a, items, rep, ctx))
    sys.exit(dry_run(a, items, rep, ctx, warn))


if __name__ == '__main__':
    main()
