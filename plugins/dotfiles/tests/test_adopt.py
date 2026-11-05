#!/usr/bin/env python3
"""Tests for adopt.py: the one tool that installs software, against a fake home folder and fake brew, mise, ps and lsof.

The fakes sit first on PATH and log every command they receive, so "it ran only the commands on its allow-list, never sudo or
--force" is checked from the outside, not just by the tool's own allow-list.

    python3 -B tests/test_adopt.py
"""
import json, os, plistlib, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
import adopt, adoptgates, common  # noqa: E402

ROOT = os.path.realpath(tempfile.mkdtemp(prefix='dotfiles-adopt-test-'))
HOME, FAKEBIN, STATE, OUTSIDE = (os.path.join(ROOT, d) for d in ('home', 'fakebin', 'state', 'outside'))
TRASH, APPS, LOG = os.path.join(HOME, '.Trash'), os.path.join(HOME, 'Applications'), os.path.join(ROOT, 'cmd.log')
FAILS = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {str(detail)[:400]}'))
    if not cond:
        FAILS.append(name)


def put(path, text='', mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def make_app(name, short, build='1', real=True):
    p = os.path.join(APPS, name)
    os.makedirs(os.path.join(p, 'Contents'))
    with open(os.path.join(p, 'Contents', 'Info.plist'), 'wb') as f:
        plistlib.dump({'CFBundleShortVersionString': short, 'CFBundleVersion': build, 'SECRET': 'SENTINEL-PLIST-9d2'}, f)
    return p


def cask(token, version, artifacts, depends=None, **extra):
    put(os.path.join(ROOT, 'casks', token + '.json'), json.dumps({'casks': [dict({'token': token, 'version': version, 'artifacts': artifacts, 'depends_on': depends or {}}, **extra)]}))


def fake(name, body):
    put(os.path.join(FAKEBIN, name), '#!/bin/bash\necho "' + name + ' $*" >> "' + LOG + '"\n' + body, 0o755)


def world():
    shutil.rmtree(ROOT, ignore_errors=True)
    for d in (HOME, FAKEBIN, STATE, OUTSIDE, TRASH, APPS, os.path.join(HOME, '.local/bin'), os.path.join(HOME, '.config/mise')):
        os.makedirs(d)
    put(os.path.join(HOME, '.config/mise/config.toml'), '[tools]\nnode = "22"\n')
    put(os.path.join(ROOT, 'installed.json'), json.dumps({'casks': [], 'formulae': []}))
    put(os.path.join(ROOT, 'mise-ls.json'), json.dumps({}))
    put(os.path.join(ROOT, 'ps.txt'), '/usr/bin/some-daemon\n')
    put(os.path.join(ROOT, 'registry'), 'oras                          aqua:oras-project/oras asdf:bodgit/asdf-oras\n'
        'tool2                         aqua:acme/tool2\nweird                         asdf:acme/weird aqua:acme/weird\nnover                         aqua:acme/nover\n'
        'badver                        aqua:acme/badver\nghost                         aqua:acme/ghost\nnode                          core:node\n')
    fake('brew', f'''case "$1 $2 $3" in
  "info --cask --json=v2") f="{ROOT}/casks/$4.json"; [ -f "$f" ] && cat "$f" || {{ echo "Error: No cask $4" >&2; exit 1; }};;
  "info --json=v2 --installed") cat "{ROOT}/installed.json";;
  "install --cask --adopt") [ -n "$FAKE_BREW_FAIL" ] && {{ echo "Error: existing app is different" >&2; exit 1; }}
        python3 -c "import json;p='{ROOT}/installed.json';d=json.load(open(p));d['casks'].append({{'token':'$4'}});json.dump(d,open(p,'w'))";;
  "install --cask "*) [ -n "$FAKE_BREW_FAIL" ] && {{ echo "Error: download failed" >&2; exit 1; }}
        echo "apps-at-install: $(ls "{APPS}" | tr '\\n' ' ')" >> "{LOG}"
        python3 -c "import json;p='{ROOT}/installed.json';d=json.load(open(p));d['casks'].append({{'token':'$3'}});json.dump(d,open(p,'w'))";;
  *) exit 99;;
esac''')
    fake('mise', f'''case "$1 $2" in
  "registry ") cat "{ROOT}/registry";;
  "ls --json") cat "{ROOT}/mise-ls.json";;
  "config ls") echo "~/.config/mise/config.toml      node";;
  "latest "*) [ -n "$FAKE_LATEST" ] && echo "$FAKE_LATEST" || {{ [ "$2" = nover ] && exit 1; [ "$2" = badver ] && echo "latest-ish" || echo "1.3.0"; }};;
  "use -g") [ -n "$FAKE_MISE_FAIL" ] && {{ echo "mise: download failed" >&2; exit 1; }}
        [ -n "$FAKE_MISE_NODETECT" ] && exit 0
        python3 - "$3" <<'EOF'
import json,os,sys
spec=sys.argv[1]; tool,ver=spec.rsplit('@',1); name=tool.split(':')[-1].split('/')[-1]
base="{ROOT}/misei/"+name+"/"+ver
os.makedirs(base+"/bin",exist_ok=True); open(base+"/bin/"+name,"w").write("x")
p="{ROOT}/mise-ls.json"; d=json.load(open(p)); d[tool]=[{{"version":ver,"install_path":base,"installed":True,"active":True}}]; json.dump(d,open(p,"w"))
EOF
        ;;
  *) exit 99;;
esac''')
    fake('ps', f'[ -n "$FAKE_PS_FAIL" ] && exit 1; cat "{ROOT}/ps.txt"')
    put(os.path.join(FAKEBIN, 'lsof'), '#!/bin/bash\n[ -n "$FAKE_LSOF_HOLD" ] && { echo 4242; exit 0; }\nexit 1\n', 0o755)


def item(kind, name, path, cls='unmanaged', suggestions=(), **kw):
    return dict({'key': f'{kind}:{path}', 'kind': kind, 'name': name, 'path': path, 'cls': cls, 'evidence': ['x'], 'kb': 1, 'suggestions': list(suggestions),
                 'accepted': None}, **kw)


def csug(token):
    return {'manager': 'brew', 'candidate': token, 'command': f'brew install --cask {token}'}


def msug(backend):
    return {'manager': 'mise', 'candidate': backend, 'command': f'mise use -g {backend}'}


def audit_file(items, brew='ok', mise='ok'):
    p = os.path.join(ROOT, 'audit.json')
    json.dump({'home': HOME, 'items': items, 'adapters': [{'name': 'brew', 'status': brew}, {'name': 'mise', 'status': mise}]}, open(p, 'w'))
    return p


def run(*args, **env):
    e = dict(os.environ, PATH=FAKEBIN + os.pathsep + os.environ['PATH'], HOME=HOME, DOTFILES_STATE=STATE, **env)
    for k in ('XDG_CONFIG_HOME', 'MISE_GLOBAL_CONFIG_FILE'):
        e.pop(k, None) if k not in env else None
    p = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'adopt.py'), *args, '--trash-dir', TRASH], capture_output=True, text=True, env=e, timeout=120)
    return p.returncode, p.stdout, p.stderr


