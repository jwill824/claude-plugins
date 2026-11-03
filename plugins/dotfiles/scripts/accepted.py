"""The accepted state: items the user has looked at and chosen to keep as they are.

An accepted item counts as resolved. Only "accept" is ever recorded, never "remove". The audit reads this file and never writes
it; `accept.py`, run on a typed command, is the only writer. The file names paths on this machine, so it is created private
(folder 0700, file 0600) and replaced atomically.

An acceptance applies only while the item still has the class it had when it was accepted. If the class changes (an accepted
unmanaged app becomes an orphan, a source that had failed is readable again) the acceptance is void and the item is unresolved
again: what the user judged was a different situation.
"""
import json, os, tempfile

ACCEPTABLE = ('unmanaged', 'unknown', 'unattributed', 'referenced', 'missing', 'orphaned')
UNRESOLVED = ACCEPTABLE
NOTE_MAX = 200
KEY_MAX = 1024


def state_path(environ=None):
    env = os.environ if environ is None else environ
    base = env.get('DOTFILES_STATE') or os.path.join(env.get('XDG_STATE_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'state'), 'dotfiles')
    return os.path.join(base, 'accepted.json')


def has_control(text):
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def bad_note(note):
    """Why this note cannot be stored, or None."""
    if not isinstance(note, str) or not note.strip():
        return 'a note is required (why is this fine as it is?)'
    if len(note.strip()) > NOTE_MAX:
        return f'the note is longer than {NOTE_MAX} characters'
    if has_control(note):
        return 'the note contains control characters'
    return None


def bad_key(key):
    if not isinstance(key, str) or not key or len(key) > KEY_MAX:
        return 'not a usable key'
    if has_control(key):
        return 'the key contains control characters'
    return None


def load(path):
    """({key: record}, warning). A missing file is empty and silent. An unreadable one is empty with a warning, and is left alone."""
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path) as f:
            data = json.load(f)
        recs = data['accepted']
        if not isinstance(recs, dict):
            raise ValueError('shape')
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {}, f'could not read the accepted file {path} ({type(e).__name__}); nothing is treated as accepted, and the file was not changed'
    good = {k: v for k, v in recs.items() if isinstance(v, dict) and isinstance(v.get('cls'), str) and isinstance(v.get('note'), str)}
    warn = None if len(good) == len(recs) else f'{len(recs) - len(good)} malformed entr{"y" if len(recs) - len(good) == 1 else "ies"} in {path} ignored'
    return good, warn


def save(path, records):
    """Atomically replace the file: write a private temp file next to it, then rename. A crash leaves the old file intact."""
    d = os.path.dirname(path) or '.'
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.accepted-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump({'version': 1, 'accepted': records}, f, indent=1, sort_keys=True)
            f.write('\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
