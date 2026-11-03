#!/usr/bin/env python3
"""Tests for audit.py and tidy.py against a fake home folder and fake package managers.

The fake managers sit first on PATH and log every command they receive, so "the audit only ran read-only commands" is
checked from the outside, not just by the runner's own allow-list. A secret planted in files the audit must never print
is searched for in every output.

    python3 -B tests/test_audit.py
"""
import json, os, plistlib, shlex, shutil, stat, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
from common import ALLOWED, age_text, birth  # noqa: E402

ROOT = os.path.realpath(tempfile.mkdtemp(prefix='dotfiles-test-'))
HOME = os.path.join(ROOT, 'home')
FAKEBIN = os.path.join(ROOT, 'fakebin')
STATE = os.path.join(ROOT, 'state')
STATE_FILE = os.path.join(STATE, 'accepted.json')
LOG = os.path.join(ROOT, 'cmd.log')
SENTINEL = 'SENTINEL-SECRET-4f8a1c'
HOSTILE = '.<img src=x onerror=alert(1)>'
DAY = 86400
NOW = int(time.time())
BIRTH = 'birthtime' if hasattr(os.stat('/'), 'st_birthtime') else 'mtime'   # what this OS can tell us
FAILS = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {str(detail)[:300]}'))
    if not cond:
        FAILS.append(name)


def write(path, text='', mode=None, age_days=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)
    if age_days is not None:
        t = time.time() - age_days * DAY
        os.utime(path, (t, t))


def age_tree(path, days):
    t = time.time() - days * DAY
    for dp, dns, fns in os.walk(path, topdown=False):
        for n in fns + dns:
            os.utime(os.path.join(dp, n), (t, t))
    os.utime(path, (t, t))


def fake(name, body):
    write(os.path.join(FAKEBIN, name), '#!/bin/bash\necho "' + name + ' $*" >> "$DOTFILES_TEST_LOG"\n' + body, 0o755)


def build_world():
    os.makedirs(HOME)
    prefix = os.path.join(HOME, 'brewprefix')
    brew_info = {'formulae': [
        {'name': 'foo', 'installed': [{'version': '1.0', 'installed_on_request': True, 'time': NOW - 400 * DAY}]},
        {'name': 'cocoapods', 'installed': [{'version': '1.0', 'installed_on_request': True}]},
        {'name': 'oldlib', 'installed': [{'version': '0.1', 'installed_on_request': False}]},
        {'name': 'node', 'installed': [{'version': '26.0.0', 'installed_on_request': True}]}],
        'casks': [{'token': 'cask-app', 'installed': '2.0', 'installed_time': NOW - 45 * DAY, 'artifacts': [{'app': ['Cask.app']}]},
                  # a pkg cask: no app artifact, and the app's bundle id is in pkgutil, not quit (as with Microsoft Teams)
                  {'token': 'teams-like', 'installed': '1.0', 'artifacts': [{'pkg': ['x.pkg']}, {'uninstall': [{'quit': ['com.example.updater'], 'pkgutil': ['com.example.teams', 'com.example.*']}]}]},
                  # a cask that shares a name with an app but installs something else (a CLI)
                  {'token': 'lookalike', 'installed': '1.0', 'artifacts': [{'binary': ['lookalike']}]}]}
    write(os.path.join(ROOT, 'brew-info.json'), json.dumps(brew_info))
    write(os.path.join(ROOT, 'known-casks'), 'manual\nlookalike\ncask-app\n')
    write(os.path.join(ROOT, 'known-formulae'), 'manual-tool\nfoo\n')
    write(os.path.join(HOME, 'Library/Caches/Homebrew/api/cask_names.txt'), 'x', age_days=3)
    write(os.path.join(HOME, 'Library/Caches/Homebrew/api/formula_names.txt'), 'x', age_days=3)
    fake('brew', f'''echo "$1 ${{HOMEBREW_NO_AUTO_UPDATE:-unset}}" >> "{ROOT}/brew-env.log"
case "$1" in
  search) [ -n "$FAKE_SEARCH_FAIL" ] && {{ echo "Error: network down" >&2; exit 3; }}
          [ -z "$HOMEBREW_NO_AUTO_UPDATE" ] && [ -n "$FAKE_REFRESH" ] && touch "{HOME}/Library/Caches/Homebrew/api/cask_names.txt"
          known="{ROOT}/known-casks"; [ "$2" = "--formula" ] && known="{ROOT}/known-formulae"
          asked=$(echo "$3" | cut -c4- | rev | cut -c4- | rev | tr '|' '\n')
          hits=$(echo "$asked" | grep -x -F -f "$known")
          [ -z "$hits" ] && {{ echo 'Error: No formulae or casks found for "'"$3"'".' >&2; exit 1; }}
          echo "$hits";;
  --prefix) echo "{prefix}";;
  info) cat "{ROOT}/brew-info.json";;
  autoremove) echo "==> Would autoremove 1 unneeded formulae:"; echo "oldlib";;
  missing) [ -n "$FAKE_BREW_MISSING_FAIL" ] && {{ echo "brew: boom" >&2; exit 2; }}
           [ -n "$FAKE_MISSING" ] && {{ echo "foo: libgone libalso"; exit 1; }}; exit 0;;
  *) exit 99;;
esac''')
    node = os.path.join(HOME, '.local/share/mise/installs/node/22.0.0')
    write(os.path.join(node, 'bin/node'), '', 0o755)
    write(os.path.join(node, 'lib/node_modules/leftpad/package.json'), json.dumps({'name': 'leftpad', 'version': '1.3.0'}))
    jq = os.path.join(HOME, '.local/share/mise/installs/jq/1.0.0')
    write(os.path.join(jq, 'bin/jq'), '', 0o755)
    mise_ls = {'node': [{'version': '22.0.0', 'install_path': node, 'installed': True, 'active': True}],
               'jq': [{'version': '1.0.0', 'install_path': jq, 'installed': True, 'active': False}]}
    write(os.path.join(ROOT, 'mise-ls.json'), json.dumps(mise_ls))
    fake('mise', f'''case "$1 $2" in
  "ls --json") cat "{ROOT}/mise-ls.json";;
  "registry ") [ -n "$FAKE_REGISTRY_FAIL" ] && {{ echo "mise: boom" >&2; exit 1; }}
               echo "mystery                       aqua:example/mystery asdf:example/asdf-mystery"; echo "node                          core:node";;
  "ls --prunable") echo "jq  1.0.0";;
  "ls --missing") [ -n "$FAKE_MISE_MISSING_FAIL" ] && {{ echo "mise: boom" >&2; exit 1; }}
                  [ -n "$FAKE_MISSING" ] && echo "terraform  1.9.0 (missing)  ~/.config/mise/config.toml  1.9"; exit 0;;
  "prune --dry-run") echo "mise jq@1.0.0 is prunable: no tracked config or tool stub requires jq" >&2;;
  *) exit 99;;
esac''')
    fake('uv', f'''[ -n "$FAKE_UV_FAIL" ] && {{ echo "uv: boom" >&2; exit 1; }}
case "$1 $2" in
  "tool dir") echo "{HOME}/.local/share/uv/tools";;
  "tool list") echo "uvtool v1.0"; echo "- uvtool";;
  "python dir") echo "{HOME}/.local/share/uv/python";;
  *) exit 99;;
esac''')
    write(os.path.join(FAKEBIN, 'lsof'), '#!/bin/bash\n[ -n "$FAKE_LSOF_FAIL" ] && { echo "lsof: WARNING: boom" >&2; exit 2; }\n[ -n "$FAKE_LSOF_HOLD" ] && { echo 4242; exit 0; }\nexit 1\n', 0o755)
    fake('cargo', 'exit 0')
    fake('rustup', '[ -n "$FAKE_NODEFAULT" ] && echo "stable-x" || echo "stable-x (default)"')
    fake('crontab', 'echo "no crontab for user" >&2; exit 1')
    # brew-owned command: a link from bin into the Cellar
    write(os.path.join(prefix, 'Cellar/foo/1.0/bin/foo'), 'x' * 20000, 0o755)
    os.makedirs(os.path.join(prefix, 'bin'))
    os.symlink('../Cellar/foo/1.0/bin/foo', os.path.join(prefix, 'bin/foo'))
    write(os.path.join(prefix, 'bin/brew'), '', 0o755)
    write(os.path.join(prefix, 'Cellar/node/26.0.0/bin/node'), '', 0o755)           # the same tool as mise's node 22: a duplicate
    os.symlink('../Cellar/node/26.0.0/bin/node', os.path.join(prefix, 'bin/node'))
    # uv-owned command, an unmanaged one, a fragile link, a broken link
    write(os.path.join(HOME, '.local/share/uv/tools/uvtool/bin/uvtool'), '', 0o755)
    lb = os.path.join(HOME, '.local/bin')
    os.makedirs(lb, exist_ok=True)
    os.symlink(os.path.join(HOME, '.local/share/uv/tools/uvtool/bin/uvtool'), os.path.join(lb, 'uvtool'))
    write(os.path.join(lb, 'manual-tool'), 'x' * 2048, 0o755)
    for name, mb, age in (('old-big-tool', 120, 500), ('new-big-tool', 120, 3), ('old-small-tool', 0, 500)):   # sparse files: no real disk used
        p = os.path.join(lb, name)
        write(p, 'x', 0o755)
        if mb:
            os.truncate(p, mb * 1024 * 1024)
        os.utime(p, (time.time() - age * DAY,) * 2)
    write(os.path.join(HOME, '.npm/_npx/x/bin/cachey'), '', 0o755)
    os.symlink(os.path.join(HOME, '.npm/_npx/x/bin/cachey'), os.path.join(lb, 'cachey'))
    os.symlink(os.path.join(HOME, '.npm/_npx/gone/bin/ghost'), os.path.join(lb, 'ghost'))
    # rustup proxies and a stranger in ~/.cargo/bin
    cb = os.path.join(HOME, '.cargo/bin')
    for n in ('rustup', 'cargo'):
        write(os.path.join(cb, n), 'RUSTUP-BINARY', 0o755)
    write(os.path.join(cb, 'mystery'), 'something else entirely', 0o755)
    # applications
    for name, bid, extra in (('Manual', 'com.example.manual', None), ('Cask', 'com.example.cask', None), ('Store', 'com.example.store', 'receipt'),
                           ('Teams', 'com.example.teams', None), ('TeamsHelper', 'com.example.teams.helper', None), ('Lookalike', 'com.example.lookalike', None), ('Updater', 'com.example.updater', None)):
        d = os.path.join(HOME, 'Applications', name + '.app', 'Contents')
        os.makedirs(d)
        with open(os.path.join(d, 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': bid, 'CFBundleShortVersionString': '1.0'}, f)
        if extra:
            os.makedirs(os.path.join(d, '_MASReceipt'))
    # dot items
    write(os.path.join(HOME, '.terraform.d/plugins/x'), 'old')            # tool absent, idle, unmentioned -> orphaned
    age_tree(os.path.join(HOME, '.terraform.d'), 200)
    write(os.path.join(HOME, '.tflint.d/cache'), 'fresh')                 # tool absent, written recently -> referenced
    write(os.path.join(HOME, '.vfox/cache/x'), 'old')                     # tool absent, idle, but mentioned -> referenced
    age_tree(os.path.join(HOME, '.vfox'), 200)
    write(os.path.join(HOME, '.cocoapods/repos/x'), 'old')                # cocoapods formula installed -> owned
    age_tree(os.path.join(HOME, '.cocoapods'), 200)
    write(os.path.join(HOME, '.mystery-dir/x'), 'unknown to the table')
    write(os.path.join(HOME, HOSTILE, 'x'), 'a folder whose name is markup')
    write(os.path.join(HOME, '.oh-my-zsh/x'), 'self-installed')
    write(os.path.join(HOME, '.ssh/config'), f'Host x\n  IdentityFile ~/.ssh/id # {SENTINEL}\n')
    write(os.path.join(HOME, '.npmrc'), f'//registry.npmjs.org/:_authToken={SENTINEL}\n')
    write(os.path.join(HOME, '.zshrc'), f'export PATH="$HOME/.vfox/shims:$PATH"\nexport TOKEN={SENTINEL}\n')


def env(**extra):
    e = dict(os.environ, PATH=f'{FAKEBIN}:/usr/bin:/bin', DOTFILES_TEST_LOG=LOG, HOME=HOME)
    e['DOTFILES_STATE'] = STATE   # never the developer's real accepted-state file
    e.pop('HOMEBREW_NO_AUTO_UPDATE', None)   # the test decides whether Homebrew may refresh, not the developer's shell
    e.update(extra)
    return e


def sh(script, *args, **extra):
    p = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, script), *args], capture_output=True, text=True, env=env(**extra), timeout=180)
    return p.returncode, p.stdout, p.stderr


