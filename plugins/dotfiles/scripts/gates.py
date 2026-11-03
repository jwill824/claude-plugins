"""The re-checks every cleanup does just before it acts, shared by `tidy.py` (which prints) and `fix.py` (which moves).

An audit describes one moment. Before anything is offered or moved, the item is looked at again, and one function answers for
both tools so they can never disagree. `recheck` returns None when the item still looks like an orphan candidate, otherwise the
reason it is held. It reads names and times only, never file contents.
"""
import os, time


def recheck(item, home, refs, records, active_days=60, now=None):
    """None if the item is still an offerable orphan candidate, else why it is held (a sentence, no key)."""
    from audit import newest_write
    now = time.time() if now is None else now
    rec = records.get(item['key'])
    rec = rec if rec and rec['cls'] == item['cls'] else item.get('accepted')
    if rec:
        return f"held, you accepted it on {rec.get('date', '?')}: {rec['note']}"
    if item['cls'] != 'orphaned':
        return f"not offered, it is {item['cls']} ({item['evidence'][0] if item['evidence'] else ''}); only orphan candidates are"
    if item['kind'] not in ('dotitem', 'broken-link'):
        return None
    p = item['path']
    if not os.path.lexists(p):
        return 'already gone'
    if item['kind'] == 'broken-link' and os.path.exists(p):
        return 'the link works again; nothing to do'
    if item['kind'] == 'dotitem':
        found = refs.find_path(os.path.relpath(p, home))
        if found:
            return f"held, config files now mention it ({', '.join(found)})"
        if refs.unreadable:
            return f"held, could not read {', '.join(refs.unreadable)}"
        newest, trunc = newest_write(p, now - active_days * 86400)
        if newest is not None and newest >= now - active_days * 86400:
            return f'held, something wrote to it within the last {active_days} days'
        if trunc:
            return 'held, too large to check for recent writes'
    return None
