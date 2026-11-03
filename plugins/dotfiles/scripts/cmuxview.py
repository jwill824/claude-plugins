"""Open the map in a cmux browser split, or reload the tab that already shows it. Opt-in (`audit.py --cmux`), cmux only.

This is the one place the plugin talks to cmux, and it says only three things, each in one exact form:

    cmux tree --all --json                 list the surfaces (read-only)
    cmux open PATH.html --no-focus         open the map in a browser split, without taking focus
    cmux browser surface:N reload          reload a tab that already shows this very file

It never uses cmux's browser automation (`eval`, scripts, cookies, storage, network), `send` or `rpc`, and the map has no
channel back to cmux. An existing tab is recognised only by its exact URL, in the current workspace: a title proves nothing.
When it cannot tell (cmux missing, the listing failing or unreadable) it does nothing and says so, never opening or reloading
blindly. Whatever happens, the audit's own result is unaffected.
"""
import json, os, pathlib, re, shutil, subprocess

SURFACE_RE = re.compile(r'^surface:\d+$')
TIMEOUT = 20


def _plain(text):
    return isinstance(text, str) and text and not any(ord(c) < 32 or ord(c) == 127 for c in text)


def allowed(argv):
    """Exactly the three forms above, with the path or surface checked. Anything else is refused."""
    argv = list(argv)
    if argv == ['cmux', 'tree', '--all', '--json']:
        return True
    if len(argv) == 4 and argv[:2] == ['cmux', 'open'] and argv[3] == '--no-focus':
        p = argv[2]
        return _plain(p) and os.path.isabs(p) and p.endswith('.html') and os.path.isfile(p)
    if len(argv) == 4 and argv[:2] == ['cmux', 'browser'] and argv[3] == 'reload':
        return bool(SURFACE_RE.match(argv[2]))
    return False


def run(argv):
    """(ok, text). Refuses any command that is not one of the three forms; failures become text, never exceptions."""
    if not allowed(argv):
        raise ValueError(f'not an allowed cmux command: {argv}')
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f'timed out after {TIMEOUT}s'
    except OSError as e:
        return False, str(e.strerror or e)
    return (True, p.stdout) if p.returncode == 0 else (False, (p.stderr or p.stdout).strip()[:200])


def matching_surfaces(tree, url):
    """Browser surfaces in the caller's workspace whose URL is exactly `url`. None when the tree cannot be understood."""
    try:
        caller = tree['caller']
        want_id, want_ref = caller.get('workspace_id'), caller.get('workspace_ref')
        if not (want_id or want_ref):
            return None
        found = []
        for w in tree['windows']:
            for ws in w['workspaces']:
                if not ((want_id and ws.get('id') == want_id) or (want_ref and ws.get('ref') == want_ref)):
                    continue
                for pane in ws['panes']:
                    for s in pane['surfaces']:
                        ref = s.get('ref')
                        if s.get('type') == 'browser' and s.get('url') == url and isinstance(ref, str) and SURFACE_RE.match(ref):
                            found.append(ref)
        return found
    except (KeyError, TypeError, AttributeError):
        return None


def show(path):
    """Open or reload the map in cmux. Returns one sentence for the user. Never raises."""
    path = os.path.abspath(path)
    if not os.environ.get('CMUX_WORKSPACE_ID') or shutil.which('cmux') is None:
        return f'not inside cmux, so the map was not opened: {path}'
    if not allowed(['cmux', 'open', path, '--no-focus']):
        return f'the map path is not one cmux is asked to open, so it was not opened: {path}'
    ok, text = run(['cmux', 'tree', '--all', '--json'])
    try:
        tree = json.loads(text) if ok else None
    except ValueError:
        tree = None
    found = matching_surfaces(tree, pathlib.Path(path).as_uri()) if isinstance(tree, dict) else None
    if found is None:
        return f'could not read cmux\'s list of surfaces, so the map was not opened or reloaded (it is at {path})'
    if found:
        done = []
        for ref in found:
            ok, text = run(['cmux', 'browser', ref, 'reload'])
            if not ok:
                return f'cmux refused to reload {ref} ({text}); the map is at {path}'
            done.append(ref)
        return f"reloaded the map in cmux ({', '.join(done)})"
    ok, text = run(['cmux', 'open', path, '--no-focus'])
    return 'opened the map in a cmux browser split (not focused)' if ok else f'cmux refused to open the map ({text}); it is at {path}'