def audit_json(**extra):
    rc, out, err = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', '--json', **extra)
    return rc, (json.loads(out) if rc == 0 else None), out + err


def by_name(rep, kind, name):
    return next((i for i in rep['items'] if i['kind'] == kind and i['name'] == name), None)


build_world()
rc, rep, raw = audit_json()
check('audit.py exits 0 and returns JSON', rc == 0 and rep is not None, raw)
if rep is None:
    shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(1)
st = {a['name']: a['status'] for a in rep['adapters']}
check('adapters report ok, and missing for tools that are not installed', st['brew'] == 'ok' and st['mise'] == 'ok' and st['uv'] == 'ok' and st['mas'] == 'missing', st)

# ---- ownership of commands
g = lambda kind, name: by_name(rep, kind, name)
check('a command linked into the Cellar is brew-managed', g('binary', 'foo')['cls'] == 'managed' and g('binary', 'foo')['owner'] == 'brew', g('binary', 'foo'))
check('brew itself is claimed', g('binary', 'brew')['cls'] == 'managed', g('binary', 'brew'))
check('a uv tool linked from ~/.local/bin is uv-managed', g('binary', 'uvtool')['owner'] == 'uv' and g('binary', 'uvtool')['cls'] == 'managed', g('binary', 'uvtool'))
check('a binary nothing claims is unmanaged, with its size', g('binary', 'manual-tool')['cls'] == 'unmanaged' and g('binary', 'manual-tool')['kb'] == 2, g('binary', 'manual-tool'))
check('a link into a cache is flagged fragile', 'fragile' in g('binary', 'cachey')['flags'], g('binary', 'cachey'))
check('a broken link is an orphan candidate', g('broken-link', 'ghost')['cls'] == 'orphaned', g('broken-link', 'ghost'))
check('rustup proxies in ~/.cargo/bin are rustup-managed', g('binary', 'cargo')['owner'] == 'rustup' and g('binary', 'cargo')['cls'] == 'managed', g('binary', 'cargo'))
check('a stranger in ~/.cargo/bin is unmanaged', g('binary', 'mystery')['cls'] == 'unmanaged', g('binary', 'mystery'))

# ---- applications
check('an app named by a cask is brew-managed', g('app', 'Cask')['cls'] == 'managed' and g('app', 'Cask')['owner'] == 'brew', g('app', 'Cask'))
check('an app with an App Store receipt is managed by the store', g('app', 'Store')['owner'] == 'mas', g('app', 'Store'))
check('an app with neither is unmanaged', g('app', 'Manual')['cls'] == 'unmanaged', g('app', 'Manual'))

check('a pkg cask is matched by its bundle id, found in pkgutil, and that is exact evidence',
      g('app', 'Teams')['cls'] == 'managed' and g('app', 'Teams')['owner'] == 'brew' and g('app', 'Teams')['tier'] == 'exact', g('app', 'Teams'))
check('a bundle id that merely starts with a cask id does not match (equality, never prefix)', g('app', 'TeamsHelper')['cls'] == 'unmanaged', g('app', 'TeamsHelper'))
check('an app named like an installed cask, with a different bundle id, stays unmanaged and only gets a suggestion',
      g('app', 'Lookalike')['cls'] == 'unmanaged' and g('app', 'Lookalike')['owner'] == 'none'
      and [s['candidate'] for s in g('app', 'Lookalike')['suggestions']] == ['lookalike'] and g('app', 'Lookalike')['suggestions'][0]['tier'] == 'candidate', g('app', 'Lookalike'))
check('a bundle id found only in a cask\'s quit list is not proof: unmanaged, with a suggestion that says so (as with Codex and the chatgpt cask)',
      g('app', 'Updater')['cls'] == 'unmanaged' and g('app', 'Updater')['tier'] is None
      and g('app', 'Updater')['suggestions'][0]['candidate'] == 'teams-like' and 'quit list' in g('app', 'Updater')['suggestions'][0]['evidence'], g('app', 'Updater'))
check('an app with no match has no suggestions', g('app', 'TeamsHelper')['suggestions'] == [] and g('app', 'TeamsHelper')['check'] == 'none', g('app', 'TeamsHelper'))

# ---- install dates: only from a real record, and honest about where they came from
pk = {(p['kind'], p['name']): p for p in rep['inventory']['brew']}
check('a Homebrew formula carries the install time Homebrew recorded', pk[('formula', 'foo')]['installed_at'] == NOW - 400 * DAY and pk[('formula', 'foo')]['date_source'] == 'brew', pk[('formula', 'foo')])
check('a Homebrew cask carries its installed_time', pk[('cask', 'cask-app')]['installed_at'] == NOW - 45 * DAY, pk[('cask', 'cask-app')])
check('a formula with no recorded time gets no date, never an invented one', pk[('formula', 'cocoapods')]['installed_at'] is None, pk[('formula', 'cocoapods')])
mi = next(p for p in rep['inventory']['mise'] if p['name'] == 'node')
check('a mise install is dated from the folder, with the source this OS offers', mi['installed_at'] and mi['date_source'] == BIRTH, mi)
check('a dot-folder is dated too, with the same source', g('dotitem', '.terraform.d')['date_source'] == BIRTH and g('dotitem', '.terraform.d')['installed_at'], g('dotitem', '.terraform.d'))
check('an app is dated', g('app', 'Manual')['installed_at'] and g('app', 'Manual')['date_source'] == BIRTH, g('app', 'Manual'))
check('age_text says nothing when there is no date', age_text(None, 'brew') == '' and birth(HOME + '/no/such/path') == (None, None))
check('age_text speaks in months and years, and marks a bare mtime with ~',
      age_text(NOW - 5 * DAY, 'birthtime', NOW) == 'this month' and age_text(NOW - 400 * DAY, 'brew', NOW) == '13 months ago'
      and age_text(NOW - 800 * DAY, 'mtime', NOW) == '~2.2 years ago', [age_text(NOW - 5 * DAY, 'birthtime', NOW), age_text(NOW - 400 * DAY, 'brew', NOW), age_text(NOW - 800 * DAY, 'mtime', NOW)])
flagged = lambda n: 'stale-candidate' in g('binary', n)['flags']
check('a large, old, unmanaged command is flagged as a stale candidate (where the date is a creation time)',
      flagged('old-big-tool') == (BIRTH == 'birthtime'), (BIRTH, g('binary', 'old-big-tool')['installed_at'], g('binary', 'old-big-tool')['flags']))
check('a recent large command, and an old small one, are not flagged', not flagged('new-big-tool') and not flagged('old-small-tool'), (g('binary', 'new-big-tool')['flags'], g('binary', 'old-small-tool')['flags']))
check('the flag never changes the class', g('binary', 'old-big-tool')['cls'] == 'unmanaged', g('binary', 'old-big-tool'))

# ---- manager verdicts and dot items
check("brew's own orphan verdict is reported with its evidence", g('brew-orphan', 'oldlib') and 'autoremove' in g('brew-orphan', 'oldlib')['evidence'][0], g('brew-orphan', 'oldlib'))
check("mise's own prunable verdict is reported with its reason", g('mise-prunable', 'jq@1.0.0') and 'no tracked config' in g('mise-prunable', 'jq@1.0.0')['evidence'][0], g('mise-prunable', 'jq@1.0.0'))
check('npm globals are read from every node install', any(p['name'] == 'leftpad' for p in rep['inventory'].get('npm', [])), rep['inventory'].get('npm'))
check('a tool folder whose tool is installed nowhere, idle and unmentioned, is an orphan candidate', g('dotitem', '.terraform.d')['cls'] == 'orphaned', g('dotitem', '.terraform.d'))
check('a tool folder written to recently is never an orphan', g('dotitem', '.tflint.d')['cls'] == 'referenced', g('dotitem', '.tflint.d'))
check('a folder a config file mentions is referenced, naming the file only',
      g('dotitem', '.vfox')['cls'] == 'referenced' and g('dotitem', '.vfox')['referenced_by'] == ['~/.zshrc'], g('dotitem', '.vfox'))
check('a folder whose tool is installed is owned', g('dotitem', '.cocoapods')['cls'] == 'owned', g('dotitem', '.cocoapods'))
check('a folder missing from the table is unattributed, never guessed', g('dotitem', '.mystery-dir')['cls'] == 'unattributed', g('dotitem', '.mystery-dir'))
check('a self-installing tool folder gets a neutral verdict', g('dotitem', '.oh-my-zsh')['cls'] == 'unmanaged', g('dotitem', '.oh-my-zsh'))
check('credentials and shell config are never candidates', all(g('dotitem', n)['cls'] == 'config' for n in ('.ssh', '.npmrc', '.zshrc')), [g('dotitem', n)['cls'] for n in ('.ssh', '.npmrc', '.zshrc')])
check('nothing in a keep entry is ever orphaned', not any(i['cls'] == 'orphaned' and i['name'] in ('.ssh', '.npmrc', '.zshrc') for i in rep['items']))

