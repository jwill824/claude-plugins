"""Adapters: each asks one package manager (or reads one well-known folder) what it owns. They only read.

An adapter returns a dict:
  name, status        'ok', 'missing' (the tool is not installed, so it owns nothing) or 'failed' (we could not find out)
  detail              why, for 'failed' / 'missing'
  packages            what it says is installed: {owner, kind, name, version, ...}
  roots               folders whose contents it owns: {prefix, owner}
  claims              single files it owns, by absolute path: {path: (owner, evidence)}
  signals             its own verdicts about leftovers: {kind, name, path, evidence}
  extra               adapter-specific data other adapters need

A 'failed' adapter matters: nothing it might have owned may be reported as unmanaged or orphaned.
"""
import filecmp, json, os, plistlib, re
from common import run


def result(name, status, detail='', packages=None, roots=None, claims=None, signals=None, extra=None):
    return {'name': name, 'status': status, 'detail': detail, 'packages': packages or [], 'roots': roots or [],
            'claims': claims or {}, 'signals': signals or [], 'extra': extra or {}}


def brew(ctx, prior):
    st, out = run(['brew', '--prefix'])
    if st == 'missing':
        return result('brew', 'missing', 'brew is not installed')
    if st != 'ok':
        return result('brew', 'failed', out)
    prefix = os.path.realpath(out.strip())
    st, out = run(['brew', 'info', '--json=v2', '--installed'], timeout=180)
    if st != 'ok':
        return result('brew', 'failed', out)
    try:
        data = json.loads(out)
    except ValueError:
        return result('brew', 'failed', 'unreadable JSON from `brew info`')
    packages, cask_apps = [], {}
    for f in data.get('formulae') or []:
        inst = f.get('installed') or []
        packages.append({'owner': 'brew', 'kind': 'formula', 'name': f.get('name', ''), 'version': (inst[-1].get('version', '') if inst else ''),
                         'requested': any(i.get('installed_on_request') for i in inst)})
    for c in data.get('casks') or []:
        apps = [a for art in (c.get('artifacts') or []) if isinstance(art, dict) for a in (art.get('app') or []) if isinstance(a, str)]
        token = c.get('token') or c.get('full_token') or ''
        packages.append({'owner': 'brew', 'kind': 'cask', 'name': token, 'version': str(c.get('installed') or ''), 'apps': apps})
        for a in apps:
            cask_apps[a] = token
    roots = [{'prefix': os.path.join(prefix, d), 'owner': 'brew'} for d in ('Cellar', 'Caskroom', 'opt')]
    signals, warnings = [], []
    st, out = run(['brew', 'autoremove', '-n'])
    if st == 'ok':
        grab = False
        for line in out.splitlines():
            if line.startswith('==>'):
                grab = 'would autoremove' in line.lower()
            elif grab and re.match(r'^[\w@.+/-]+$', line.strip()):
                signals.append({'kind': 'brew-orphan', 'name': line.strip(), 'path': '',
                                'evidence': '`brew autoremove -n` lists it: no installed formula depends on it'})
    elif st == 'failed':
        warnings.append(f'brew autoremove -n: {out}')
    claims = {os.path.join(prefix, 'bin', 'brew'): ('brew', 'Homebrew itself')}
    return result('brew', 'ok', '; '.join(warnings), packages, roots, claims, signals, extra={'prefix': prefix, 'cask_apps': cask_apps})


def mise(ctx, prior):
    st, out = run(['mise', 'ls', '--json'])
    if st == 'missing':
        return result('mise', 'missing', 'mise is not installed')
    if st != 'ok':
        return result('mise', 'failed', out)
    try:
        data = json.loads(out)
    except ValueError:
        return result('mise', 'failed', 'unreadable JSON from `mise ls`')
    packages, installs = [], {}
    for tool, versions in (data or {}).items():
        for v in versions or []:
            if v.get('installed') is False:
                continue
            path = v.get('install_path') or ''
            packages.append({'owner': 'mise', 'kind': 'tool', 'name': tool, 'version': v.get('version', ''), 'path': path})
            installs[(tool, v.get('version', ''))] = path
    base = ''
    for p in packages:
        if p['path']:
            base = os.path.dirname(os.path.dirname(p['path']))
            break
    base = base or os.path.join(ctx.home, '.local', 'share', 'mise', 'installs')
    roots = [{'prefix': base, 'owner': 'mise'}]
    shims = os.path.join(os.path.dirname(base), 'shims')
    signals, warnings = [], []
    st, listed = run(['mise', 'ls', '--prunable'])
    prunable = []
    if st == 'ok':
        prunable = [l.split()[:2] for l in listed.splitlines() if len(l.split()) >= 2]
    elif st == 'failed':
        warnings.append(f'mise ls --prunable: {listed}')
    reasons = {}
    if prunable:
        st, txt = run(['mise', 'prune', '--dry-run'], with_stderr=True)
        if st == 'ok':
            for m in re.finditer(r'^mise (\S+?)@(\S+) is prunable: (.*)$', txt, re.M):
                reasons[(m.group(1), m.group(2))] = m.group(3).strip()
        elif st == 'failed':
            warnings.append(f'mise prune --dry-run: {txt}')
    for tool, ver in prunable:
        signals.append({'kind': 'mise-prunable', 'name': f'{tool}@{ver}', 'path': installs.get((tool, ver), ''),
                        'evidence': 'mise says it is prunable: ' + reasons.get((tool, ver), '`mise ls --prunable` lists it')})
    return result('mise', 'ok', '; '.join(warnings), packages, roots, signals=signals, extra={'shims': shims})


