"""Shared helpers: a read-only command runner with an allow-list, directory sizes, and small formatters.

Everything the audit asks of the system goes through `run`, and `run` refuses any command that is not on the
allow-list below. The list is the whole contract: the audit only asks package managers what they have installed.
"""
import concurrent.futures, os, shutil, subprocess, time

# Exact argument lists that may run. `du -sk PATH` is the one pattern with a variable part.
ALLOWED = (
    ('brew', '--prefix'),
    ('brew', 'info', '--json=v2', '--installed'),
    ('brew', 'autoremove', '-n'),
    ('mise', 'ls', '--json'),
    ('mise', 'ls', '--prunable'),
    ('mise', 'prune', '--dry-run'),
    ('uv', 'tool', 'list'),
    ('uv', 'tool', 'dir'),
    ('uv', 'python', 'dir'),
    ('cargo', 'install', '--list'),
    ('rustup', 'toolchain', 'list'),
    ('mas', 'list'),
    ('crontab', '-l'),
)

# An audit must never update, analyse or tidy anything as a side effect.
ENV = {'HOMEBREW_NO_AUTO_UPDATE': '1', 'HOMEBREW_NO_ANALYTICS': '1', 'HOMEBREW_NO_INSTALL_CLEANUP': '1',
       'HOMEBREW_NO_ENV_HINTS': '1', 'NO_COLOR': '1'}


def allowed(argv):
    argv = tuple(argv)
    return argv in ALLOWED or (len(argv) == 3 and argv[:2] == ('du', '-sk'))


def run(argv, timeout=30, with_stderr=False):
    """Run an allow-listed command. Returns (status, text): 'ok', 'missing' (not installed) or 'failed' (with a reason).
    Some tools (mise) log their findings to stderr, so `with_stderr` appends it to the text."""
    if not allowed(argv):
        raise ValueError(f'command not on the read-only allow-list: {list(argv)}')
    if shutil.which(argv[0]) is None:
        return 'missing', ''
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, **ENV))
    except subprocess.TimeoutExpired:
        return 'failed', f'timed out after {timeout}s'
    except OSError as e:
        return 'failed', str(e)
    if p.returncode != 0:
        return 'failed', f'exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}'
    return 'ok', p.stdout + (p.stderr if with_stderr else '')


def du_kb(path, timeout=30):
    st, out = run(['du', '-sk', path], timeout)
    if st != 'ok':
        return None
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None


def sizes(paths, deadline, workers=8):
    """Sizes in KB for many paths, in parallel, stopping at the deadline. A path that did not finish gets None."""
    out = {p: None for p in paths}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(du_kb, p, max(1, min(30, deadline - time.time()))): p for p in paths}
        for f in concurrent.futures.as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                pass
    return out


def human(kb):
    if kb is None:
        return '?'
    v = float(kb)
    for unit in ('KB', 'MB', 'GB', 'TB'):
        if v < 1024 or unit == 'TB':
            return f'{v:.0f} {unit}' if unit == 'KB' else f'{v:.1f} {unit}'
        v /= 1024


def tilde(path, home):
    """Show a path relative to the home folder, as ~/..., so reports read the same on any machine."""
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path
