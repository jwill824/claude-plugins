"""Fast, read-only disk status for `scan_system.py --status`, plus the small state files it reads.

State lives in $DISK_RECLAIM_STATE, else $XDG_STATE_HOME/disk-reclaim, else ~/.local/state/disk-reclaim:
  history.tsv     one line per status check: unix time, free KB, total KB
  last-scan.json  summary written by `scan_system.py --save-state`
  last-run.json   summary written by every `execute_system.py --execute`
Nothing here scans, deletes or runs a tool, so it is cheap enough for a hook or a scheduled check.
"""
import json, os, shutil, time

DAY = 86400


def state_dir():
    d = os.environ.get('DISK_RECLAIM_STATE') or os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'disk-reclaim')
    os.makedirs(d, exist_ok=True)
    return d


def _write(name, obj):
    try:
        with open(os.path.join(state_dir(), name), 'w') as f:
            json.dump(obj, f)
    except OSError:
        pass


def _read(name):
    try:
        return json.load(open(os.path.join(state_dir(), name)))
    except (OSError, ValueError):
        return None


def volume():
    vol = '/System/Volumes/Data' if os.path.isdir('/System/Volumes/Data') else '/'
    u = shutil.disk_usage(vol)
    return u.total // 1024, u.free // 1024


def save_scan(data):
    """Summarise a full scan: sizes per tier and per SAFE bucket, not the items."""
    items = data['items']
    tier = lambda t: sum(i['kb'] or 0 for i in items if i['tier'] == t)
    bucket = lambda b: sum(i['kb'] or 0 for i in items if i.get('bucket') == b)
    _write('last-scan.json', {'time': time.time(), 'free_kb': data['free_kb'], 'safe_stale_kb': bucket('SAFE-STALE'), 'safe_used_kb': bucket('SAFE-USED'),
                              'safe_blocked_kb': bucket('SAFE-BLOCKED'), 'verify_kb': tier('VERIFY'), 'keep_kb': tier('KEEP')})


def save_run(done, du_freed_kb, df_delta_kb, log, trashed_kb=0, vm_kb=0):
    _write('last-run.json', {'time': time.time(), 'done': done, 'du_freed_kb': du_freed_kb, 'df_delta_kb': df_delta_kb, 'log': log,
                             'trashed_kb': trashed_kb, 'vm_kb': vm_kb})


def human(kb):
    v = float(abs(kb))
    for unit in ('KB', 'MB', 'GB', 'TB'):
        if v < 1024 or unit == 'TB':
            return f'{v:.0f} {unit}' if unit == 'KB' else f'{v:.1f} {unit}'
        v /= 1024


def ago(t):
    d = (time.time() - t) / DAY
    return 'today' if d < 1 else f'{d:.0f} day{"s" if d >= 1.5 else ""} ago'


def record_history(total, free):
    path = os.path.join(state_dir(), 'history.tsv')
    rows = []
    try:
        rows = [tuple(map(int, l.split('\t'))) for l in open(path).read().splitlines() if l.count('\t') == 2]
    except (OSError, ValueError):
        pass
    now = int(time.time())
    if not rows or now - rows[-1][0] >= 600:  # at most one entry per 10 minutes
        rows.append((now, free, total))
    rows = [r for r in rows if now - r[0] <= 60 * DAY][-2000:]
    try:
        with open(path, 'w') as f:
            f.write(''.join('\t'.join(map(str, r)) + '\n' for r in rows))
    except OSError:
        pass
    return rows