def npm(ctx, prior):
    """Global npm packages. Read from disk, once per node install, so nothing depends on which node is active."""
    prefixes = {}
    for p in prior.get('mise', {}).get('packages', []):
        if p['name'] == 'node' and p['path']:
            prefixes[os.path.realpath(p['path'])] = f"node {p['version']} (mise)"
    for d in ctx.path_dirs:
        if os.path.exists(os.path.join(d, 'npm')):
            pre = os.path.dirname(os.path.realpath(d))
            if os.path.isdir(os.path.join(pre, 'lib', 'node_modules')):
                prefixes.setdefault(pre, f'node at {pre}')
    if not prefixes:
        return result('npm', 'missing', 'no npm found')
    packages, roots, warnings = [], [], []
    for pre, label in sorted(prefixes.items()):
        mods = os.path.join(pre, 'lib', 'node_modules')
        if not os.path.isdir(mods):
            continue
        roots.append({'prefix': mods, 'owner': 'npm'})
        try:
            names = sorted(os.listdir(mods))
        except OSError as e:
            warnings.append(f'{mods}: {e}')
            continue
        for n in names:
            dirs = [(n, os.path.join(mods, n))] if not n.startswith('@') else \
                   [(f'{n}/{c}', os.path.join(mods, n, c)) for c in sorted(os.listdir(os.path.join(mods, n)))] if os.path.isdir(os.path.join(mods, n)) else []
            for name, d in dirs:
                ver = ''
                try:
                    ver = json.load(open(os.path.join(d, 'package.json'))).get('version', '')
                except (OSError, ValueError):
                    pass
                packages.append({'owner': 'npm', 'kind': 'package', 'name': name, 'version': ver, 'node': label, 'bundled': name in ('npm', 'corepack')})
    return result('npm', 'failed' if warnings and not packages else 'ok', '; '.join(warnings), packages, roots)


def uv(ctx, prior):
    st, out = run(['uv', 'tool', 'dir'])
    if st == 'missing':
        return result('uv', 'missing', 'uv is not installed')
    if st != 'ok':
        return result('uv', 'failed', out)
    tool_dir = out.strip()
    st, listing = run(['uv', 'tool', 'list'])
    if st != 'ok':
        return result('uv', 'failed', listing)
    packages, cur = [], None
    for line in listing.splitlines():
        m = re.match(r'^(\S+) v(\S+)', line)
        if m and not line.startswith('-'):
            cur = {'owner': 'uv', 'kind': 'tool', 'name': m.group(1), 'version': m.group(2), 'exes': []}
            packages.append(cur)
        elif line.startswith('- ') and cur is not None:
            cur['exes'].append(line[2:].strip())
    roots = [{'prefix': tool_dir, 'owner': 'uv'}]
    warnings = []
    st, pdir = run(['uv', 'python', 'dir'])
    if st == 'ok' and pdir.strip():
        roots.append({'prefix': pdir.strip(), 'owner': 'uv'})
    elif st == 'failed':
        warnings.append(f'uv python dir: {pdir}')
    return result('uv', 'ok', '; '.join(warnings), packages, roots)


def cargo(ctx, prior):
    """Crates from `cargo install --list`, plus the rustup proxies that fill ~/.cargo/bin."""
    cargo_bin = os.path.join(ctx.home, '.cargo', 'bin')
    st, out = run(['cargo', 'install', '--list'])
    if st == 'failed':
        return result('cargo', 'failed', out)
    packages, claims, crate = [], {}, None
    if st == 'ok':
        for line in out.splitlines():
            m = re.match(r'^(\S+) v(\S+?)( \(.*\))?:$', line)
            if m:
                crate = {'owner': 'cargo', 'kind': 'crate', 'name': m.group(1), 'version': m.group(2), 'bins': []}
                packages.append(crate)
            elif line.startswith('    ') and crate is not None:
                b = line.strip()
                crate['bins'].append(b)
                claims[os.path.join(cargo_bin, b)] = ('cargo', f'`cargo install --list`: {crate["name"]} installs {b}')
    rustup = os.path.join(cargo_bin, 'rustup')
    if os.path.isfile(rustup) and os.path.isdir(cargo_bin):
        size = os.path.getsize(rustup)
        for entry in sorted(os.listdir(cargo_bin)):
            p = os.path.join(cargo_bin, entry)
            if p in claims or not os.path.isfile(p):
                continue
            try:
                same = p == rustup or os.path.samefile(p, rustup) or (os.path.getsize(p) == size and filecmp.cmp(p, rustup, shallow=False))
            except OSError:
                same = False
            if same:
                claims[p] = ('rustup', 'identical to ~/.cargo/bin/rustup (a rustup proxy)')
    return result('cargo', 'ok' if st == 'ok' else 'missing', '' if st == 'ok' else 'cargo is not installed', packages, claims=claims)