# ---- read-only, and no secrets anywhere
logged = [l.strip() for l in open(LOG)] if os.path.exists(LOG) else []
ok_cmds = {' '.join(a) for a in ALLOWED}
import common  # noqa: E402
check('every command the fake managers received is on the allow-list', logged and all(l in ok_cmds or common.allowed(l.split()) for l in logged), [l for l in logged if l not in ok_cmds and not common.allowed(l.split())])
verbs = {'install', 'uninstall', 'upgrade', 'update', 'remove', 'cleanup', 'delete', '--force', '--zap'}
# whole words only: `--installed` and `cargo install --list` are read-only
mutating = [l for l in logged if l != 'cargo install --list' and any(w in verbs for w in l.split())]
check('no command mutates anything', not mutating, mutating)
check('autoremove and prune only ever ran as dry runs',
      all(('autoremove' not in l or ' -n' in l) and ('prune' not in l or '--dry-run' in l) for l in logged), logged)
rc, md, err = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes')
check('the markdown report prints and carries the evidence header', rc == 0 and '## Sources' in md and 'Not read by this version' in md, md[:200])
check('the report shows ages and explains where dates come from', ' ago' in md and '_Dates:' in md and 'Installed' in md, md[-700:])
check('a planted secret never appears in the report or the JSON', SENTINEL not in md and SENTINEL not in raw and SENTINEL not in err, '')

# ---- a failed adapter must not become evidence
rc, rep2, raw2 = audit_json(FAKE_UV_FAIL='1')
st2 = {a['name']: a['status'] for a in rep2['adapters']}
check('a failing adapter is reported as failed', st2['uv'] == 'failed', st2)
check('so an unclaimed command becomes unknown, not unmanaged', by_name(rep2, 'binary', 'manual-tool')['cls'] == 'unknown', by_name(rep2, 'binary', 'manual-tool'))
check('and an orphan folder becomes unknown, not orphaned', by_name(rep2, 'dotitem', '.terraform.d')['cls'] == 'unknown', by_name(rep2, 'dotitem', '.terraform.d'))
check("a manager's own verdict is still reported", by_name(rep2, 'mise-prunable', 'jq@1.0.0')['cls'] == 'orphaned')
rc, md2, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', FAKE_UV_FAIL='1')
check('the report says so, in a header', 'could not be read' in md2 and 'uv' in md2, md2[:400])

# ---- the HTML map: self-contained, escaped, no secrets, sound layout
import htmlmap  # noqa: E402

page = os.path.join(ROOT, 'map.html')
rc, out, err = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--html', page, '--json')
check('--html writes the page and still prints valid JSON', rc == 0 and os.path.exists(page) and json.loads(out) and 'wrote the map' in err, err[:200])
doc = open(page).read() if os.path.exists(page) else ''
from html.parser import HTMLParser  # noqa: E402


class Inspect(HTMLParser):
    """What the page really contains: its tags, their attributes, and its stylesheet text. Escaped text is just text."""
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.style, self._in_style = set(), [], '', False
        self.scripts, self._script, self.order = [], None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.order.append((tag, dict(attrs)))
        self.attrs += [(tag, k, v or '') for k, v in attrs]
        self._in_style = tag == 'style'
        if tag == 'script':
            self._script = ''

    def handle_endtag(self, tag):
        self._in_style = False
        if tag == 'script' and self._script is not None:
            self.scripts.append(self._script)
            self._script = None

    def handle_data(self, data):
        if self._in_style:
            self.style += data
        if self._script is not None:
            self._script += data


ins = Inspect()
ins.feed(doc)
bad_tags = ins.tags & {'link', 'img', 'iframe', 'object', 'embed', 'base', 'form', 'video', 'audio', 'source', 'svg'}
bad_attrs = [(t_, k) for t_, k, v in ins.attrs if k in ('src', 'href', 'srcset', 'action') or k.startswith('on') or 'http' in v.lower()]
check('the page is self-contained: no links, images, frames, forms or external resources, and exactly one script with no src',
      not bad_tags and not bad_attrs and len(ins.scripts) == 1 and 'url(' not in ins.style and '@import' not in ins.style, (bad_tags, bad_attrs, len(ins.scripts)))
check('a folder named like markup is escaped everywhere', '<img src=x' not in doc and '&lt;img src=x' in doc, doc.count('<img'))
check('the page carries no secret', SENTINEL not in doc)
check('the treemap groups by owner and sizes packages too', 'Homebrew formulae' in doc and 'mise tools' in doc and 'class="t c-' in doc, doc[:200])
check('an orphan candidate is drawn with its own pattern and listed with its key for tidy',
      'c-orphaned' in doc and g('dotitem', '.terraform.d')['key'].replace('&', '&amp;') in doc, '')
check('the page says it names things on this machine', 'keep it local' in doc)
check('the map tooltips and rows carry install dates', 'this version installed' in doc, '')
# every place a name can land is escaped, including the ones the small fake world never draws (a large block's label, a detail row)
EVIL = '<img src=x onerror=alert(1)>"\'&'
tm = htmlmap.treemap([{'group': EVIL, 'name': EVIL, 'kb': 1000, 'cls': 'managed', 'tip': EVIL}])
check('a large block escapes its label, tooltip and group caption', '<img' not in tm and tm.count('&lt;img src=x') >= 3, tm[:300])
row = htmlmap.rows([{'path': HOME + '/' + EVIL, 'name': EVIL, 'kb': 5, 'cls': 'orphaned', 'key': 'dotitem:' + EVIL, 'evidence': [EVIL],
                     'referenced_by': [EVIL], 'flags': [EVIL]}], HOME, '')
check('a detail row escapes path, evidence, references, flags and key', '<img' not in row and row.count('&lt;img src=x') >= 5, row[:300])


# ---- interactive map: a strict policy that pins the one script, a script that cannot build markup or reach the network, and a page that still reads without it
import hashlib, base64, re as _re  # noqa: E402
import mapjs  # noqa: E402

first_tags = [t for t, _a in ins.order if t not in ('html', 'head')][:2]
cspm = next((a for t, a in ins.order if t == 'meta' and a.get('http-equiv') == 'Content-Security-Policy'), {'content': '(no policy)'})
check('the policy meta is the first thing in the head', len(ins.order) > 2 and ins.order[2][1].get('http-equiv') == 'Content-Security-Policy', ins.order[:4])
digest = 'sha256-' + base64.b64encode(hashlib.sha256(ins.scripts[0].encode('utf-8')).digest()).decode()
check('the policy is exactly: nothing by default, only this script by its hash, inline styles, no base, no forms',
      cspm['content'] == f"default-src 'none'; script-src '{digest}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'", cspm['content'])
check('the pinned hash is the hash of the script text that is actually in the page', digest in cspm['content'] and ins.scripts[0] == mapjs.SCRIPT, '')
check('the script has no src and the page has no inline event handlers or javascript: links',
      not any(k == 'src' for t_, k, v in ins.attrs if t_ == 'script') and not any(k.startswith('on') for _t, k, _v in ins.attrs) and not any('javascript:' in v for _t, _k, v in ins.attrs), '')
hits = [f for f in mapjs.FORBIDDEN if f in mapjs.SCRIPT]
check('the script contains nothing that builds markup from text, loads code, stores data or reaches a network', not hits, hits)
check('the script never builds markup: it writes text, moves existing nodes, and clones the pre-rendered detail', 'textContent' in mapjs.SCRIPT and 'cloneNode' in mapjs.SCRIPT and not _re.search(r'\bnew\s+Function\b|\.html\s*=', mapjs.SCRIPT), '')
check('copied commands put one key per line, never on a shared shell line', "Array.from(selected.keys()).join('\\n')" in mapjs.SCRIPT and ".join(' ')" not in mapjs.SCRIPT and "command + '\\n' +" in mapjs.SCRIPT, '')
check('tidy can only be copied when every selected item is an orphan candidate', "if (cls !== 'orphaned') { allOrphans = false; }" in mapjs.SCRIPT and 'bTidy.disabled = !allOrphans' in mapjs.SCRIPT, '')
check('there is no data blob: no JSON script, and none of the report is embedded as JSON', 'application/json' not in doc and '"cls":' not in doc and '"installed_at"' not in doc, '')
check('the policy builder pins whatever script it is given (change the script, the hash changes)', mapjs.csp('x') != mapjs.csp('y') and mapjs.script_hash('x') in mapjs.csp('x'), '')
check('every control the page needs from scripts is hidden until the script runs', [a for t_, a in ins.order if 'data-js' in a] and all('hidden' in a for t_, a in ins.order if 'data-js' in a), [a for t_, a in ins.order if 'data-js' in a and 'hidden' not in a][:2])
check('detail blocks, group views, the selection bar and the copy box start hidden; the overview does not', all(
      any(a.get('id') == i_ and 'hidden' in a for t_, a in ins.order) for i_ in ('dstore', 'selbar', 'copybox', 'detail', 'crumbs'))
      and not any(a.get('id') == 'tm-overview' and 'hidden' in a for t_, a in ins.order), '')
groups_hidden = [a for t_, a in ins.order if t_ == 'div' and 'data-g' in a]
check('every group has a complete hidden layout of its own', groups_hidden and all('hidden' in a for a in groups_hidden) and len(groups_hidden) >= 4, len(groups_hidden))
ov = doc[doc.index('id="tm-overview"'):doc.index('id="detail"')]
n_over = ov.count('data-n="')
check('the overview blocks all have a pre-rendered detail block, and nothing else points nowhere',
      all(f'data-d="{m}"' in doc for m in _re.findall(r'data-n="(n\d+)"', doc)) and n_over > 5, n_over)
check('the manager tables are present in the page itself, not built by the script', 'What each manager has installed' in doc and '<table class="sortable">' in doc and 'Homebrew (' in doc and 'mise (' in doc, '')
check('a table header sorts by a real button', _re.search(r'<th><button type="button" data-type="num">Size</button></th>', doc) is not None, '')
check('list rows carry a select box (shown by the script) and the key, and the filter attributes', 'class="pick" data-js hidden' in doc and 'data-acc="0"' in doc and 'aria-pressed="false"' in doc, '')
# hostile input, everywhere it can land
EV2 = '<img src=x onerror=alert(1)>"\'&</script><script>alert(2)</script>'
lv_h = [{'group': EV2, 'name': EV2, 'kb': 5000, 'cls': 'orphaned', 'tip': EV2, 'key': 'dotitem:' + EV2, 'acc': True, 'id': 'n0', 'body': htmlmap.item_inner(
        {'key': 'dotitem:' + EV2, 'name': EV2, 'cls': 'orphaned', 'kind': 'dotitem', 'path': HOME + '/' + EV2, 'evidence': [EV2], 'referenced_by': [EV2], 'flags': [EV2],
         'suggestions': [{'candidate': EV2, 'command': EV2, 'evidence': EV2}], 'accepted': {'date': EV2, 'note': EV2}, 'acceptance_void': None, 'installed_at': None, 'date_source': None}, {})}]