def cmds():
    try:
        return [l.strip() for l in open(LOG)]
    except OSError:
        return []


def standard():
    """Firefox (replace), Same (adopt), oras in home (mise), tool2 outside home (mise), and the items that must be held."""
    world()
    ff, same = make_app('Firefox.app', '152.0.6'), make_app('Same.app', '2.0')
    cask('firefox', '159.0.1', [{'uninstall': [{}]}, {'app': ['Firefox.app']}, {'zap': [{}]}], {'macos': {}})
    cask('same', '2.0', [{'app': ['Same.app']}])
    put(os.path.join(HOME, '.local/bin/oras'), 'x', 0o755)
    put(os.path.join(OUTSIDE, 'bin/tool2'), 'x', 0o755)
    its = [item('app', 'Firefox', ff, suggestions=[csug('firefox')]), item('app', 'Same', same, suggestions=[csug('same')]),
           item('binary', 'oras', os.path.join(HOME, '.local/bin/oras'), suggestions=[msug('aqua:oras-project/oras')]),
           item('binary', 'tool2', os.path.join(OUTSIDE, 'bin/tool2'), suggestions=[msug('aqua:acme/tool2')])]
    return its


def keys(its, *names):
    return [x for i in its if i['name'] in names for x in ('--key', i['key'])]


def held_reason(its, name, **env):
    """The reason a dry run gives for holding the named item ('' if it was planned)."""
    a = audit_file(its)
    rc, out, err = run(a, *keys(its, name), '--plan', os.path.join(ROOT, 'plan.json'), **env)
    line = [l for l in out.splitlines() if l.startswith('- `')]
    return line[0] if line else ('' if rc == 0 else out + err)


