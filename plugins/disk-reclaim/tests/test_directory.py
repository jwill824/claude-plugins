#!/usr/bin/env python3
"""Tests for directory mode (scan.py, reclaim.py) and the platform guards.

Builds throwaway git projects and runs the scripts as a user would. Works on macOS and Linux,
which is the point: directory mode and --status are the parts that claim to run on both.

    python3 -B tests/test_directory.py
"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
ROOT = os.path.realpath(tempfile.mkdtemp(prefix='dir-test-'))
ENV = dict(os.environ, HOME=ROOT, DISK_RECLAIM_STATE=os.path.join(ROOT, 'state'),
           GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com',
           GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.com')
FAILS = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  -> {detail}'))
    if not cond:
        FAILS.append(name)


def run(script, *args, cwd=ROOT):
    p = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, script), *args], cwd=cwd, env=ENV,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, env=ENV, check=True, capture_output=True)


def blob(path, mb):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'\0' * (mb * 1024 * 1024))


def project(name):
    """A git project: ignored node_modules (manifest beside it), a tracked folder, an ignored folder with a key."""
    d = os.path.join(ROOT, name)
    os.makedirs(d)
    git(d, 'init', '-q')
    with open(os.path.join(d, '.gitignore'), 'w') as f:
        f.write('node_modules/\ndist/\n')
    with open(os.path.join(d, 'package.json'), 'w') as f:
        f.write('{"name": "x"}\n')
    blob(os.path.join(d, 'node_modules', 'dep', 'blob.bin'), 3)
    blob(os.path.join(d, 'tracked_build', 'keep.bin'), 2)
    os.makedirs(os.path.join(d, 'src'))
    with open(os.path.join(d, 'src', 'a.txt'), 'w') as f:
        f.write('hello\n')
    os.makedirs(os.path.join(d, 'dist'))
    with open(os.path.join(d, 'dist', 'server.pem'), 'w') as f:
        f.write('-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n')
    blob(os.path.join(d, 'dist', 'bundle.js'), 2)
    git(d, 'add', '.gitignore', 'package.json', 'src', 'tracked_build')
    git(d, 'commit', '-q', '-m', 'init')
    return d


def tree(d):
    out = {}
    for base, dirs, files in os.walk(d):
        if '.git' in dirs:
            dirs.remove('.git')
        for fn in files:
            p = os.path.join(base, fn)
            out[os.path.relpath(p, d)] = os.path.getsize(p)
    return out


def scan_json(d):
    rc, out = run('scan.py', d, '--min-mb', '0', '--json')
    return rc, (json.loads(out) if rc == 0 else out)


# ---- scan is read-only and classifies correctly
p1 = project('proj1')
before = tree(p1)
rc, scan = scan_json(p1)
check('scan.py exits 0 and returns JSON', rc == 0 and isinstance(scan, dict), str(scan)[:200])
check('scan.py changes nothing', tree(p1) == before)
cands = {os.path.relpath(c['path'], p1): c for c in scan['candidates']} if isinstance(scan, dict) else {}
check('ignored node_modules with a manifest is SAFE', cands.get('node_modules', {}).get('tier') == 'SAFE', str(cands.get('node_modules')))
check('a tracked folder is never offered', 'tracked_build' not in cands and 'src' not in cands, str(list(cands)))
check('an ignored folder holding key material is not SAFE', cands.get('dist', {}).get('tier') != 'SAFE', str(cands.get('dist')))

# ---- reclaim: dry run changes nothing, execute removes only the SAFE item
scan_file = os.path.join(ROOT, 'scan1.json')
with open(scan_file, 'w') as f:
    json.dump(scan, f)
rc, out = run('reclaim.py', scan_file, '--min-mb', '0', '--no-protect')
check('reclaim.py without --execute is a dry run', rc == 0 and 'DRY RUN' in out and tree(p1) == before, out[:200])

log = os.path.join(ROOT, 'reclaim-log.tsv')
rc, out = run('reclaim.py', scan_file, '--min-mb', '0', '--no-protect', '--execute', '--log', log)
check('--execute removes node_modules', rc == 0 and not os.path.exists(os.path.join(p1, 'node_modules')), out[-200:])
check('--execute leaves tracked files, source and key material alone',
      os.path.exists(os.path.join(p1, 'tracked_build', 'keep.bin')) and os.path.exists(os.path.join(p1, 'src', 'a.txt'))
      and os.path.exists(os.path.join(p1, 'dist', 'server.pem')))
check('--execute writes a log naming what it deleted', os.path.exists(log) and 'deleted' in open(log).read())

# ---- the scan describes one moment: a file that becomes tracked afterwards holds the item
p2 = project('proj2')
rc, scan2 = scan_json(p2)
scan_file2 = os.path.join(ROOT, 'scan2.json')
with open(scan_file2, 'w') as f:
    json.dump(scan2, f)
git(p2, 'add', '-f', os.path.join('node_modules', 'dep', 'blob.bin'))
rc, out = run('reclaim.py', scan_file2, '--min-mb', '0', '--no-protect', '--execute', '--log', os.path.join(ROOT, 'log2.tsv'))
check('an item that gained a tracked file after the scan is held, not deleted',
      os.path.exists(os.path.join(p2, 'node_modules', 'dep', 'blob.bin')), out[-300:])

# ---- key material is matched by name; every default SSH key name and the extra extensions hold an item
sys.path.insert(0, SCRIPTS)
from scan import has_key_material

def holds(filename):
    d = os.path.join(ROOT, 'keys', filename.replace('.', '_'))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), 'w') as f:
        f.write('x')
    return bool(has_key_material(d))

for name in ('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'id_ecdsa_sk', 'id_ed25519_sk', 'ID_RSA',
             'deploy.ppk', 'AuthKey_ABC123.p8', 'server.pem', '.env.local', 'credentials'):
    check(f'key material: {name} holds an item', holds(name))
for name in ('id_rsa.pub', 'idea.txt', 'bundle.js', 'README.md'):
    check(f'not key material: {name} does not hold an item', not holds(name))

# ---- platform guards and status work everywhere
rc, out = run('scan_system.py', '--status')
check('--status runs on this platform', rc in (0, 1) and 'Disk:' in out, f'rc={rc} {out[:200]}')
if sys.platform != 'darwin':
    rc, out = run('scan_system.py')
    check('scan_system.py exits with a message off macOS', rc != 0 and 'macOS' in out and 'scan.py' in out, f'rc={rc} {out[:200]}')
    rc, out = run('execute_system.py', os.path.join(ROOT, 'nope.json'))
    check('execute_system.py exits with a message off macOS', rc != 0 and 'macOS' in out, f'rc={rc} {out[:200]}')
else:
    print('SKIP  off-macOS guards (this is macOS)')

shutil.rmtree(ROOT, ignore_errors=True)
print()
print('FAILED: ' + ', '.join(FAILS) if FAILS else 'all directory-mode checks passed')
sys.exit(1 if FAILS else 0)