hostile = htmlmap.treemap(lv_h) + htmlmap.group_views(lv_h) + lv_h[0]['body'] + htmlmap.select_button('dotitem:' + EV2, EV2)
hostile += htmlmap.rows([{'key': 'dotitem:' + EV2, 'name': EV2, 'cls': 'orphaned', 'kind': 'dotitem', 'path': HOME + '/' + EV2, 'kb': 3, 'evidence': [EV2], 'referenced_by': [EV2], 'flags': [EV2],
                         'suggestions': [], 'accepted': {'date': EV2, 'note': EV2}}], HOME, '')
hostile += htmlmap.manager_panels({'items': [], 'duplicates': [], 'inventory': {EV2: [{'kind': EV2, 'name': EV2, 'version': EV2, 'kb': 3, 'installed_at': 1, 'date_source': 'brew'}]}})
ih = Inspect()
ih.feed(hostile)
check('hostile text (names, keys, notes, suggestions, owners) is escaped in blocks, group views, details, rows, buttons and tables',
      '<img' not in hostile and '<script' not in hostile and not ih.scripts and not any(k.startswith('on') for _t, k, _v in ih.attrs) and hostile.count('&lt;img src=x') >= 8, (hostile.count('&lt;img src=x'), ih.tags))
check('a key with a control character gets no select box and no select button',
      'class="pick"' not in htmlmap.rows([{'key': 'dotitem:a\nb', 'name': 'a', 'cls': 'orphaned', 'kind': 'dotitem', 'path': '', 'kb': 1, 'evidence': [], 'referenced_by': [], 'flags': [], 'suggestions': []}], HOME, '')
      and 'class="sel"' not in htmlmap.select_button('dotitem:a\nb', 'orphaned') and 'control characters' in htmlmap.select_button('dotitem:a\nb', 'orphaned'), '')
check('accepted items are marked by a glyph and an outline, not by colour alone', '✓' in doc and 'acc-sw' in doc and '.t.acc' in ins.style, '')
if shutil.which('node'):
    js_path = os.path.join(ROOT, 'map.js')
    open(js_path, 'w').write(ins.scripts[0])
    r_ = subprocess.run(['node', '--check', js_path], capture_output=True, text=True)
    check('the script is valid JavaScript (node --check)', r_.returncode == 0, r_.stderr[:300])
else:
    print('SKIP  node is not installed, so the script was not syntax-checked here')

sizes_in = [(10, 'a'), (6, 'b'), (4, 'c'), (2, 'd'), (1, 'e')]
rects = htmlmap.squarify([s for s, _ in sizes_in], 0, 0, 100, 100)
area = [w * h for (_x, _y, w, h) in rects]
check('treemap rects fill the box and stay inside it', abs(sum(area) - 10000) < 1e-6 and all(x >= -1e-9 and y >= -1e-9 and x + w <= 100 + 1e-9 and y + h <= 100 + 1e-9 for (x, y, w, h) in rects), rects)
check('treemap areas are proportional to sizes', all(abs(a / 10000 - s / 23) < 1e-9 for a, (s, _) in zip(area, sizes_in)), area)

# ---- duplicates: the same tool through two managers is a finding; a system copy is a note; versions under one manager are a line
import audit  # noqa: E402
import checker  # noqa: E402
import identity  # noqa: E402

check('canon folds the known spellings of a tool and leaves everything else alone',
      [identity.canon(n) for n in ('python@3.14', 'python3.13', 'Python', 'nodejs', 'node@22', 'openjdk@21', 'jq', 'npm:prettier', 'python-tk@3.14', '@types/node')]
      == ['python', 'python', 'python', 'node', 'node', 'java', 'jq', 'npm:prettier', 'python-tk', '@types/node'],
      [identity.canon(n) for n in ('python@3.14', 'python3.13', 'Python', 'nodejs', 'node@22', 'openjdk@21', 'jq', 'npm:prettier', 'python-tk@3.14', '@types/node')])
PBIN = os.path.join(HOME, 'brewprefix', 'bin')
rc, repd, rawd = audit_json(PATH=f'{PBIN}:{FAKEBIN}:/usr/bin:/bin')
dn = {d['tool']: d for d in repd['duplicates']}
check('a tool installed by brew and by mise is a duplicate finding, with both copies', set(dn) == {'node'} and {c['owner'] for c in dn['node']['copies']} == {'brew', 'mise'}, repd['duplicates'])
check('the active copy is the first on PATH, and it is the brew one here', dn['node']['active']['owner'] == 'brew' and dn['node']['active']['version'] == '26.0.0', dn['node']['active'])
check('a tool under one manager only (jq in mise) is not a duplicate', 'jq' not in dn and not any(v['tool'] == 'node' for v in repd['versions']), repd['versions'])
rc, mdd, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', PATH=f'{PBIN}:{FAKEBIN}:/usr/bin:/bin')
check('the report has a duplicates section that says active is the audit\'s PATH', '## Duplicate installs (1)' in mdd and "audit's PATH" in mdd and '| `node` |' in mdd, mdd[-1500:])

sysd = os.path.join(ROOT, 'sysbin')
mbin = os.path.join(ROOT, 'mbin')
shimd = os.path.join(ROOT, 'shims')
write(os.path.join(sysd, 'python3'), '', 0o755)
write(os.path.join(sysd, 'java'), '', 0o755)
write(os.path.join(mbin, 'python3'), '', 0o755)
write(os.path.join(shimd, 'node'), '', 0o755)
mk = lambda owner, kind, name, ver, path=None: {'owner': owner, 'kind': kind, 'name': name, 'version': ver, 'path': path}
fres = {'brew': {'status': 'ok', 'packages': [mk('brew', 'formula', 'python@3.13', '3.13.1'), mk('brew', 'formula', 'python@3.14', '3.14.1'), mk('brew', 'formula', 'nodejs', '26.0'), mk('brew', 'formula', 'corepack', '0.1')], 'extra': {}},
        'mise': {'status': 'ok', 'packages': [mk('mise', 'tool', 'python', '3.14.8', mbin), mk('mise', 'tool', 'java', '17'), mk('mise', 'tool', 'java', '21'),
                                              mk('mise', 'tool', 'node', '18'), mk('mise', 'tool', 'node', '22')], 'extra': {'shims': shimd}},
        'npm': {'status': 'ok', 'packages': [mk('npm', 'package', 'leftpad', '1.0'), mk('npm', 'package', 'leftpad', '1.0'), {**mk('npm', 'package', 'corepack', '0.2'), 'bundled': True}], 'extra': {}}}
real_prefixes = audit.SYSTEM_PREFIXES
audit.SYSTEM_PREFIXES = (os.path.realpath(sysd),)
try:
    fctx = audit.Ctx(HOME, [shimd, sysd, mbin], [])
    fd, fv = audit.find_duplicates(fctx, fres, [])
    fdn = {d['tool']: d for d in fd}
    check('python@3.13, python@3.14 and mise python are one tool, found through two managers', set(fdn) == {'python', 'node'} and [c['owner'] for c in fdn['python']['copies']] == ['brew', 'brew', 'mise'], fd)
    check('a system copy next to managed ones is a note on the finding, never a copy of its own',
          any('system copy' in n for n in fdn['python']['notes']), fdn['python']['notes'])
    check('no copy in the finding is the system one', all(c['owner'] != 'system' for d in fd for c in d['copies']), fd)
    check('a system copy first on PATH is reported as the active one, not as shadowed',
          fdn['python']['active']['owner'] == 'system' and any('first on PATH' in n for n in fdn['python']['notes']), (fdn['python']['active'], fdn['python']['notes']))
    check('a mise shim is the active node (a pointer, not a copy), and nodejs normalises to node', fdn['node']['active']['owner'] == 'mise' and 'shim' in fdn['node']['active']['how'], fdn['node']['active'])
    check('versions under one manager are a versions line, not a finding, and carry the system note',
          [(v['tool'], v['owner'], v['versions']) for v in fv] == [('java', 'mise', ['17', '21'])] and fv[0]['notes'], fv)
    check('the same npm package at the same version under several node installs is not reported, and bundled packages are skipped', 'leftpad' not in fdn and all(v['tool'] != 'leftpad' for v in fv) and 'corepack' not in fdn, (fd, fv))
    fctx2 = audit.Ctx(HOME, [mbin, sysd, shimd], [])
    fd2, _ = audit.find_duplicates(fctx2, fres, [])
    check('PATH order decides which copy is active and which system copy is shadowed',
          {d['tool']: d for d in fd2}['python']['active']['owner'] == 'mise' and any('shadowed system copy' in n for n in {d['tool']: d for d in fd2}['python']['notes']), fd2)
finally:
    audit.SYSTEM_PREFIXES = real_prefixes
rc, repf, _ = audit_json(FAKE_UV_FAIL='1')
check('a failed adapter marks the duplicate list as possibly incomplete', repf['duplicates_incomplete'] == ['uv'], repf['duplicates_incomplete'])
check('the map has a duplicates section', '<h2>Duplicate installs (1)' in htmlmap.render(repd), '')
check('a duplicate\'s tool, owner and notes are escaped in the map', htmlmap.dup_section({'duplicates': [{'tool': EVIL, 'active': None, 'copies': [{'owner': EVIL, 'version': ''}], 'notes': [EVIL]}], 'versions': []}).count('<img') == 0, '')

# ---- missing toolchains and dead PATH entries
check('with nothing missing, the report says none were reported, by the sources that were actually checked',
      repd['missing_checks'] == [{'source': s_, 'status': 'checked'} for s_ in ('mise', 'brew', 'rustup')] and not [i for i in repd['items'] if i['cls'] == 'missing']
      and 'None reported by mise, brew, rustup.' in mdd, repd['missing_checks'])
DEAD = os.path.join(ROOT, 'no-such-bin')
rc, repm, rawm = audit_json(FAKE_MISSING='1', FAKE_NODEFAULT='1', PATH=f'{PBIN}:{FAKEBIN}:{DEAD}:/var/run/com.apple/fake/bin:/usr/bin:/bin')
ms = {i['name']: i for i in repm['items'] if i['cls'] == 'missing'}
check('a toolchain mise lists as missing is reported with the manager\'s own line',
      'terraform@1.9.0' in ms and ms['terraform@1.9.0']['owner'] == 'mise' and 'ls --missing' in ms['terraform@1.9.0']['evidence'][0] and '1.9.0 (missing)' in ms['terraform@1.9.0']['evidence'][0], ms)