# ---------------------------------------------------------------- the allow-lists and the audit's read-only promise
check('the install forms are exact: adopt, plain cask, mise use with a pinned version',
      all(adopt.allowed_write(a) for a in (['brew', 'install', '--cask', '--adopt', 'firefox'], ['brew', 'install', '--cask', 'firefox'],
                                          ['mise', 'use', '-g', 'aqua:oras-project/oras@1.3.0'], ['mise', 'use', '-g', 'node@22.1.0'])), '')
check('everything else is refused: sudo, --force, flags, unpinned or hostile names, other verbs',
      not any(adopt.allowed_write(a) for a in (['sudo', 'brew', 'install', '--cask', 'x'], ['brew', 'install', '--cask', '--force', 'x'], ['brew', 'install', '--cask', '--adopt', '-x'],
                                              ['brew', 'install', '--cask', 'a;b'], ['brew', 'install', '--cask', 'A'], ['brew', 'install', 'firefox'], ['brew', 'uninstall', '--cask', 'x'],
                                              ['mise', 'install', 'oras'], ['mise', 'use', '-g', 'oras@latest'], ['mise', 'use', 'oras@1.0'], ['mise', 'use', '-g', 'asdf:x/y@1.0'],
                                              ['mise', 'use', '-g', 'oras;rm@1.0'], ['mise', 'use', '-g', 'oras@1.0', '--force'], ['rm', '-rf', '/'])), '')
check('reads are exact too', adopt.allowed_read(['mise', 'latest', 'oras']) and not adopt.allowed_read(['mise', 'latest', '-x']) and not adopt.allowed_read(['brew', 'install', 'x'])
      and not adopt.allowed_read(['brew', 'info', '--cask', '--json=v2', 'a b']), '')
check('the audit stays read-only: no install form is on common.ALLOWED, and the audit modules never import adopt',
      not any(common.allowed(a) for a in (['brew', 'install', '--cask', 'firefox'], ['mise', 'use', '-g', 'node@22.1.0'], ['brew', 'install', '--cask', '--adopt', 'x'], ['mise', 'latest', 'oras']))
      and not any('adopt' in open(os.path.join(SCRIPTS, f)).read().lower().replace('adoption', '') for f in ('audit.py', 'common.py', 'adapters.py', 'checker.py')), '')
try:
    adopt.write(['sudo', 'brew', 'install', '--cask', 'x'])
    check('write() raises for a command off the list', False, '')
except ValueError:
    check('write() raises for a command off the list', True)

# ---------------------------------------------------------------- the dry run
its = standard()
a = audit_file(its)
rc, out, err = run(a, *keys(its, 'Firefox', 'Same', 'oras'), '--plan', os.path.join(ROOT, 'plan.json'))
log = cmds()
check('a dry run succeeds, writes a private plan, and runs only reads', rc == 0 and os.path.exists(os.path.join(ROOT, 'plan.json')) and oct(os.stat(os.path.join(ROOT, 'plan.json')).st_mode & 0o777) == '0o600'
      and not any(' install ' in l or l.startswith('mise use') for l in log), (rc, err, log))
check('it names how each is done: replace for a different version, adopt for the same, mise for a command',
      'replace (old app to the Trash first)' in out and 'adopt in place' in out and 'mise use -g aqua:oras-project/oras@1.3.0' in out and '(installed: 152.0.6)' in out, out)
check('it states the limits before the user confirms', 'no one-line undo' in out and 'downloads and installs software' in out, out)
check('no plist content beyond two version strings reaches the output', 'SENTINEL-PLIST-9d2' not in out + err, '')
check('nothing was installed or moved by the dry run', os.path.exists(its[0]['path']) and os.listdir(TRASH) == [] and json.load(open(os.path.join(ROOT, 'installed.json')))['casks'] == [], '')

