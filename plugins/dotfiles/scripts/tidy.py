#!/usr/bin/env python3
"""Print the commands that would clean up audit findings you name. It prints. It never runs anything.

    python3 tidy.py AUDIT.json --key KEY [--key KEY ...]

Only orphan candidates are offered, and each is re-checked first, because the audit describes one moment:
  folder / broken link   still there, still not mentioned by a config file, still not written to recently
  brew orphans           `brew autoremove` removes every orphan, so the command is the manager's own, after a dry run
  mise versions          `mise prune <tool>` prunes every prunable version of that tool

Folders and links go to the Trash (a move, reversible until the Trash is emptied). Managers' own removals are
reinstallable, and a snapshot command comes first. It never prints --zap, --force or --ignore-dependencies.
"""
import argparse, datetime, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accepted
from audit import newest_write
from refs import Refs

FORBIDDEN = ('--zap', '--force', '--ignore-dependencies', ' -f ', 'rm -rf', 'sudo')


def trash_cmd(path, stamp):
    base = os.path.basename(path.rstrip('/'))
    return f'mv -- \'{path}\' "$HOME/.Trash/{base} {stamp}"'


def main():
    ap = argparse.ArgumentParser(description='Print cleanup commands for audit findings you name. Never runs them.')
    ap.add_argument('audit')
    ap.add_argument('--key', action='append', default=[], help='an item key from the audit (repeatable)')
    ap.add_argument('--active-days', type=int, default=60)
    ap.add_argument('--state', metavar='FILE', help='accepted-state file (default: $DOTFILES_STATE, else the XDG state folder)')
    a = ap.parse_args()
    rep = json.load(open(a.audit))
    home = rep['home']
    items = {i['key']: i for i in rep['items']}
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')
    refs = Refs(home)
    records, _ = accepted.load(a.state or accepted.state_path())
    out, held, cmds, snaps = [], [], [], []
    brew_done, mise_tools = False, []
    for key in a.key:
        i = items.get(key)
        if i is None:
            held.append(f'`{key}`: not in this audit')
            continue
        rec = records.get(key)
        rec = rec if rec and rec['cls'] == i['cls'] else i.get('accepted')
        if rec:
            held.append(f"`{key}`: held, you accepted it on {rec.get('date', '?')}: {rec['note']}")
            continue
        if i['cls'] != 'orphaned':
            held.append(f"`{key}`: not offered, it is {i['cls']} ({i['evidence'][0] if i['evidence'] else ''}); only orphan candidates are")
            continue
        if i['kind'] in ('dotitem', 'broken-link'):
            p = i['path']
            if not os.path.lexists(p):
                held.append(f'`{key}`: already gone')
                continue
            if i['kind'] == 'broken-link' and os.path.exists(p):
                held.append(f'`{key}`: the link works again; nothing to do')
                continue
            if i['kind'] == 'dotitem':
                rel = os.path.relpath(p, home)
                found = refs.find_path(rel)
                if found:
                    held.append(f"`{key}`: held, config files now mention it ({', '.join(found)})")
                    continue
                if refs.unreadable:
                    held.append(f"`{key}`: held, could not read {', '.join(refs.unreadable)}")
                    continue
                newest, trunc = newest_write(p, time.time() - a.active_days * 86400)
                if newest is not None and newest >= time.time() - a.active_days * 86400:
                    held.append(f'`{key}`: held, something wrote to it within the last {a.active_days} days')
                    continue
                if trunc:
                    held.append(f'`{key}`: held, too large to check for recent writes')
                    continue
            cmds.append(trash_cmd(p, stamp))
        elif i['kind'] == 'brew-orphan':
            if not brew_done:
                brew_done = True
                snaps.append(f'brew bundle dump --file="$HOME/Brewfile.before-tidy {stamp}"   # snapshot: reinstall anything from it')
                cmds += ['brew autoremove --dry-run   # review what it lists first', 'brew autoremove   # removes every formula the dry run listed']
        elif i['kind'] == 'mise-prunable':
            tool = i['name'].split('@')[0]
            if tool not in mise_tools:
                mise_tools.append(tool)
        else:
            held.append(f"`{key}`: no cleanup command for {i['kind']}")
    if mise_tools:
        snaps.append(f'mise ls --json > "$HOME/mise-tools.before-tidy {stamp}.json"   # snapshot: reinstall any version from it')
        for t in mise_tools:
            cmds += [f'mise prune --dry-run {t}   # review first', f'mise prune {t}   # prunes every prunable version of {t}']
    lines = ['# Cleanup commands (not run)', '', 'Review them, then run the ones you want yourself. Nothing here has been executed.', '']
    if snaps or cmds:
        lines += ['```sh'] + snaps + cmds + ['```', '']
    if held:
        lines += ['## Not offered', ''] + [f'- {h}' for h in held] + ['']
    text = '\n'.join(lines)
    bad = [f for f in FORBIDDEN if f in text]
    if bad:
        sys.exit(f'refusing to print a command containing {bad}')
    print(text)


if __name__ == '__main__':
    main()
