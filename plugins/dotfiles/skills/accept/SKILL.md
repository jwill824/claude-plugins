---
name: accept
description: Mark items from a dotfiles audit as accepted (legitimately unmanaged, kept on purpose) so they count as resolved. Writes one small state file; it installs, removes and moves nothing. Only on the user's explicit command /dotfiles:accept.
argument-hint: "[audit.json]"
disable-model-invocation: true
---

# accept

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

Accepting says "I looked at this and it is fine as it is" (a company VPN client, a downloaded tool you chose to keep). It is the only thing in this plugin that writes anything, and what it writes is one private state file (`accepted.json` under `$DOTFILES_STATE`, else `$XDG_STATE_HOME/dotfiles`, else `~/.local/state/dotfiles`). It never installs, removes or moves anything, and only "accept" is ever recorded, never "remove".

**Non-negotiables:**

- Never accept anything the user did not name. Do not pre-select, do not recommend, and do not accept "everything unmanaged".
- Only unresolved items can be accepted (unmanaged, unknown, unattributed, referenced, missing, orphaned). `accept.py` refuses the rest ("already fine").
- Accepting an orphan candidate keeps it: `/dotfiles:tidy` will not offer it afterwards.
- An acceptance is void if the item's class later changes (the audit says so), because the judgement was about a different situation.
- Never read or print file contents.

## Steps

1. **Get an audit.** Use `$ARGUMENTS` if it names an audit JSON. Otherwise: `SCAN="$(mktemp -d)/audit.json"; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py --save "$SCAN"`.
2. **Show the unresolved items** from the report (what, class, evidence, size) before asking anything. The dialog may be all the user sees, so each question carries the facts needed to decide alone.
3. **Ask which to accept** with `AskUserQuestion`, one multi-select over the unresolved items the user is likely to mean (group by kind if there are many). Unticked means not accepted, and free text from "Other" is an instruction to interpret, not approval of anything unlisted.
4. **Ask for a note** (why is this fine as it is, at most 200 characters). One note covers the whole batch; run again for a different reason.
5. **Record it:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/accept.py "$SCAN" --key 'KEY' --key 'KEY' --note 'NOTE'`. Pass **each key as its own single-quoted `--key` argument, exactly as the audit prints it** (keys are paths and can contain spaces or odd characters; never join them into one string and never build the command from a copied line).
6. **Report** what was written, what was refused and why, and the file path. To undo: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/accept.py "$SCAN" --forget --key 'KEY'`.

## Notes

- The next audit shows a coverage line (green, accepted, unresolved) and an "Accepted" section with each note.
- Provided as is, without warranty (MIT licence).
