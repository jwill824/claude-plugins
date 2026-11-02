"""Gate tests for scan_system.py. Builds a fake home folder in a temp dir; touches nothing real.
Run: python3 -B tests/test_gates.py"""
import os, sys, subprocess, time, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import scan_system as ss

root = os.path.realpath(tempfile.mkdtemp(prefix='diskreclaim-fixture-'))
def mk(rel, files=('f.bin',), age_days=0):
    d = os.path.join(root, rel); os.makedirs(d, exist_ok=True)
    for f in files:
        p = os.path.join(d, f); open(p, 'wb').write(b'x' * 2_000_000)
        t = time.time() - age_days * 86400; os.utime(p, (t, t))
    t = time.time() - age_days * 86400; os.utime(d, (t, t)); return d
ss.H = root                                   # fixture acts as the home folder
stale = mk('c/stale', age_days=90)
active = mk('c/active', age_days=0)
keyd = mk('c/withkey', files=('f.bin', '.env'), age_days=90)
gitd = mk('c/withrepo', age_days=90)
subprocess.run(['git', '-C', gitd, 'init', '-q']); subprocess.run(['git', '-C', gitd, '-c', 'user.email=a@b', '-c', 'user.name=t', 'add', '-A'])
subprocess.run(['git', '-C', gitd, '-c', 'user.email=a@b', '-c', 'user.name=t', 'commit', '-qm', 'x'])
open(os.path.join(gitd, 'new.txt'), 'w').write('uncommitted')         # dirty + commit on no remote
openp = mk('c/held', age_days=90); fh = open(os.path.join(openp, 'f.bin'), 'rb')   # this process holds a file open
cmdl = mk('c/cmdline', age_days=90)
proc = subprocess.Popen(['python3', '-c', 'import time; time.sleep(60)', cmdl])                          # a process whose command line names the path
real = mk('c/real', age_days=90); link = os.path.join(root, 'c/link'); os.symlink(real, link)
shallow = mk('top', age_days=90)

rm = lambda p: ss.rm_cmd(p)
for label, p in [('stale', stale), ('active', active), ('withkey', keyd), ('withrepo', gitd), ('held', openp), ('cmdline', cmdl), ('symlink', link), ('shallow', shallow)]:
    ss.add('Tool caches', label, 'SAFE', 'fixture', p, kb=2000, cmd=rm(p))
ss.add('Claude data', 'authored-repo', 'VERIFY', 'fixture', gitd, kb=2000, cmd=rm(gitd))
ss.CFG.update(active=7, stale=30, deadline=time.time() + 60)
# the held-open check must see this process's own handle, so test it as a different pid would
ss.os.getpid = lambda: -1
time.sleep(0.5)
items = ss.ITEMS; ss.apply_gates(items)
by = {i['label']: i for i in items}
def show(l):
    i = by[l]; return f"{l:<14} tier={i['tier']:<6} act={i['activity']['class']:<7} blocked={bool(i['blocked'])!s:<5} gates=" + ','.join(f"{g['name']}:{g['result']}" for g in i['gates'])
for l in by: print(show(l))
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(('PASS ' if cond else 'FAIL ') + name)
print()
check('stale tree classed STALE and not blocked', by['stale']['activity']['class'] == 'STALE' and not by['stale']['blocked'] and by['stale']['tier'] == 'SAFE')
check('active tree classed ACTIVE', by['active']['activity']['class'] == 'ACTIVE')
check('key material demotes SAFE -> VERIFY', by['withkey']['tier'] == 'VERIFY')
check('dirty/unpushed repo demotes SAFE -> VERIFY', by['withrepo']['tier'] == 'VERIFY')
check('same repo in Claude data goes to KEEP', by['authored-repo']['tier'] == 'KEEP')
check('open file blocks SAFE item', by['held']['blocked'] and 'python3' in by['held']['blocked'])
check('process command line blocks SAFE item', by['cmdline']['blocked'] and 'command line' in by['cmdline']['blocked'])
check('symlink path fails the sanity gate -> KEEP', by['symlink']['tier'] == 'KEEP')
check('shallow path fails the sanity gate -> KEEP', by['shallow']['tier'] == 'KEEP')
check('fingerprint recorded for real paths', by['stale']['fingerprint'] and by['stale']['fingerprint']['kb'] == 2000)
# staleness must never promote: a VERIFY stale item stays VERIFY
ss.ITEMS.clear(); ss.add('Tool caches', 'verify-stale', 'VERIFY', 'fixture', stale, kb=2000); ss.apply_gates(ss.ITEMS)
check('stale VERIFY item is never promoted', ss.ITEMS[0]['tier'] == 'VERIFY' and ss.ITEMS[0]['activity']['class'] == 'STALE')
proc.kill(); fh.close(); shutil.rmtree(root, ignore_errors=True)
print('\nALL PASS' if ok else '\nSOME FAILED'); sys.exit(0 if ok else 1)