def hook_output(free, pct, low, why, trend, run, problems, source):
    """The SessionStart hook payload: a one-line summary the user sees (no model cost), plus context for the model only when
    something needs attention, telling it to offer choices and never to delete on its own."""
    parts = [f'Disk: {human(free)} free ({pct:.0f}%)']
    if low:
        parts.append(why)
    if trend:
        parts.append(trend)
    if run:
        parts.append(f'last cleanup {ago(run["time"])}')
    out = {'systemMessage': ' · '.join(parts)}
    if problems:
        ctx = (f'Session-start disk check (source: {source or "unknown"}): {human(free)} free ({pct:.0f}%), {why}'
               + ''.join('; ' + p for p in problems[1:]) + '.\n'
               'Before starting other work, briefly tell the user and use AskUserQuestion to offer options, e.g.:\n'
               '- see where the space went: /disk-reclaim:system (read-only scan)\n'
               '- walk the items that need a decision: /disk-reclaim:review\n'
               '- empty the Trash, if the last cleanup moved items there\n'
               '- leave it for now\n'
               'Removal only starts when the user types /disk-reclaim:run or /disk-reclaim:review, so give them the command; do not remove anything yourself. '
               'If the user\'s first message is an unrelated urgent request, answer it and raise the disk space afterwards.')
        out['hookSpecificOutput'] = {'hookEventName': 'SessionStart', 'additionalContext': ctx}
    return out


def status(warn_free_gb=50, warn_free_pct=10, max_scan_age_days=14, quiet_if_ok=False, hook=False, source=None):
    """Print the status and return an exit code: 0 fine, 1 needs attention.

    Only low space needs attention by itself. A missing or old scan is shown, and counts as a problem only when space is
    low, so a hook that runs every session stays silent until it matters.
    """
    total, free = volume()
    rows = record_history(total, free)
    problems, lines = [], []
    pct = 100 * free / total if total else 0
    low = free < warn_free_gb * 1048576 or pct < warn_free_pct
    why = ' and '.join(([f'under {warn_free_gb:g} GB free'] if free < warn_free_gb * 1048576 else []) +
                       ([f'under {warn_free_pct:g}% free'] if pct < warn_free_pct else []))
    if low:
        problems.append(f'only {human(free)} free ({pct:.0f}%)')
    lines.append(f'Disk: {human(free)} free of {human(total)} ({pct:.0f}% free)' + ('  LOW' if low else ''))

    now = time.time()
    trend = None
    old = [r for r in rows if 0.5 * DAY <= now - r[0] <= 14 * DAY]
    if old:
        t0, f0, _ = min(old, key=lambda r: r[0])
        days = (now - t0) / DAY
        d = free - f0
        rate = d / days
        runway = f', about {free / -rate:.0f} days of room at that pace' if rate < -1024 else ''
        trend = f'{"+" if d >= 0 else "-"}{human(d)} over {days:.1f} days'
        lines.append(f'Trend: {trend}{runway} (net of any cleanups)')

    scan = _read('last-scan.json')
    if scan:
        age = (now - scan['time']) / DAY
        stale = age > max_scan_age_days
        if stale and low:
            problems.append(f'last full scan is {age:.0f} days old')
        lines.append(f'Last scan: {ago(scan["time"])}: SAFE {human(scan["safe_stale_kb"])} stale + {human(scan["safe_used_kb"])} recently used'
                     f' (+{human(scan["safe_blocked_kb"])} blocked), needs review {human(scan["verify_kb"])}' + ('  OLD' if stale else ''))
    else:
        if low:
            problems.append('no full scan recorded')
        lines.append('Last scan: none recorded (run `scan_system.py --save-state`)')
    run = _read('last-run.json')
    extra = ''
    if run:  # older state files lack these fields
        extra = (f', {human(run["trashed_kb"])} in the Trash (frees space when emptied)' if run.get('trashed_kb') else '') + \
                (f', {human(run["vm_kb"])} removed inside the Colima VM (frees space after fstrim)' if run.get('vm_kb') else '')
    lines.append(f'Last cleanup: {ago(run["time"])}: {run["done"]} item(s), folders shrank by {human(run["du_freed_kb"])}, free space {"+" if run["df_delta_kb"] >= 0 else "-"}{human(run["df_delta_kb"])}{extra}'
                 if run else 'Last cleanup: none recorded')
    if hook:  # always exit 0: a hook must never fail the session
        if not (quiet_if_ok and not problems):
            print(json.dumps(hook_output(free, pct, low, why, trend, run, problems, source)))
        return 0
    if not (quiet_if_ok and not problems):
        print('\n'.join(lines))
        if problems:
            print('Attention: ' + '; '.join(problems) + '. Run `scan_system.py --save-state` for the full picture.')
    return 1 if problems else 0