# ---------------------------------------------------------------- the gates (each is a hold with a reason)
S = standard()
cask('codex-like', '1.0', [{'app': ['ChatGPT.app']}])
codex = item('app', 'Codex', make_app('Codex.app', '1.0'), suggestions=[csug('codex-like')])
cask('pkgy', '1.0', [{'pkg': ['x.pkg']}, {'zap': [{}]}])
pkgy = item('app', 'Pkgy', make_app('Pkgy.app', '1.0'), suggestions=[csug('pkgy')])
cask('depy', '1.0', [{'app': ['Depy.app']}], {'formula': ['openssl']})
depy = item('app', 'Depy', make_app('Depy.app', '1.0'), suggestions=[csug('depy')])
cask('scripty', '1.0', [{'app': ['Scripty.app']}, {'postflight': [{}]}])
scripty = item('app', 'Scripty', make_app('Scripty.app', '1.0'), suggestions=[csug('scripty')])
cask('moved', '1.0', [{'app': ['Orig.app', {'target': 'Renamed.app'}]}])
renamed = item('app', 'Renamed', make_app('Renamed.app', '1.0'), suggestions=[csug('moved')])
cask('gone', '1.0', [{'app': ['Gone.app']}], deprecated=True)
gone = item('app', 'Gone', make_app('Gone.app', '1.0'), suggestions=[csug('gone')])
cask('busy', '1.0', [{'app': ['Busy.app']}])
busy = item('app', 'Busy', make_app('Busy.app', '1.0'), suggestions=[csug('busy')])
put(os.path.join(ROOT, 'ps.txt'), f"{busy['path']}/Contents/MacOS/Busy\n")
cask('lnk', '1.0', [{'app': ['Lnk.app']}])
os.symlink(S[0]['path'], os.path.join(APPS, 'Lnk.app'))
lnk = item('app', 'Lnk', os.path.join(APPS, 'Lnk.app'), suggestions=[csug('lnk')])
cask('acc', '1.0', [{'app': ['Acc.app']}])
acc = item('app', 'Acc', make_app('Acc.app', '1.0'), suggestions=[csug('acc')], accepted={'cls': 'unmanaged', 'note': 'keep', 'date': '2026-01-01'})
mgd = item('app', 'Mgd', make_app('Mgd.app', '1.0'), cls='managed', suggestions=[csug('acc')])
cask('already', '1.0', [{'app': ['Already.app']}])
put(os.path.join(ROOT, 'installed.json'), json.dumps({'casks': [{'token': 'already'}], 'formulae': []}))
already = item('app', 'Already', make_app('Already.app', '1.0'), suggestions=[csug('already')])
nosug = item('app', 'Nosug', make_app('Nosug.app', '1.0'))
weird = item('binary', 'weird', os.path.join(HOME, '.local/bin/weird'), suggestions=[msug('asdf:acme/weird')])
weird2 = item('binary', 'weird2', os.path.join(OUTSIDE, 'bin/weird'), suggestions=[msug('aqua:acme/weird')])
stale = item('binary', 'tool2stale', os.path.join(OUTSIDE, 'bin/tool2'), suggestions=[msug('aqua:acme/other')])
nover = item('binary', 'nover', os.path.join(HOME, '.local/bin/nover'), suggestions=[msug('aqua:acme/nover')])
badver = item('binary', 'badver', os.path.join(HOME, '.local/bin/badver'), suggestions=[msug('aqua:acme/badver')])
dot = item('dotitem', '.x', os.path.join(HOME, '.x'))
missing = item('binary', 'ghost', os.path.join(HOME, '.local/bin/ghost'), suggestions=[msug('aqua:acme/ghost')])
for n in (codex, pkgy, depy, scripty, renamed, gone, busy, lnk, acc, mgd, already, nosug, weird, weird2, stale, nover, badver, dot, missing):
    S.append(n)
H = lambda name, **env: held_reason(S, name, **env)
check('a cask whose app has another name is held (a name match is not the same product)', 'ChatGPT.app' in H('Codex') and 'not the same product' in H('Codex'), H('Codex'))
check('a cask that runs an installer (pkg) is held', 'pkg' in H('Pkgy') and 'installer or script' in H('Pkgy'), H('Pkgy'))
check('a cask with a postflight script is held', 'postflight' in H('Scripty'), H('Scripty'))
check('a cask that depends on a formula is held', 'depends on other software' in H('Depy') and 'formula' in H('Depy'), H('Depy'))
check('a cask whose app is renamed on install (target form) matches by the installed name', H('Renamed') == '', H('Renamed'))
check('a deprecated cask is held', 'deprecated' in H('Gone'), H('Gone'))
check('a running app is held', 'running' in H('Busy'), H('Busy'))
if os.geteuid() != 0:
    cask('prot', '1.0', [{'app': ['Prot.app']}])
    prot = item('app', 'Prot', make_app('Prot.app', '0.5'), suggestions=[csug('prot')])
    os.chmod(prot['path'], 0o555)
    S.append(prot)
    check('an app this terminal may not modify (App Management, another owner) is held with that reason, not run as admin',
          'not allowed to modify it' in H('Prot') and 'never uses admin rights' in H('Prot'), H('Prot'))
    os.chmod(prot['path'], 0o755)