check('`brew missing` exiting 1 with a pair is read as a finding, not a failure', 'foo' in ms and ms['foo']['owner'] == 'brew' and 'libgone libalso' in ms['foo']['evidence'][0], ms)
check('rustup with toolchains but none marked default is a signal', 'rustup default' in ms and ms['rustup default']['owner'] == 'rustup', ms)
check('a missing toolchain is its own class, never an orphan, and tidy refuses it', ms['terraform@1.9.0']['cls'] == 'missing' and all(i['cls'] != 'orphaned' or i['kind'] != 'missing-toolchain' for i in repm['items']), '')
check('the folder waiting for a missing toolchain is not an orphan candidate: the manager declares the tool',
      by_name(repm, 'dotitem', '.terraform.d')['cls'] == 'referenced' and 'declares' in by_name(repm, 'dotitem', '.terraform.d')['evidence'][0], by_name(repm, 'dotitem', '.terraform.d'))
check('and the missing item notes that its folder is present', any('.terraform.d' in e_ and 'present' in e_ for e_ in ms['terraform@1.9.0']['evidence']), ms['terraform@1.9.0'])
dead = [i for i in repm['items'] if i['kind'] == 'path-dead']
check('a PATH entry that does not exist is listed as config, and an OS-managed one is not', [i['path'] for i in dead] == [DEAD] and dead[0]['cls'] == 'config', dead)
afile = os.path.join(ROOT, 'a-file-on-path')
write(afile, 'x')
rc, repf2, _ = audit_json(PATH=f'{FAKEBIN}:{afile}:/usr/bin:/bin')
check('a PATH entry that is a file says it is not a folder', [i['evidence'] for i in repf2['items'] if i['kind'] == 'path-dead'] == [['on PATH, but it is not a folder']], [i['evidence'] for i in repf2['items'] if i['kind'] == 'path-dead'])
rc, mdm, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', FAKE_MISSING='1', PATH=f'{PBIN}:{FAKEBIN}:{DEAD}:/usr/bin:/bin')
check('the report lists missing toolchains and the dead entry', '(2 missing)' in mdm and '`terraform@1.9.0`' in mdm and 'does not exist' in mdm and 'None reported' not in mdm, mdm[mdm.find('## Missing'):][:900])
rc, repx, _ = audit_json(FAKE_MISE_MISSING_FAIL='1', FAKE_BREW_MISSING_FAIL='1')
check('a failed check is never reported as none: it says could not check, and the adapter itself stays ok',
      {c['source']: c['status'] for c in repx['missing_checks']} == {'mise': 'failed', 'brew': 'failed', 'rustup': 'checked'}
      and {a['name']: a['status'] for a in repx['adapters']}['brew'] == 'ok', repx['missing_checks'])
rc, mdx, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', FAKE_MISE_MISSING_FAIL='1', FAKE_BREW_MISSING_FAIL='1')
check('the report says "could not check" and does not say none were reported by them', 'Could not check mise, brew' in mdx and 'None reported by rustup' in mdx and 'None reported by mise' not in mdx, mdx[mdx.find('## Missing'):][:500])
check('the two new commands are on the allow-list and nothing like them is', common.allowed(['brew', 'missing']) and common.allowed(['mise', 'ls', '--missing'])
      and not common.allowed(['brew', 'missing', '--force']) and not common.allowed(['mise', 'ls', '--missing', 'x']))
os.environ.update(PATH=f'{FAKEBIN}:' + os.environ['PATH'], DOTFILES_TEST_LOG=LOG)
st0 = common.run(['brew', 'missing'], ok_codes=(0, 1))
os.environ['FAKE_MISSING'] = '1'
st1 = common.run(['brew', 'missing'], ok_codes=(0, 1))
st2 = common.run(['brew', 'missing'])
os.environ.pop('FAKE_MISSING')
os.environ['FAKE_BREW_MISSING_FAIL'] = '1'
st3 = common.run(['brew', 'missing'], ok_codes=(0, 1, 2))
os.environ.pop('FAKE_BREW_MISSING_FAIL')
check('a non-zero exit code is ok only when listed AND it came with output (exit 1 with a list is ok, bare exit 2 with only stderr never is)',
      st0 == ('ok', '') and st1[0] == 'ok' and 'libgone' in st1[1] and st2[0] == 'failed' and st3[0] == 'failed', (st0, st1, st2, st3))
check('the map has the section', 'Missing toolchains and dead PATH entries (3 missing)' in htmlmap.render(repm), '')
check('names in the missing section are escaped', '<img' not in htmlmap.missing_section({'home': HOME, 'missing_checks': [], 'items': [
      {'cls': 'missing', 'kind': 'missing-toolchain', 'name': EVIL, 'owner': EVIL, 'evidence': [EVIL], 'path': ''},
      {'cls': 'config', 'kind': 'path-dead', 'name': EVIL, 'owner': 'none', 'evidence': [EVIL], 'path': '/' + EVIL}]}), '')

# ---- item checker: suggestions only, never ownership
check('an unmanaged app whose name is a cask gets a candidate, with the command as text and the basis stated',
      [(x['manager'], x['candidate'], x['tier'], x['command']) for x in g('app', 'Manual')['suggestions']] == [('brew', 'manual', 'candidate', 'brew install --cask manual')]
      and 'name match only' in g('app', 'Manual')['suggestions'][0]['evidence'], g('app', 'Manual'))
check('a candidate never changes the class or the owner', g('app', 'Manual')['cls'] == 'unmanaged' and g('app', 'Manual')['owner'] == 'none' and g('binary', 'manual-tool')['cls'] == 'unmanaged', '')
check('an unmanaged command gets a formula candidate, and one from the mise registry',
      [x['command'] for x in g('binary', 'manual-tool')['suggestions']] == ['brew install manual-tool']
      and [x['command'] for x in g('binary', 'mystery')['suggestions']] == ['mise use -g aqua:example/mystery'], (g('binary', 'manual-tool')['suggestions'], g('binary', 'mystery')['suggestions']))
check('a cask that is already installed is not suggested again (step 1 already says so)', 'lookalike' not in [x['candidate'] for x in g('app', 'Lookalike')['suggestions'] if x.get('command')], g('app', 'Lookalike')['suggestions'])
check('no candidate is said only where the search succeeded', g('binary', 'old-small-tool')['check'] == 'none' and g('binary', 'manual-tool')['check'] == 'found', (g('binary', 'old-small-tool')['check']))
open(LOG, 'w').close()
open(os.path.join(ROOT, 'brew-env.log'), 'w').close()
rc, repc, _ = audit_json()
cmds = [l.strip() for l in open(LOG)]
check('the checker makes exactly two brew searches and one registry call, however many items there are',
      len([c for c in cmds if c.startswith('brew search --cask')]) == 1 and len([c for c in cmds if c.startswith('brew search --formula')]) == 1 and cmds.count('mise registry') == 1, [c[:60] for c in cmds])
envs = [l.split() for l in open(os.path.join(ROOT, 'brew-env.log'))]
check('every brew command except search runs with auto-update disabled; search is left to Homebrew', all(v == '1' for c, v in envs if c != 'search') and all(v == 'unset' for c, v in envs if c == 'search') and any(c == 'search' for c, v in envs), envs)
check('the header says the index was cached when nothing touched it', repc['checker']['index'] == 'cached' and repc['checker']['brew'] == 'ok' and repc['checker']['mise'] == 'ok', repc['checker'])
rc, repr_, _ = audit_json(FAKE_REFRESH='1')
check('the header says refreshed when Homebrew refreshed its own index during the search', repr_['checker']['index'] == 'refreshed', repr_['checker'])
open(os.path.join(ROOT, 'brew-env.log'), 'w').close()
rc, repo, _ = audit_json(FAKE_REFRESH='1')
age_old = time.time() - 3 * DAY
os.utime(os.path.join(HOME, 'Library/Caches/Homebrew/api/cask_names.txt'), (age_old, age_old))
open(os.path.join(ROOT, 'brew-env.log'), 'w').close()
rc, out_o, err_o = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', '--offline', '--json', FAKE_REFRESH='1')
repo = json.loads(out_o)
envs = [l.split() for l in open(os.path.join(ROOT, 'brew-env.log'))]
check('--offline keeps auto-update off for search too, so nothing is refreshed, and the header says so',
      all(v == '1' for c, v in envs) and repo['checker']['index'] == 'cached (offline)' and repo['checker']['offline']
      and abs(os.stat(os.path.join(HOME, 'Library/Caches/Homebrew/api/cask_names.txt')).st_mtime - age_old) < 2, (envs, repo['checker']))
rc, repfail, rawf = audit_json(FAKE_SEARCH_FAIL='1')
check('a failed search is "could not check", never "no candidate", and says so in the header',
      repfail['checker']['brew'] == 'could not check' and g_ if (g_ := by_name(repfail, 'binary', 'old-small-tool'))['check'] == 'could-not-check' else False, repfail['checker'])
check('a failed search leaves the earlier evidence-based suggestions alone', by_name(repfail, 'app', 'Updater')['suggestions'][0]['candidate'] == 'teams-like', by_name(repfail, 'app', 'Updater')['suggestions'])
rc, mdf, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', FAKE_SEARCH_FAIL='1')
check('the report warns and does not claim "no candidate" for what it could not check', 'Homebrew: could not check' in mdf and 'No candidate found for' not in mdf, mdf[mdf.find('## Adoption'):][:600])
rc, repreg, _ = audit_json(FAKE_REGISTRY_FAIL='1')
check('a failed mise registry call is reported, and Homebrew results still stand', repreg['checker']['mise'] == 'could not check' and by_name(repreg, 'app', 'Manual')['suggestions'], repreg['checker'])
rc, mdc, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes', '--check', 'Manual', '--check', 'Nothing Here')
check('--check NAME adds the name to the report, with a candidate or the honest absence of one',
      '**Checked `Manual`:** `brew install --cask manual`' in mdc and '**Checked `Nothing Here`:** no candidate found' in mdc, mdc[mdc.find('## Adoption'):][-700:])
