#!/usr/bin/env python3
"""Tests for audit.py and tidy.py against a fake home folder and fake package managers.

The fake managers sit first on PATH and log every command they receive, so "the audit only ran read-only commands" is
checked from the outside, not just by the runner's own allow-list. A secret planted in files the audit must never print
is searched for in every output.

    python3 -B tests/test_audit.py
"""
import json, os, plistlib, shutil, stat, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
from common import ALLOWED, age_text, birth  # noqa: E402

ROOT = os.path.realpath(tempfile.mkdtemp(prefix='dotfiles-test-'))
HOME = os.path.join(ROOT, 'home')
FAKEBIN = os.path.join(ROOT, 'fakebin')
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
    fake('brew', f'''case "$1" in
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
check('an app with no match has no suggestions', g('app', 'Manual')['suggestions'] == [], g('app', 'Manual'))

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
check('every command the fake managers received is on the allow-list', logged and all(l in ok_cmds for l in logged), [l for l in logged if l not in ok_cmds])
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

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs += [(tag, k, v or '') for k, v in attrs]
        self._in_style = tag == 'style'

    def handle_endtag(self, tag):
        self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.style += data


ins = Inspect()
ins.feed(doc)
bad_tags = ins.tags & {'script', 'link', 'img', 'iframe', 'object', 'embed', 'base', 'form', 'input'}
bad_attrs = [(t_, k) for t_, k, v in ins.attrs if k in ('src', 'href', 'srcset', 'action') or k.startswith('on') or 'http' in v.lower()]
check('the page is self-contained: no scripts, links, images or external resources',
      not bad_tags and not bad_attrs and 'url(' not in ins.style and '@import' not in ins.style, (bad_tags, bad_attrs))
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

sizes_in = [(10, 'a'), (6, 'b'), (4, 'c'), (2, 'd'), (1, 'e')]
rects = htmlmap.squarify([s for s, _ in sizes_in], 0, 0, 100, 100)
area = [w * h for (_x, _y, w, h) in rects]
check('treemap rects fill the box and stay inside it', abs(sum(area) - 10000) < 1e-6 and all(x >= -1e-9 and y >= -1e-9 and x + w <= 100 + 1e-9 and y + h <= 100 + 1e-9 for (x, y, w, h) in rects), rects)
check('treemap areas are proportional to sizes', all(abs(a / 10000 - s / 23) < 1e-9 for a, (s, _) in zip(area, sizes_in)), area)

# ---- duplicates: the same tool through two managers is a finding; a system copy is a note; versions under one manager are a line
import audit  # noqa: E402
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
import common  # noqa: E402
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
