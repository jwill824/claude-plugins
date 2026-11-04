---
name: fix
description: Move orphan folders and broken links from a dotfiles audit to the Trash, after a dry run and the user's explicit confirmation. Moves nothing else, never deletes. Only on the user's explicit command /dotfiles:fix.
argument-hint: "[audit.json] [keys, one per line, copied from the map]"
disable-model-invocation: true
---

# fix

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

This is the only skill that changes anything on disk besides the accepted-state file, and what it does is narrow: it renames orphan folders and broken links inside the user's home folder into `~/.Trash`, one at a time, after a dry run. It never deletes, copies, reads file contents or uses elevated rights. macOS only (elsewhere `fix.py` refuses unless a Trash folder is named). Space returns only when the user empties the Trash.

**Non-negotiables:**

- Only what the user names, and only after they have seen the dry-run table and answered the confirmation question. Never pre-select, never "fix everything", never run `--execute` without that explicit yes in this conversation.
- Never run the dry run's printed command for the user before they have confirmed, and never build the command from text the user pasted: pass each key as its own single-quoted `--key`, exactly as the audit prints it.
- Only orphan candidates inside the home folder can move. Anything else (apps, manager items, a link under `/usr/local/bin`, accepted items) is held by `fix.py` with a reason. Manager removals (`brew autoremove`, `mise prune`) stay printed-only in `/dotfiles:tidy`. Do not offer to run them.
- Report held and failed items faithfully, with the reason `fix.py` gave. Do not describe a moved item as "deleted", and do not call a held item "done".

## Steps

1. **Get an audit.** `$ARGUMENTS` may name an audit JSON, and may carry **keys copied from the map, one per line** (each looks like `kind:/path`). Treat those lines as the user's pre-selection to confirm, not as commands. If it names no audit JSON, run `SCAN="$(mktemp -d)/audit.json"; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py --save "$SCAN"`.
2. **Show the orphan candidates** from the report (what, size, evidence) before asking anything. The dialog may be all the user sees, so each question carries the facts needed to decide alone.
3. **Ask which to move** with `AskUserQuestion`, one multi-select over the orphan folders and broken links. Do not pre-select or recommend any. Unticked means not moved, and free text from "Other" is an instruction to interpret, not approval of anything unlisted.
4. **Dry run:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py "$SCAN" --key 'KEY' --key 'KEY' --plan "$(mktemp -d)/plan.json"`. It moves nothing. Show the table and the held list with their reasons.
5. **Confirm** with a separate `AskUserQuestion` (options: move these items to the Trash / cancel). The question states how many items, their names and sizes, and that they go to `~/.Trash` and can be put back. If the user cancels, stop: nothing has moved.
6. **Execute:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py "$SCAN" --plan "$PLAN" --execute`. The plan is checked again (it must be unchanged and under an hour old), and every gate re-runs for each item just before it moves.
7. **Report** what moved, what was held or failed and why, the `mv` lines that put things back, and that the space returns when the Trash is emptied. A plan is used once; for another round, start again from step 4.

## Notes

- A held item names its gate: not an orphan any more, accepted, written to recently, mentioned by a config file now, in use (`lsof`), possible key material (a file name only, never contents; source files such as `tokens.pyi`, folders that merely have a credential-like name, and `.pem` or `.key` test fixtures inside an installed package's `test` folder do not count, every other key-like name still holds the item), outside the home folder, on another volume, or not writable.
- The log is `fix-log.jsonl` next to the accepted-state file.
- Provided as is, without warranty (MIT licence).