rc, _, errb = sh('audit.py', '--home', HOME, '--check', '--version')
rc2, _, errb2 = sh('audit.py', '--home', HOME, '--check', 'a;rm -rf x')
check('--check refuses anything but a plain name', rc != 0 and rc2 != 0 and 'plain name' in errb2, (rc, rc2, errb2))
st_, none_ = checker.search('--cask', ['zzz-nothing']) if (os.environ.update(PATH=f'{FAKEBIN}:' + os.environ['PATH']) or True) else None
check('a search with no match is an empty answer, not a failure (Homebrew exits 1 and says so on stderr)', (st_, none_) == ('ok', set()), (st_, none_))
sg, oc, hd = checker.check(HOME, {'k': 'Lookalike'}, {}, installed_casks={'lookalike'})
check('an installed cask is skipped by the checker itself', sg['k'] == [] and oc['k'] == 'none', (sg, oc))
G = lambda *a: common.allowed(['brew', 'search', *a])
check('the allow-list takes only a plain list of names for brew search',
      G('--cask', '/^(firefox|a.b_c-d)$/') and G('--formula', '/^(oras)$/')
      and not any(G(*x) for x in (('--cask', '/^(-x)$/'), ('--cask', '--version'), ('--cask', '/^(a|b/'), ('--cask', '/^(a b)$/'), ('--cask', '/^(a|)$/'), ('--cask', '/^(a)$/ ; x'),
                                  ('--desc', '/^(a)$/'), ('--cask', '/^(A)$/'), ('--cask', '/^(' + 'a|' * 3000 + 'b)$/'), ('/^(a)$/',))), '')
check('mise registry is allowed with no arguments only', common.allowed(['mise', 'registry']) and not common.allowed(['mise', 'registry', '--json']) and not common.allowed(['mise', 'install', 'x']))
check('the map has the adoption section and escapes what it prints', 'Adoption candidates' in htmlmap.render(repc) and '<img' not in htmlmap.candidates_section(
      {'checker': {'ran': True, 'index': EVIL, 'brew': EVIL, 'mise': 'ok'}, 'items': [{'cls': 'unmanaged', 'kind': 'app', 'name': EVIL, 'suggestions': [{'candidate': EVIL, 'command': EVIL, 'evidence': EVIL}]}]}), '')
check('nothing ever installs: no logged command is an install, and no suggestion was run',
      not any(l.split()[:2] in (['brew', 'install'], ['mise', 'use'], ['mise', 'install']) for l in [l.strip() for l in open(LOG)]), '')

# ---- accepted state: only a typed accept writes it, only unresolved items can be accepted, and a changed item is unresolved again
import accepted  # noqa: E402

check('the audits so far never created the accepted-state file (the audit is read-only)', not os.path.exists(STATE_FILE) and not os.path.exists(STATE))
sa = os.path.join(ROOT, 'accept-scan.json')
json.dump(rep, open(sa, 'w'))
K = lambda kind, name: g(kind, name)['key']
acc = lambda *a, **kw: sh('accept.py', sa, *a, **kw)
rc, out, err = acc('--key', K('app', 'Manual'), '--key', K('binary', 'manual-tool'), '--key', K('broken-link', 'ghost'), '--note', 'work VPN, installed by IT')
check('accept records the named unresolved items and says exactly where', rc == 0 and out.count('accepted `') == 3 and STATE_FILE in out, out)
recs, warn = accepted.load(STATE_FILE)
check('the record holds note, date, class and name', warn is None and recs[K('app', 'Manual')]['note'] == 'work VPN, installed by IT' and recs[K('app', 'Manual')]['cls'] == 'unmanaged'
      and recs[K('app', 'Manual')]['name'] == 'Manual' and recs[K('app', 'Manual')]['date'][:2] == '20', recs)
check('the state file is private: 0600 in a 0700 folder', oct(os.stat(STATE_FILE).st_mode & 0o777) == '0o600' and oct(os.stat(STATE).st_mode & 0o777) == '0o700', (oct(os.stat(STATE_FILE).st_mode), oct(os.stat(STATE).st_mode)))
check('no temporary file is left behind', os.listdir(STATE) == ['accepted.json'], os.listdir(STATE))
before_bytes = open(STATE_FILE, 'rb').read()
rc, out, err = acc('--key', K('binary', 'foo'), '--key', K('dotitem', '.cocoapods'), '--key', K('dotitem', '.ssh'), '--key', K('app', 'Cask'), '--key', 'nonsense', '--note', 'x')
check('managed, owned and config items, and unknown keys, cannot be accepted', rc == 1 and out.count('already fine') == 4, out)
check('and nothing was written for them', open(STATE_FILE, 'rb').read() == before_bytes and 'not in this audit' in out, out)
for label, args in (('an empty note', ['--note', '  ']), ('a 201-character note', ['--note', 'x' * 201]), ('a note with a control character', ['--note', 'a\x07b'])):
    rc, out, err = acc('--key', K('app', 'Updater'), *args)
    check(f'{label} is refused and nothing is written', rc != 0 and open(STATE_FILE, 'rb').read() == before_bytes, (rc, out, err))
rc, out, err = acc('--key', 'app:/x\nacceptme', '--note', 'n')
check('a key with a newline is refused', rc != 0 and 'control characters' in out + err and open(STATE_FILE, 'rb').read() == before_bytes, (out, err))
rc, rep_a, _ = audit_json()
ga = lambda kind, name: by_name(rep_a, kind, name)
check('an accepted item carries the acceptance, and its class is unchanged', ga('app', 'Manual')['accepted']['note'] == 'work VPN, installed by IT' and ga('app', 'Manual')['cls'] == 'unmanaged', ga('app', 'Manual'))
cv0, cv1 = rep['coverage'], rep_a['coverage']
check('accepting three items moves exactly three from unresolved to accepted, and the total is unchanged',
      cv1['accepted'] == cv0['accepted'] + 3 and cv1['unresolved'] == cv0['unresolved'] - 3 and cv1['green'] == cv0['green'] and cv1['total'] == cv0['total'], (cv0, cv1))
manual_unres = sum(1 for i in rep_a['items'] if i['cls'] in accepted.UNRESOLVED and not i['accepted'])
manual_green = sum(1 for i in rep_a['items'] if i['cls'] in ('managed', 'owned'))
check('coverage equals a count made straight from the items; config and system are in neither number', cv1['unresolved'] == manual_unres and cv1['green'] == manual_green
      and not any(i['cls'] in ('config', 'system') for i in rep_a['items'] if i['accepted']), cv1)
rc, mda, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes')
check('the report shows coverage and an Accepted section with the note, and no longer lists an accepted orphan as a candidate',
      '**Coverage:**' in mda and '## Accepted (3)' in mda and 'work VPN, installed by IT' in mda and mda.count('ghost') >= 1 and K('broken-link', 'ghost') not in mda.split('## Orphan candidates')[-1].split('\n## ')[0], mda[mda.find('**Coverage'):][:500])
# tidy: an accepted orphan is held, and still offered once forgotten
rc, out, err = sh('tidy.py', sa, '--key', K('broken-link', 'ghost'))
check('tidy refuses an accepted orphan and says why', 'mv -- ' not in out and 'you accepted it' in out and 'work VPN' in out, out)
rc, out, err = acc('--forget', '--key', K('broken-link', 'ghost'))
check('--forget removes only the named acceptance', rc == 0 and set(accepted.load(STATE_FILE)[0]) == {K('app', 'Manual'), K('binary', 'manual-tool')}, out)
rc, out, err = sh('tidy.py', sa, '--key', K('broken-link', 'ghost'))
check('after forgetting, tidy offers the orphan again', 'mv -- ' in out, out)
rc, out, err = acc('--forget', '--key', K('app', 'Cask'))
check('forgetting something that was not accepted changes nothing', rc == 1 and 'was not accepted' in out and len(accepted.load(STATE_FILE)[0]) == 2, out)
# a changed item is unresolved again: make the accepted app managed by a cask
bi = json.load(open(os.path.join(ROOT, 'brew-info.json')))
bi['casks'].append({'token': 'manual-cask', 'installed': '1', 'artifacts': [{'app': ['Manual.app']}]})
json.dump(bi, open(os.path.join(ROOT, 'brew-info.json'), 'w'))
rc, rep_v, _ = audit_json()
gv = by_name(rep_v, 'app', 'Manual')
check('when an accepted item changes class the acceptance is void, and the item is unresolved or green on its merits',
      gv['cls'] == 'managed' and gv['accepted'] is None and gv['acceptance_void']['cls'] == 'unmanaged' and gv['acceptance_void']['note'] == 'work VPN, installed by IT', gv)
rc, mdv, _ = sh('audit.py', '--home', HOME, '--apps-dir', os.path.join(HOME, 'Applications'), '--no-sizes')
check('the report lists the acceptance that no longer applies', '## Acceptances that no longer apply (1)' in mdv, mdv[mdv.find('**Coverage'):][:600])
bi['casks'].pop()
json.dump(bi, open(os.path.join(ROOT, 'brew-info.json'), 'w'))
# a corrupt file: warn, treat as empty, never touch it
open(STATE_FILE, 'w').write('{ this is not json')
corrupt = open(STATE_FILE, 'rb').read()
rc, rep_c, raw_c = audit_json()
check('a corrupt state file is a warning, not a crash, and nothing counts as accepted', rc == 0 and 'could not read the accepted file' in rep_c['accepted_warning'] and rep_c['coverage']['accepted'] == 0, rep_c.get('accepted_warning'))
rc, out, err = acc('--key', K('app', 'Manual'), '--note', 'n')
check('accept refuses to write over a file it cannot read, and the file is untouched', rc != 0 and open(STATE_FILE, 'rb').read() == corrupt, (out, err))
write(STATE_FILE, json.dumps({'version': 1, 'accepted': {K('app', 'Manual'): {'note': 'kept', 'date': '2026-01-01', 'cls': 'unmanaged', 'name': 'Manual'}, 'bad': 'oops'}}))
recs, warn = accepted.load(STATE_FILE)
check('a malformed entry is ignored with a warning and the good one survives', list(recs) == [K('app', 'Manual')] and '1 malformed entry' in warn, (recs, warn))
write(STATE_FILE, json.dumps({'version': 1, 'accepted': {}}))
# the save is atomic: a crash while replacing leaves the old file whole
import accepted as acc_mod  # noqa: E402
good = open(STATE_FILE, 'rb').read()
real_replace, raised = os.replace, False


def boom(*a, **k):
    raise OSError('simulated crash')


os.replace = boom
try:
    acc_mod.save(STATE_FILE, {'x': {'note': 'n', 'cls': 'unmanaged'}})
except OSError:
    raised = True
finally:
    os.replace = real_replace
check('a crash while saving leaves the previous file whole and no temp file behind', raised and open(STATE_FILE, 'rb').read() == good and os.listdir(STATE) == ['accepted.json'], (raised, os.listdir(STATE)))
# hostile text is escaped or inert
HNOTE = '<img src=x onerror=alert(1)> & "quoted"'
rc, out, err = acc('--key', K('dotitem', HOSTILE), '--note', HNOTE)
check('a hostile folder name and note are stored as plain text', rc == 0 and accepted.load(STATE_FILE)[0][K('dotitem', HOSTILE)]['note'] == HNOTE and out.count('\n') < 12, (rc, out, err))
rc, rep_h, _ = audit_json()
check('a hostile note or folder name in the accepted state does not become live markup in the map', '<img src=x onerror' not in htmlmap.render(rep_h), '')
os.unlink(STATE_FILE)


