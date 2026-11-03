#!/usr/bin/env python3
"""Mark audit items as accepted (or forget an acceptance). The only thing in this plugin that writes anything.

    python3 accept.py AUDIT.json --key KEY [--key KEY ...] --note "why this is fine as it is"
    python3 accept.py AUDIT.json --forget --key KEY [--key KEY ...]

It writes one file, the accepted state (see accepted.py), and nothing else: it never installs, removes or moves anything.
Only items that are not already fine can be accepted (unmanaged, unknown, unattributed, referenced, missing, orphaned).
Accepting an orphan candidate keeps it: `tidy.py` will no longer offer it.
"""
import argparse, datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accepted


def main():
    ap = argparse.ArgumentParser(description='Mark audit items as accepted. Writes only the accepted-state file.')
    ap.add_argument('audit')
    ap.add_argument('--key', action='append', default=[], help='an item key from the audit (repeatable)')
    ap.add_argument('--note', default='', help='why it is fine as it is (required to accept)')
    ap.add_argument('--forget', action='store_true', help='remove the acceptance of the named keys instead')
    ap.add_argument('--state', metavar='FILE', help='accepted-state file (default: $DOTFILES_STATE, else the XDG state folder)')
    a = ap.parse_args()
    if not a.key:
        sys.exit('name at least one --key')
    path = a.state or accepted.state_path()
    records, warn = accepted.load(path)
    if warn:
        sys.exit(f'refusing to write: {warn}')
    out, held, changed = [], [], 0
    if a.forget:
        for k in a.key:
            bad = accepted.bad_key(k)
            if bad:
                held.append(f'`{k[:60]!r}`: {bad}')
            elif k in records:
                del records[k]
                changed += 1
                out.append(f'forgot `{k}`')
            else:
                held.append(f'`{k}`: was not accepted')
    else:
        why = accepted.bad_note(a.note)
        if why:
            sys.exit(why)
        rep = json.load(open(a.audit))
        items = {i['key']: i for i in rep['items']}
        today = datetime.date.today().isoformat()
        for k in a.key:
            bad = accepted.bad_key(k)
            i = items.get(k)
            if bad:
                held.append(f'`{k[:60]!r}`: {bad}')
            elif i is None:
                held.append(f'`{k}`: not in this audit')
            elif i['cls'] not in accepted.ACCEPTABLE:
                held.append(f"`{k}`: not accepted, it is already fine ({i['cls']}); only unresolved items can be accepted")
            else:
                records[k] = {'note': a.note.strip(), 'date': today, 'cls': i['cls'], 'name': i['name']}
                changed += 1
                out.append(f"accepted `{k}` ({i['cls']})")
    if changed:
        accepted.save(path, records)
    lines = ['# Accepted state', '']
    lines += [f'- {o}' for o in out] or ['- nothing changed']
    if held:
        lines += ['', '## Not changed', ''] + [f'- {h}' for h in held]
    lines += ['', f'File: `{path}` ({len(records)} accepted). Nothing else was changed.']
    print('\n'.join(lines))
    sys.exit(0 if changed else 1)


if __name__ == '__main__':
    main()