check('an unreadable process list is a hold, not a pass', 'could not tell whether it is running' in H('Firefox', FAKE_PS_FAIL='1'), H('Firefox', FAKE_PS_FAIL='1'))
check('a link is not replaced', 'not a real folder' in H('Lnk'), H('Lnk'))
check('an accepted item is held until the acceptance is forgotten', 'you accepted it' in H('Acc'), H('Acc'))
check('a managed item is not adopted', 'not unmanaged' in H('Mgd'), H('Mgd'))
check('a cask Homebrew already has is held', 'already has cask' in H('Already'), H('Already'))
check('an item with no cask suggestion is held', 'no Homebrew cask' in H('Nosug'), H('Nosug'))
check('a command whose first backend is asdf (plugin scripts) is held', 'not aqua or core' in H('weird'), H('weird'))
check('and so is one the audit suggested aqua for, when the registry now prefers asdf', 'now lists `asdf:acme/weird`' in H('weird2'), H('weird2'))
check('a command whose registry entry changed since the audit is held', 'out of date' in H('tool2stale'), H('tool2stale'))
check('a command with no version from mise is held', 'no plain version' in H('nover'), H('nover'))
check('a version that is not plain digits is held', 'no plain version' in H('badver'), H('badver'))
check('an item whose file is gone is held, not a crash', 'could not be examined' in H('ghost'), H('ghost'))
check('a dotitem is not adopted', 'not adopted' in H('.x'), H('.x'))
a = audit_file(S, brew='failed')
rc, out, err = run(a, *keys(S, 'Firefox'), '--plan', os.path.join(ROOT, 'p2.json'))
check('an app is held when brew could not be read in the audit', rc == 1 and 'could not be read in the audit' in out, out)
rc, out, err = run(audit_file(S), '--key', 'app:/nowhere\nrm -rf', '--plan', os.path.join(ROOT, 'p3.json'))
check('a key that is not in the audit is held, never interpreted', rc == 1 and 'not in this audit' in out, out)
# mise global config must be the one mise lists
fake('mise', open(os.path.join(FAKEBIN, 'mise')).read().split('\n', 2)[2].replace('echo "~/.config/mise/config.toml      node"', 'echo "~/elsewhere/config.toml      node"'))
check('a command is held when mise does not list the global config it would change', 'not clear which file' in H('oras'), H('oras'))

# ---------------------------------------------------------------- the effects file from the mise MCP can only add holds
its = standard()
a = audit_file(its)
eff = os.path.join(ROOT, 'effects.json')
for label, body, held in (('use declared write', {'commands': [{'command': 'use', 'effect': 'write'}]}, False),
                          ('use declared destructive', {'commands': [{'command': 'use', 'effect': 'destructive'}]}, True),
                          ('use unclassified', {'commands': [{'command': 'use', 'effect': None}]}, True),
                          ('use missing from the list', {'commands': [{'command': 'ls', 'effect': 'read'}]}, True),
                          ('a malformed file', {'nope': 1}, True)):
    json.dump(body, open(eff, 'w'))
    rc, out, err = run(a, *keys(its, 'oras'), '--plan', os.path.join(ROOT, 'pe.json'), '--mise-effects', eff)
    check(f'effects file, {label}: ' + ('held' if held else 'proceeds'), (rc == 1 and 'mise' in out and 'Held' in out) if held else rc == 0, out)
rc, out, err = run(a, *keys(its, 'Firefox'), '--plan', os.path.join(ROOT, 'pe2.json'), '--mise-effects', eff)
check('the effects file only affects mise items, and can never grant what a gate refused', rc == 0, out)
json.dump({'commands': [{'command': 'use', 'effect': 'write'}]}, open(eff, 'w'))
S2 = standard()
S2.append(item('binary', 'weird', os.path.join(HOME, '.local/bin/weird'), suggestions=[msug('asdf:acme/weird')]))
rc, out, err = run(audit_file(S2), *keys(S2, 'weird'), '--plan', os.path.join(ROOT, 'pe3.json'), '--mise-effects', eff)
check('a write-declared effects file does not release a held backend', rc == 1, out)