# ---- fix: move orphan folders and broken links to the Trash, after a dry run, and nothing else
import contextlib, io, types  # noqa: E402
import fix  # noqa: E402

TRASH = os.path.join(ROOT, 'trash')
for rel in ('.vaadin/a', '.sts4/b', '.config/helix/c'):
    write(os.path.join(HOME, rel), 'data')
    age_tree(os.path.join(HOME, '.config/helix' if rel.startswith('.config') else rel.split('/')[0]), 200)
lbd = os.path.join(HOME, '.local/bin')
os.symlink(os.path.join(HOME, '.npm/_npx/gone2/bin/ghost2'), os.path.join(lbd, 'ghost2'))
os.symlink(os.path.join(HOME, '.npm/_npx/gone3/bin/x'), os.path.join(lbd, "it's gone"))
os.symlink(os.path.join(HOME, '.npm/_npx/gone4/bin/x'), os.path.join(lbd, 'bad\nname'))
rc, repf, _ = audit_json()
fa = os.path.join(ROOT, 'fix-audit.json')
json.dump(repf, open(fa, 'w'))
KF = lambda kind, name: by_name(repf, kind, name)['key']
VA, ST, HX, GH = KF('dotitem', '.vaadin'), KF('dotitem', '.sts4'), KF('dotitem', '.config/helix'), KF('broken-link', 'ghost2')
fx = lambda *a, **kw: sh('fix.py', fa, '--trash-dir', TRASH, *a, **kw)
PLAN = os.path.join(ROOT, 'plan.json')
check('the fixtures are orphan candidates for the audit', all(by_name(repf, k, n)['cls'] == 'orphaned' for k, n in (('dotitem', '.vaadin'), ('dotitem', '.sts4'), ('dotitem', '.config/helix'), ('broken-link', 'ghost2'))), '')

rc, out, err = fx('--key', VA, '--key', GH, '--plan', PLAN)
check('the dry run says nothing moved, shows the table and writes a plan', rc == 0 and 'nothing has been moved' in out and '| `' + VA + '`' in out and os.path.exists(PLAN), out + err)
check('and it really moved nothing, and did not even create the Trash folder', os.path.exists(os.path.join(HOME, '.vaadin')) and os.path.islink(os.path.join(lbd, 'ghost2')) and not os.path.exists(TRASH), '')
check('the plan file is private', oct(os.stat(PLAN).st_mode & 0o777) == '0o600', oct(os.stat(PLAN).st_mode))
for label, args in (('--execute without a plan', ['--execute']), ('--execute with keys instead of a plan', ['--execute', '--plan', PLAN, '--key', VA]), ('a missing plan file', ['--execute', '--plan', PLAN + '.nope'])):
    rc, out, err = fx(*args)
    check(f'{label} is refused and nothing moves', rc != 0 and os.path.exists(os.path.join(HOME, '.vaadin')) and not os.path.exists(TRASH), (out + err)[:200])
plan = json.load(open(PLAN))
bad = dict(plan, items=[dict(plan['items'][0], path=HOME + '/.ssh')])
json.dump(bad, open(PLAN + '.bad', 'w'))
rc, out, err = fx('--execute', '--plan', PLAN + '.bad')
check('a plan edited after the dry run is refused (its hash no longer matches), and nothing moves', 'Refused' in out and os.path.exists(os.path.join(HOME, '.ssh')) and os.path.exists(os.path.join(HOME, '.vaadin')), out)
old_plan = dict(plan, created=time.time() - 7200)
old_plan['sha256'] = fix.plan_hash(old_plan)
json.dump(old_plan, open(PLAN + '.old', 'w'))
rc, out, err = fx('--execute', '--plan', PLAN + '.old')
check('a valid but old plan is refused', 'older than 60 minutes' in out and os.path.exists(os.path.join(HOME, '.vaadin')), out)
rc, out, err = sh('fix.py', fa, '--trash-dir', os.path.join(ROOT, 'other-trash'), '--execute', '--plan', PLAN)
check('a plan made for another Trash folder is refused', 'different home folder or Trash' in out and os.path.exists(os.path.join(HOME, '.vaadin')), out)

rc, out, err = fx('--execute', '--plan', PLAN)
trashed = sorted(os.listdir(TRASH)) if os.path.isdir(TRASH) else []
check('executing the plan moves exactly the two approved items into the Trash, with timestamped names',
      rc == 0 and len(trashed) == 2 and any(t.startswith('.vaadin 20') for t in trashed) and any(t.startswith('ghost2 20') for t in trashed), (out, trashed))
check('the folder left home, arrived whole, and the link arrived as a link',
      not os.path.lexists(os.path.join(HOME, '.vaadin')) and open(os.path.join(TRASH, [t for t in trashed if t.startswith('.vaadin')][0], 'a')).read() == 'data'
      and os.path.islink(os.path.join(TRASH, [t for t in trashed if t.startswith('ghost2')][0])) and not os.path.lexists(os.path.join(lbd, 'ghost2')), trashed)
check('nothing else moved', os.path.exists(os.path.join(HOME, '.sts4')) and os.path.exists(os.path.join(HOME, '.config/helix')) and os.path.exists(os.path.join(HOME, '.terraform.d')), '')
lg = [json.loads(l) for l in open(os.path.join(STATE, 'fix-log.jsonl'))]
check('the log records both moves, privately', [e['status'] for e in lg] == ['moved', 'moved'] and oct(os.stat(os.path.join(STATE, 'fix-log.jsonl')).st_mode & 0o777) == '0o600', lg)
mvs = [shlex.split(l) for l in out.splitlines() if l.startswith('mv -- ')]
check('it prints one mv per item that puts it back, and says nothing was deleted', len(mvs) == 2 and 'Nothing was deleted' in out, out)
for _mv, _dd, dst, src in mvs:
    os.rename(dst, src)
check('running the printed mv puts everything back as it was', os.path.exists(os.path.join(HOME, '.vaadin/a')) and os.path.islink(os.path.join(lbd, 'ghost2')), '')
rc, out, err = fx('--execute', '--plan', PLAN)
check('a plan is used once: replaying it after the user put things back is refused, and nothing moves again',
      rc != 0 and 'already been carried out' in out and os.path.exists(os.path.join(HOME, '.vaadin/a')) and os.path.islink(os.path.join(lbd, 'ghost2')), out)

# held: each gate has its own reason, and nothing moves
def dry(*keys, **env_):
    rc, out, err = fx(*sum((['--key', k] for k in keys), []), '--plan', PLAN + '.t', **env_)
    return out + err


o = dry(KF('binary', 'manual-tool'))
check('a command that is not an orphan is not offered', 'it is unmanaged' in o and 'Nothing can be moved' in o, o)
o = dry(KF('mise-prunable', 'jq@1.0.0'))
check('a manager item is not movable here and points to tidy', 'not movable here' in o and '/dotfiles:tidy' in o, o)
o = dry('dotitem:/nowhere')
check('an unknown key is held', 'not in this audit' in o, o)
rc, _o, _e = sh('accept.py', fa, '--key', ST, '--note', 'keep for now')
o = dry(ST)
check('an accepted orphan is never moved', 'you accepted it' in o and 'Nothing can be moved' in o and os.path.exists(os.path.join(HOME, '.sts4')), o)
sh('accept.py', fa, '--forget', '--key', ST)
write(os.path.join(HOME, '.sts4/id_rsa'), 'not a real key', age_days=200)
os.utime(os.path.join(HOME, '.sts4'), (time.time() - 200 * DAY,) * 2)
o = dry(ST)
check('a folder holding a file named like a key is held, by name only', 'possible key material (id_rsa' in o and 'not a real key' not in o, o)
os.unlink(os.path.join(HOME, '.sts4/id_rsa'))
check('a name like tokenizer is not mistaken for a credential, and a .pem is', not fix.KEY_NAMES.match('tokenizer') and not fix.KEY_NAMES.match('environment.js') and fix.KEY_NAMES.match('x.pem')
      and fix.KEY_NAMES.match('.env.local') and fix.KEY_NAMES.match('id_ed25519') and fix.KEY_NAMES.match('token.json'), '')
os.utime(os.path.join(HOME, '.sts4'), (time.time() - 200 * DAY,) * 2)
o = dry(ST, FAKE_LSOF_HOLD='1')
check('a folder something has open is held', 'in use: process 4242' in o and 'Nothing can be moved' in o, o)
o = dry(ST, FAKE_LSOF_FAIL='1')
check('when lsof cannot answer the folder is held, never assumed free', 'could not tell whether it is in use' in o and 'Nothing can be moved' in o, o)
o = dry(ST)
check('with lsof saying nothing holds it, it is offered', 'Would move 1 item' in o, o)