def rustup(ctx, prior):
    root = os.path.join(ctx.home, '.rustup')
    st, out = run(['rustup', 'toolchain', 'list'])
    if st == 'missing':
        return result('rustup', 'missing', 'rustup is not installed')
    if st != 'ok':
        return result('rustup', 'failed', out)
    packages = [{'owner': 'rustup', 'kind': 'toolchain', 'name': l.split()[0], 'version': ''} for l in out.splitlines() if l.strip() and not l.startswith('no ')]
    return result('rustup', 'ok', '', packages, roots=[{'prefix': root, 'owner': 'rustup'}])


def mas(ctx, prior):
    st, out = run(['mas', 'list'])
    if st == 'missing':
        return result('mas', 'missing', 'mas is not installed (App Store apps are still found by their receipts)')
    if st != 'ok':
        return result('mas', 'failed', out)
    packages = []
    for line in out.splitlines():
        m = re.match(r'^\s*(\d*)\s+(.+?)\s+\(([^)]*)\)\s*$', line)
        if m:
            packages.append({'owner': 'mas', 'kind': 'app', 'name': m.group(2), 'version': m.group(3), 'id': m.group(1)})
    return result('mas', 'ok', '', packages)


def apps(ctx, prior):
    """Application bundles, and who put each there: a Homebrew cask, the App Store (receipt), Apple, or nobody we can see."""
    cask_apps = prior.get('brew', {}).get('extra', {}).get('cask_apps', {})
    brew_failed = prior.get('brew', {}).get('status') == 'failed'
    packages, roots, warnings = [], [], []
    bundles = []
    for d in ctx.app_dirs:
        try:
            for entry in sorted(os.listdir(d)):
                p = os.path.join(d, entry)
                if entry.endswith('.app') and os.path.isdir(p):
                    bundles.append((entry, p))
                elif os.path.isdir(p) and not entry.startswith('.'):
                    try:
                        bundles += [(c, os.path.join(p, c)) for c in sorted(os.listdir(p)) if c.endswith('.app')]
                    except OSError:
                        pass
        except OSError:
            continue
    for entry, p in bundles:
        bid, ver = '', ''
        try:
            with open(os.path.join(p, 'Contents', 'Info.plist'), 'rb') as f:
                info = plistlib.load(f)
            bid, ver = str(info.get('CFBundleIdentifier', '')), str(info.get('CFBundleShortVersionString', ''))
        except (OSError, ValueError, plistlib.InvalidFileException):
            warnings.append(f'{entry}: unreadable Info.plist')
        if entry in cask_apps:
            owner, how = 'brew', f'Homebrew cask {cask_apps[entry]}'
        elif os.path.isdir(os.path.join(p, 'Contents', '_MASReceipt')):
            owner, how = 'mas', 'has an App Store receipt'
        elif bid.startswith('com.apple.'):
            owner, how = 'apple', 'Apple bundle id'
        elif brew_failed:
            owner, how = 'unknown', 'Homebrew could not be read, so a cask cannot be ruled out'
        else:
            owner, how = 'manual', 'no cask, no App Store receipt: installed by hand'
        packages.append({'owner': owner, 'kind': 'app', 'name': entry[:-4], 'version': ver, 'path': p, 'bundle_id': bid, 'how': how})
        roots.append({'prefix': p, 'owner': 'app:' + owner})
    return result('apps', 'ok', '; '.join(warnings), packages, roots)


# Order matters: later adapters read what earlier ones found.
ORDER = (brew, mise, npm, uv, cargo, rustup, mas, apps)
# Managers whose folders we recognise but do not read yet. Anything that resolves into them is "unknown", never "unmanaged".
NOT_COVERED = {'pipx': ('/pipx/',), 'gem': ('/.gem/', '/.local/share/gem/'), 'go': ('/go/bin/',), 'bun': ('/.bun/',), 'deno': ('/.deno/',),
               'pyenv': ('/.pyenv/',), 'rbenv': ('/.rbenv/',), 'asdf': ('/.asdf/',), 'nvm': ('/.nvm/',), 'vfox': ('/.vfox/',),
               'sdkman': ('/.sdkman/',), 'pnpm': ('/pnpm/', '/.pnpm')}


def collect(ctx):
    results = {}
    for fn in ORDER:
        try:
            results[fn.__name__] = fn(ctx, results)
        except Exception as e:  # an adapter bug must show up as a failure, never as silence
            results[fn.__name__] = result(fn.__name__, 'failed', f'{type(e).__name__}: {e}')
    return results