# ---------------------------------------------------------------- execute: exactly what the plan said, nothing else
its = standard()
a = audit_file(its)
plan = os.path.join(ROOT, 'plan.json')
run(a, *keys(its, 'Firefox', 'Same', 'oras', 'tool2'), '--plan', plan)
open(LOG, 'w').close()
rc, out, err = run(a, '--plan', plan, '--execute')
log = cmds()
installs = [l for l in log if l.startswith(('brew install', 'mise use'))]
check('execute runs exactly the planned installs, in order, and no other write',
      installs == ['brew install --cask firefox', 'brew install --cask --adopt same', 'mise use -g aqua:oras-project/oras@1.3.0', 'mise use -g aqua:acme/tool2@1.3.0'], installs)
check('nothing ran with sudo or --force', not any('sudo' in l or '--force' in l for l in log), log)
check('a replaced app was out of the way when the cask installed, and sits in the Trash', any('apps-at-install:' in l and 'Firefox.app' not in l and 'Same.app' in l for l in log)
      and any(n.startswith('Firefox.app ') for n in os.listdir(TRASH)) and not os.path.exists(its[0]['path']), (log, os.listdir(TRASH)))
check('an adopted app was not moved', os.path.exists(its[1]['path']), '')
check('the mise config was backed up before the first mise use', any(n.startswith('mise-config.bak-') for n in os.listdir(STATE)), os.listdir(STATE))
claims = [json.loads(l) for l in open(os.path.join(STATE, 'adopt-log.jsonl'))]
check('all four are adopted, logged, and the report says so', rc == 0 and [c['status'] for c in claims] == ['adopted'] * 4 and '# Adopted 4 item(s)' in out, (rc, out, claims))
check('the report gives the undo, and says the old command outside home needs admin and is not run',
      'brew uninstall --cask firefox' in out and 'mise uninstall oras@1.3.0' in out and 'sudo mv' in out and os.path.exists(its[3]['path']), out)
rc2, out2, _ = run(a, '--plan', plan, '--execute')
check('a plan is used once', rc2 == 1 and 'already been carried out' in out2 and len([l for l in cmds() if l.startswith('brew install')]) == 2, out2)

# ---------------------------------------------------------------- failures leave things as they were
its = standard()
a = audit_file(its)
run(a, *keys(its, 'Firefox'), '--plan', plan)
rc, out, err = run(a, '--plan', plan, '--execute', FAKE_BREW_FAIL='1')
check('a failed replace puts the old app back and reports it', rc == 1 and os.path.exists(its[0]['path']) and not [n for n in os.listdir(TRASH) if n.startswith('Firefox')]
      and 'put back where it was' in out and 'FAILED' in out, (rc, out, os.listdir(TRASH)))
its = standard()
a = audit_file(its)
run(a, *keys(its, 'Same'), '--plan', plan)
rc, out, err = run(a, '--plan', plan, '--execute', FAKE_BREW_FAIL='1')
check('a failed adopt changes nothing and the item stays unmanaged', rc == 1 and os.path.exists(its[1]['path']) and json.load(open(os.path.join(ROOT, 'installed.json')))['casks'] == [], out)
its = standard()
a = audit_file(its)
run(a, *keys(its, 'oras'), '--plan', plan)
rc, out, err = run(a, '--plan', plan, '--execute', FAKE_MISE_NODETECT='1')
st = [json.loads(l)['status'] for l in open(os.path.join(STATE, 'adopt-log.jsonl'))][-1]
check('installed but not claimed by the manager is reported, never counted as adopted', rc == 1 and st == 'installed-not-detected' and 'not counted as adopted' in out, (rc, st, out))

