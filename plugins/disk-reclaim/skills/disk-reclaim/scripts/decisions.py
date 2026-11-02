#!/usr/bin/env python3
"""Remember the user's "keep" rulings between scans, so an item they decided to keep stops coming back for review.

Only "keep" is ever stored. A stored "remove" would be a standing approval that outlives the moment the user looked
at the item, so removal is approved per run and never saved.

A ruling lapses (the item is shown again) when it expires, or when the item has grown a lot since the user ruled on it.
The file lives in the state folder next to history.tsv: $DISK_RECLAIM_STATE, else $XDG_STATE_HOME/disk-reclaim,
else ~/.local/state/disk-reclaim.

  decisions.py keep --scan scan.json --id 012-some-item [--note "why"] [--days 90]
  decisions.py keep --key KEY --label LABEL --kb SIZE_KB [--note "why"]
  decisions.py forget KEY
  decisions.py list
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disk_status import state_dir, human

DAY = 86400
DEFAULT_DAYS = 90
GROW_FACTOR, GROW_MIN_KB, GROW_ABS_KB = 2, 1048576, 5 * 1048576  # lapse at 2x and +1 GB, or at +5 GB whatever the ratio


class DecisionsError(Exception):
    pass


def file():
    return os.path.join(state_dir(), 'decisions.json')


def load():
    """All rulings. Missing file = none. An unreadable or malformed file raises: callers that delete things must not
    treat 'cannot read my keep list' as 'nothing is kept'."""
    p = file()
    if not os.path.exists(p):
        return {}
    try:
        data = json.load(open(p))
    except (OSError, ValueError) as e:
        raise DecisionsError(f'{p} is unreadable ({e}); fix or remove it') from e
    if not isinstance(data, dict):
        raise DecisionsError(f'{p} is not a JSON object')
    return data


def save(data):
    """Write through a temp file and rename, and let any error propagate: a keep that was silently lost is a safety bug."""
    p = file()
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def lookup(key, kb, rulings=None, now=None):
    """(ruling, lapsed_reason). A live ruling returns (ruling, None). A ruling that no longer applies returns
    (None, why), so the report can say why the item is back. No ruling at all returns (None, None)."""
    rulings = load() if rulings is None else rulings
    d = rulings.get(key) if key else None
    if not d or d.get('decision') != 'keep':
        return None, None
    now = now or time.time()
    if d.get('until') and now > d['until']:
        return None, f'your keep ruling from {time.strftime("%Y-%m-%d", time.localtime(d["time"]))} expired'
    was, now_kb = d.get('kb') or 0, kb or 0
    if was and ((now_kb >= GROW_FACTOR * was and now_kb - was >= GROW_MIN_KB) or now_kb - was >= GROW_ABS_KB):
        return None, f'it grew from {human(was)} to {human(now_kb)} since you chose to keep it'
    return d, None


def keep(key, label, kb, note='', days=DEFAULT_DAYS):
    if not key:
        raise DecisionsError('this item has no stable identity, so a ruling on it could not be matched later')
    data = load()
    now = time.time()
    data[key] = {'decision': 'keep', 'label': label, 'kb': int(kb or 0), 'time': now, 'until': now + days * DAY if days else None, 'note': note}
    save(data)


def forget(key):
    data = load()
    removed = data.pop(key, None) is not None
    if removed:
        save(data)
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('keep', help='record that an item is to be kept')
    k.add_argument('--scan', help='scan_system.py --json output to read the item from')
    k.add_argument('--id', action='append', default=[], help='item id in that scan (repeatable)')
    k.add_argument('--key'), k.add_argument('--label', default=''), k.add_argument('--kb', type=int, default=0)
    k.add_argument('--note', default='')
    k.add_argument('--days', type=int, default=DEFAULT_DAYS, help='lapse after this many days (0 = never expire)')
    f = sub.add_parser('forget', help='drop a ruling')
    f.add_argument('key')
    sub.add_parser('list', help='show the rulings')
    a = ap.parse_args()
    try:
        if a.cmd == 'keep':
            todo = []
            if a.scan:
                items = {i['id']: i for i in json.load(open(a.scan))['items']}
                for iid in a.id:
                    if iid not in items:
                        sys.exit(f'unknown id: {iid}')
                    it = items[iid]
                    if it.get('members'):  # a group has no stable identity of its own: rule on each member
                        todo += [(m['key'], f'{it["label"]}: {m["key"]}', m['kb']) for m in it['members']]
                    elif it.get('key'):
                        todo.append((it['key'], it['label'], it['kb'] or 0))
                    else:
                        sys.exit(f'{iid} has no stable identity, so a ruling on it could not be matched later')
            elif a.key:
                todo.append((a.key, a.label or a.key, a.kb))
            else:
                sys.exit('give --scan with --id, or --key')
            for key, label, kb in todo:
                keep(key, label, kb, a.note, a.days)
            print(f'kept {len(todo)} item(s) for {a.days} days' if a.days else f'kept {len(todo)} item(s) with no expiry')
        elif a.cmd == 'forget':
            print('dropped' if forget(a.key) else 'no such ruling')
        else:
            data = load()
            for key, d in sorted(data.items(), key=lambda x: -(x[1].get('kb') or 0)):
                until = time.strftime('%Y-%m-%d', time.localtime(d['until'])) if d.get('until') else 'never'
                print(f'{d["decision"]:5} {human(d.get("kb") or 0):>9}  until {until}  {d.get("label") or key}  [{key}]' + (f'  note: {d["note"]}' if d.get('note') else ''))
            if not data:
                print('no rulings recorded')
    except DecisionsError as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