# in-process: the path and device gates, in isolation from the audit's own re-check
real_recheck, real_which, real_ismount, real_dev = fix.gates.recheck, fix.shutil.which, os.path.ismount, fix.device_of
fix.gates.recheck = lambda *a, **k: None
ctx0 = {'home': HOME, 'trash': TRASH, 'refs': types.SimpleNamespace(unreadable=[], find_path=lambda rel: []), 'records': {}, 'active_days': 60}
mk = lambda path, kind='dotitem', key=None: {'key': key or kind + ':' + path, 'kind': kind, 'path': path, 'name': os.path.basename(path), 'cls': 'orphaned', 'evidence': ['x'], 'kb': 1}
outside = os.path.join(ROOT, 'outside-home')
write(os.path.join(outside, 'f'), 'x', age_days=200)
try:
    why = lambda i, c=None: fix.check_item(i, c or ctx0)
    check('the home folder itself is never moved', 'outside your home folder' in (why(mk(HOME)) or ''), why(mk(HOME)))
    check('something outside the home folder is held, with a pointer to tidy', 'outside your home folder' in (why(mk(outside)) or '') and '/dotfiles:tidy' in why(mk(outside)), why(mk(outside)))
    os.makedirs(os.path.join(HOME, '.Trash-test'), exist_ok=True)
    ctx_t = dict(ctx0, trash=os.path.join(HOME, '.Trash-test'))
    check('the Trash itself is never moved', 'Trash' in (why(mk(os.path.join(HOME, '.Trash-test')), ctx_t) or ''), why(mk(os.path.join(HOME, '.Trash-test')), ctx_t))
    os.path.ismount = lambda p: True
    check('a mount point is held', 'mount point' in (why(mk(os.path.join(HOME, '.config/helix'))) or ''), why(mk(os.path.join(HOME, '.config/helix'))))
    os.path.ismount = real_ismount
    fix.device_of = lambda p: -1
    check('an item on a different volume than the Trash is held: it would need a copy, which this tool never makes', 'different volume' in (why(mk(os.path.join(HOME, '.config/helix'))) or ''), why(mk(os.path.join(HOME, '.config/helix'))))
    fix.device_of = real_dev
    check('a name with a control character is held', 'control characters' in (why(mk(os.path.join(lbd, 'bad\nname'), 'broken-link')) or ''), '')
    fix.shutil.which = lambda n: None
    check('with no lsof at all a folder is held', 'lsof is not available' in (why(mk(os.path.join(HOME, '.config/helix'))) or ''), why(mk(os.path.join(HOME, '.config/helix'))))
    fix.shutil.which = real_which
    ctx_bad = dict(ctx0, refs=types.SimpleNamespace(unreadable=['~/.zshrc'], find_path=lambda rel: []))
    fix.gates.recheck = real_recheck
    check('when a config file cannot be read the folder is held', 'could not read' in (why(by_name(repf, 'dotitem', '.config/helix'), ctx_bad) or ''), '')
    fix.gates.recheck = lambda *a, **k: None
    if os.geteuid() != 0:
        os.makedirs(os.path.join(ROOT, 'ro'), exist_ok=True)
        write(os.path.join(ROOT, 'ro/f'), 'x')
        ctx_ro = dict(ctx0, home=ROOT)
        os.chmod(os.path.join(ROOT, 'ro'), 0o500)
        check('an item in a folder you cannot write to is held', 'not writable' in (why(mk(os.path.join(ROOT, 'ro/f')), ctx_ro) or ''), why(mk(os.path.join(ROOT, 'ro/f')), ctx_ro))
        os.chmod(os.path.join(ROOT, 'ro'), 0o700)
        os.makedirs(os.path.join(ROOT, 'rotrash'), exist_ok=True)
        os.chmod(os.path.join(ROOT, 'rotrash'), 0o500)
        check('a Trash folder you cannot write to is held, with the Full Disk Access hint', 'Full Disk Access' in (why(mk(os.path.join(HOME, '.config/helix')), dict(ctx0, trash=os.path.join(ROOT, 'rotrash'))) or ''), '')
        os.chmod(os.path.join(ROOT, 'rotrash'), 0o700)
finally:
    fix.gates.recheck, fix.shutil.which = real_recheck, real_which
    os.path.ismount = real_ismount
    fix.device_of = real_dev
p1 = {'dev': 1, 'ino': 2, 'islink': False, 'link': None, 'real_parent': '/a', 'newest': 100}
check('drift is detected for each thing that identifies the item, and for a newer write', all(fix.drifted(p1, dict(p1, **{k: v})) for k, v in (('dev', 9), ('ino', 9), ('islink', True), ('link', 'x'), ('real_parent', '/b'), ('newest', 101)))
      and fix.drifted(p1, dict(p1)) is None, '')

# at execute time: changed since the plan
rc, out, err = fx('--key', ST, '--key', HX, '--plan', PLAN)
assert rc == 0, out
shutil.rmtree(os.path.join(HOME, '.sts4'))
write(os.path.join(HOME, '.sts4/b'), 'data')
age_tree(os.path.join(HOME, '.sts4'), 200)
write(os.path.join(HOME, '.config/helix/new-file'), 'written after the plan')
rc, out, err = fx('--execute', '--plan', PLAN)
check('an item replaced after the plan (a new folder under the same name; some filesystems reuse the inode, so only the prefix of the reason is pinned) is held as changed, and one written to after the plan is held too, and nothing moves',
      'changed since the plan was made (' in out and 'wrote to it within' in out and os.path.exists(os.path.join(HOME, '.sts4')) and os.path.exists(os.path.join(HOME, '.config/helix')) and rc != 0, out)
os.unlink(os.path.join(HOME, '.config/helix/new-file'))
age_tree(os.path.join(HOME, '.config/helix'), 200)
zsh0 = open(os.path.join(HOME, '.zshrc')).read()
rc, out, err = fx('--key', HX, '--plan', PLAN)
write(os.path.join(HOME, '.zshrc'), zsh0 + 'export P="$HOME/.config/helix/bin:$P"\n')
rc, out, err = fx('--execute', '--plan', PLAN)
check('a config file that starts mentioning the folder after the plan holds it at execute time, and the file is not echoed', 'config files now mention it' in out and SENTINEL not in out and os.path.exists(os.path.join(HOME, '.config/helix')), out)
write(os.path.join(HOME, '.zshrc'), zsh0)

# partial: one fails (permission), the other still moves, and nothing is deleted
rc, out, err = fx('--key', ST, '--key', HX, '--plan', PLAN)
os.environ['DOTFILES_STATE'] = STATE
os.environ['HOME'] = HOME
real_rename = os.rename
def picky(src, dst):
    if src.endswith('/.sts4'):
        raise PermissionError(1, 'Operation not permitted')
    return real_rename(src, dst)
os.rename = picky
buf = io.StringIO()
try:
    ctx1 = dict(ctx0, refs=__import__('refs').Refs(HOME))
    with contextlib.redirect_stdout(buf):
        code = fix.execute(types.SimpleNamespace(plan=PLAN), {i['key']: i for i in repf['items']}, ctx1)
finally:
    os.rename = real_rename
txt = buf.getvalue()
check('a refused move is FAILED with the Full Disk Access hint and nothing deleted, while the other item still moves',
      code == 1 and 'FAILED' in txt and 'Full Disk Access' in txt and 'nothing was deleted' in txt and os.path.exists(os.path.join(HOME, '.sts4')) and not os.path.lexists(os.path.join(HOME, '.config/helix')), txt)
for t_ in os.listdir(TRASH):
    if t_.startswith('helix'):
        os.rename(os.path.join(TRASH, t_), os.path.join(HOME, '.config/helix'))
# quoting: a name with a quote and a space round-trips through shlex, and a name with a newline is held
rcq, repq, _ = audit_json()
fq = os.path.join(ROOT, 'fix-audit-q.json')
json.dump(repq, open(fq, 'w'))
QK = by_name(repq, 'broken-link', "it's gone")['key']
rc, out, err = sh('fix.py', fq, '--trash-dir', TRASH, '--key', QK, '--plan', PLAN)
rc, out, err = sh('fix.py', fq, '--trash-dir', TRASH, '--execute', '--plan', PLAN)
mvq = [shlex.split(l) for l in out.splitlines() if l.startswith('mv -- ')]
check('the printed restore command survives a quote and a space in the name', len(mvq) == 1 and mvq[0][3] == os.path.join(lbd, "it's gone") and mvq[0][2].startswith(TRASH), (out, mvq))
os.rename(mvq[0][2], mvq[0][3])
NL = by_name(repq, 'broken-link', 'bad\nname')
rc, out, err = sh('fix.py', fq, '--trash-dir', TRASH, '--key', NL['key'], '--plan', PLAN)
check('a name with a newline in it is held', 'control characters' in out and os.path.lexists(NL['path']), out)
# platform: no default Trash off macOS
real_platform, real_argv = sys.platform, sys.argv
sys.platform, sys.argv = 'linux', ['fix.py', fa, '--key', ST]
try:
    fix.main()
    refused = ''
except SystemExit as e_:
    refused = str(e_)
finally:
    sys.platform, sys.argv = real_platform, real_argv
check('off macOS the tool refuses unless a Trash folder is named', '--trash-dir' in refused, refused)
fsrc = open(os.path.join(SCRIPTS, 'fix.py')).read()
bad_calls = [b for b in ('rmtree', 'os.remove', 'os.unlink', '.unlink(', 'rmdir', 'shutil.move', 'sudo', '--force', 'rm -', 'shutil.copy') if b in fsrc]
check('the executor contains no deletion, copy, elevation or force: its only mutation is a rename into the Trash', not bad_calls and fsrc.count('os.rename(') == 1, bad_calls)
check('the real Trash was never touched by these tests', not os.path.exists(os.path.join(HOME, '.Trash')), '')
rc, out_t, _ = sh('tidy.py', fa, '--key', VA, '--key', HX)
check('tidy is unchanged by the shared checks: it still prints Trash moves for orphans', out_t.count('mv -- ') == 2 and 'Cleanup commands (not run)' in out_t, out_t)

# ---- tidy prints, re-checks, and refuses
scan = os.path.join(ROOT, 'audit.json')
json.dump(rep, open(scan, 'w'))
keys = [g('dotitem', '.terraform.d')['key'], g('broken-link', 'ghost')['key'], g('brew-orphan', 'oldlib')['key'], g('mise-prunable', 'jq@1.0.0')['key']]
rc, out, err = sh('tidy.py', scan, *sum((['--key', k] for k in keys), []))
check('tidy prints a Trash move for an orphan folder and for a broken link', rc == 0 and out.count('mv -- ') == 2 and '.Trash/' in out, out)
check('tidy prints the managers\' own commands, dry run first', out.index('brew autoremove --dry-run') < out.index('brew autoremove   #') and 'mise prune --dry-run jq' in out, out)
check('tidy puts a snapshot command before any manager removal', out.index('brew bundle dump') < out.index('brew autoremove') and 'mise ls --json >' in out, out)
check('tidy never prints a forbidden flag', not any(f in out for f in ('--zap', '--force', '--ignore-dependencies', 'rm -rf', 'sudo')), out)
check('tidy runs nothing: the folder is still there', os.path.exists(os.path.join(HOME, '.terraform.d')))
rc, out, err = sh('tidy.py', scan, '--key', g('binary', 'manual-tool')['key'], '--key', g('dotitem', '.vfox')['key'], '--key', g('dotitem', '.ssh')['key'], '--key', 'nonsense')
check('tidy refuses unmanaged, referenced, config and unknown keys', rc == 0 and 'mv -- ' not in out and out.count('not offered') >= 3 and 'not in this audit' in out, out)
write(os.path.join(HOME, '.terraform.d/new-file'), 'written after the audit')
rc, out, err = sh('tidy.py', scan, '--key', g('dotitem', '.terraform.d')['key'])
check('tidy holds an orphan that was written to after the audit', 'mv -- ' not in out and 'held' in out, out)
age_tree(os.path.join(HOME, '.terraform.d'), 200)
write(os.path.join(HOME, '.zshrc'), f'export PATH="$HOME/.terraform.d/bin:$PATH"\nexport TOKEN={SENTINEL}\n')
rc, out, err = sh('tidy.py', scan, '--key', g('dotitem', '.terraform.d')['key'])
check('tidy holds an orphan that a config file now mentions, and does not echo the file', 'mv -- ' not in out and '~/.zshrc' in out and SENTINEL not in out, out)

shutil.rmtree(ROOT, ignore_errors=True)
print()
print('FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS')
sys.exit(1 if FAILS else 0)