# ---------------------------------------------------------------- plans cannot be reused, edited, aged or run against a changed world
its = standard()
a = audit_file(its)
run(a, *keys(its, 'Firefox', 'oras'), '--plan', plan)
p = json.load(open(plan))
p['items'][0]['argv'] = ['brew', 'install', '--cask', 'evil']
json.dump(p, open(plan, 'w'))
rc, out, err = run(a, '--plan', plan, '--execute')
check('an edited plan is refused and nothing runs', rc == 1 and 'changed since the dry run' in out and not [l for l in cmds() if l.startswith('brew install')], out)
run(a, *keys(its, 'Firefox'), '--plan', plan)
p = json.load(open(plan))
p['created'] = time.time() - 7200
p['sha256'] = adopt.plan_hash(p)
json.dump(p, open(plan, 'w'))
rc, out, err = run(a, '--plan', plan, '--execute')
check('an old plan is refused', rc == 1 and 'older than' in out, out)
run(a, *keys(its, 'Firefox'), '--plan', plan)
os.utime(its[0]['path'], (time.time() + 50, time.time() + 50))
rc, out, err = run(a, '--plan', plan, '--execute')
check('an app that changed since the plan (it updated itself) is held', rc == 1 and 'changed since the plan was made' in out and os.path.exists(its[0]['path']), out)
run(a, *keys(its, 'oras'), '--plan', plan)
rc, out, err = run(a, '--plan', plan, '--execute', FAKE_LATEST='9.9.9')
check('a different version at execute time than at the dry run is held', rc == 1 and 'has changed since the plan was made' in out and not [l for l in cmds() if l.startswith('mise use')], out)
run(a, *keys(its, 'Firefox'), '--plan', plan)
put(os.path.join(ROOT, 'ps.txt'), f"{its[0]['path']}/Contents/MacOS/firefox\n")
rc, out, err = run(a, '--plan', plan, '--execute')
check('an app started after the plan is held at execute time', rc == 1 and 'running' in out and os.path.exists(its[0]['path']), out)
check('execute without a plan, or with --key, is refused', run(a, '--execute')[0] != 0 and run(a, '--execute', '--key', its[0]['key'], '--plan', plan)[0] != 0, '')

# ---------------------------------------------------------------- the old copy of a command
its = standard()
a = audit_file(its)
run(a, *keys(its, 'oras', 'tool2', 'Same'), '--plan', plan)
# mise claims `oras` (the user installed it by hand after the dry run), but this plan never adopted it
put(os.path.join(ROOT, 'misei/oras/1.3.0/bin/oras'), 'x')
put(os.path.join(ROOT, 'mise-ls.json'), json.dumps({'aqua:oras-project/oras': [{'version': '1.3.0', 'install_path': os.path.join(ROOT, 'misei/oras/1.3.0'), 'installed': True, 'active': True}]}))
rc, out, err = run(a, '--plan', plan, '--trash-old', its[2]['key'])
check('trash-old is refused for a command this plan did not adopt, even if mise has it', rc == 1 and 'was not adopted by this plan' in out and os.path.exists(its[2]['path']), out)
put(os.path.join(ROOT, 'mise-ls.json'), json.dumps({}))
run(a, '--plan', plan, '--execute')
rc, out, err = run(a, '--plan', plan, '--trash-old', its[2]['key'])
check('the old copy inside home goes to the Trash, with the mv that puts it back', rc == 0 and not os.path.exists(its[2]['path']) and any(n.startswith('oras ') for n in os.listdir(TRASH)) and 'mv -- ' in out, (rc, out))
rc, out, err = run(a, '--plan', plan, '--trash-old', its[3]['key'])
check('an old copy outside home is printed with sudo and never moved', rc == 0 and os.path.exists(its[3]['path']) and 'sudo mv' in out and 'not run' in out, out)
rc, out, err = run(a, '--plan', plan, '--trash-old', its[1]['key'])
check('an app is not an old copy, so it is held', rc == 1 and 'not a command adopted' in out, out)
rc, out, err = run(a, '--plan', plan, '--trash-old', 'binary:/nowhere')
check('a key the plan does not have is held', rc == 1 and 'not a command adopted' in out, out)
its = standard()
a = audit_file(its)
run(a, *keys(its, 'oras'), '--plan', plan)
run(a, '--plan', plan, '--execute')
rc, out, err = run(a, '--plan', plan, '--trash-old', its[2]['key'], FAKE_LSOF_HOLD='1')
check('an old copy in use is held', rc == 1 and os.path.exists(its[2]['path']) and 'in use' in out, out)
put(os.path.join(ROOT, 'mise-ls.json'), json.dumps({}))
rc, out, err = run(a, '--plan', plan, '--trash-old', its[2]['key'])
check('the old copy is kept when mise no longer claims the new one', rc == 1 and os.path.exists(its[2]['path']) and 'no longer claims' in out, out)

print()
if FAILS:
    print(f'{len(FAILS)} FAILED:', FAILS)
    sys.exit(1)
print('ALL PASS')
shutil.rmtree(ROOT, ignore_errors=True)
