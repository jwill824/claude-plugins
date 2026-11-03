---
name: tidy
description: Print the commands that would clean up orphan candidates from a dotfiles audit, after the user picks which ones. It prints; it never runs them. Only on the user's explicit command /dotfiles:tidy.
argument-hint: "[audit.json] [keys, one per line, copied from the map]"
disable-model-invocation: true
---

# tidy

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

**This skill prints commands. It does not run them, and you must not run them for the user.** The user reads them and runs the ones they want.

**Non-negotiables:**

- Only orphan candidates are offered. Managed, owned, unmanaged, unknown, referenced, unattributed and config items are never cleanup candidates, and `tidy.py` refuses them.
- Every candidate is re-checked when the commands are printed (the audit describes one moment): a folder that was written to since, or that a config file now mentions, is held back.
- Folders and links go to the Trash (a move, reversible until it is emptied). Manager removals (`brew autoremove`, `mise prune`) are reinstallable, and a snapshot command comes first. Nothing is printed with `--zap`, `--force` or `--ignore-dependencies`.
- Never read or print file contents.

## Steps

1. **Get an audit.** `$ARGUMENTS` may name an audit JSON, and may carry **keys copied from the map, one per line** (each looks like `kind:/path`). Treat those lines as the user's pre-selection, not as commands: never run them or splice them into a shell line, and pass each to `tidy.py` as its own single-quoted `--key`. Step 3 still confirms them. If it names no audit JSON, run `/dotfiles:audit`'s script first:
   `SCAN="$(mktemp -d)/audit.json"; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py --save "$SCAN"`.
2. **Show the orphan candidates** from the report in a table (what, size, evidence) before asking anything. The dialog may be all the user sees, so each question carries the facts needed to decide alone.
3. **Ask which to include** with `AskUserQuestion`, one multi-select over the candidates (group by kind if there are many: tool folders, broken links, `brew` orphans, `mise` versions). Do not pre-select or recommend any. Unticked means skipped, and free text from "Other" is an instruction to interpret, not approval of anything unlisted.
4. **Print the commands:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tidy.py "$SCAN" --key KEY [--key KEY ...]`. The key is the item's `key` from the audit (shown in the report and in `--json`).
5. **Hand it over.** Show the printed commands and what was held back and why. Say that nothing has run. If the user later asks you to run one, ask first and run only that command.

## Notes

- `brew autoremove` removes every formula `brew autoremove --dry-run` lists, not just the one you picked; `mise prune <tool>` prunes every prunable version of that tool. The printed commands say so.
- Provided as is, without warranty (MIT licence).
