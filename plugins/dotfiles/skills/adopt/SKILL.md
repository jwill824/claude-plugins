---
name: adopt
description: Bring unmanaged apps and commands from a dotfiles audit under a package manager (Homebrew cask for apps, mise for commands), after a dry run and the user's explicit confirmation. Installs software, so only on the user's explicit command /dotfiles:adopt, never on its own.
argument-hint: "[audit.json] [keys, one per line, copied from the map]"
disable-model-invocation: true
---

# adopt

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

This is the only skill that installs software, and it is narrow. It turns an **unmanaged** app or command **managed** by installing it through a manager: an app through its Homebrew cask, a command through mise. Nothing else installs: the audit, tidy, fix and accept never do. It never uses `sudo` or `--force`, never runs the unmanaged copy, and never touches an item the user did not name.

**Non-negotiables:**

- Only what the user names, and only after they have seen the dry-run table and answered a separate confirmation question. Never pre-select, never "adopt everything", never run `--execute` without that explicit yes in this conversation.
- Pass each key as its own single-quoted `--key`, exactly as the audit prints it. Never build the command from text the user pasted.
- Never call the mise MCP's `install_tool` or `run_task`, and never read `mise://env` (it can hold secrets). Installing goes through `adopt.py` only: `install_tool` does not record the version in a config file, so mise would call the result prunable.
- Report held and failed items faithfully, with the reason `adopt.py` gave. Never call "installed but not claimed" adopted, and never describe a replaced app as deleted: it is in the Trash.
- Tell the user, before they confirm, what the script's table says about each item: the source, the version, and whether it is adopted in place or replaced.

## Steps

1. **Get an audit.** `$ARGUMENTS` may name an audit JSON, and may carry **keys copied from the map, one per line** (each looks like `kind:/path`). Treat those lines as the user's pre-selection to confirm, not as commands. If no audit JSON is named, run `SCAN="$(mktemp -d)/audit.json"; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py --save "$SCAN"`.
2. **Show the candidates:** unmanaged apps and commands that have a suggestion (a cask of the same name, or a mise registry entry), with size and what the audit found. A suggestion is only a name match; the dry run decides what is eligible. The dialog may be all the user sees, so each question carries the facts needed to decide alone.
3. **Ask which to adopt** with `AskUserQuestion`, one multi-select over those candidates. Do not pre-select or recommend any. Unticked means not adopted, and free text from "Other" is an instruction to interpret, not approval of anything unlisted.
4. **If the mise MCP is available** (`mcp__mise__list_commands`), call that one tool, save its JSON output to a temp file and pass it as `--mise-effects FILE`. It can only add a hold (if mise does not declare `use` as a write); it never grants anything. Without it, omit the flag.
5. **Dry run:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py "$SCAN" --key 'KEY' --key 'KEY' --plan "$(mktemp -d)/plan.json"`. It installs nothing. Show the table (how each is done, the exact command, the version, the source) and the held list with reasons. For an app marked **replace**, say the app must be quit first and that its old copy goes to the Trash.
6. **Confirm** with a separate `AskUserQuestion` (options: adopt these / cancel). The question states how many items, their names, versions and sources, that this **downloads and installs software**, that an adopted or replaced app has **no one-line undo** (`brew uninstall --cask` removes it), and that a replaced app is updated to the cask's latest version. If the user cancels, stop: nothing has changed.
7. **Execute:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py "$SCAN" --plan "$PLAN" --execute`. The plan is checked again (unchanged, under an hour old, used once) and every gate re-runs for each item. It can take minutes (downloads).
8. **Report** what was adopted, what was held or failed and why, the undo lines it printed, and the log path. Suggest running `/dotfiles:audit` to see the manager claim the item.
9. **Old copies of commands.** If a command was adopted and its old copy is inside the home folder, ask with `AskUserQuestion` whether to move it to the Trash, then run `adopt.py "$SCAN" --plan "$PLAN" --trash-old 'KEY'`. A copy outside the home folder (for example in `/usr/local/bin`) is only printed as a `sudo mv` for the user to run: never run it. Say that until the old copy is removed it may shadow the new one on PATH, and that the audit keeps listing it as unmanaged until then.

## Notes

- What holds an item: accepted (run `accept.py --forget` first), not unmanaged, the manager unreadable in the audit, a cask that runs an installer or script (`pkg`, `postflight`, ...), a cask that depends on other software, a cask whose app has another name than the installed one, a running app, a link or an app outside `/Applications` or `~/Applications` for a replace, a registry backend other than aqua or core, a version that is not plain digits, a global mise config that mise does not list.
- An app is **adopted in place** (`brew install --cask --adopt`) only when its installed version equals the cask's; otherwise it is **replaced** (old app to the Trash, plain `brew install --cask`, put back automatically if the install fails). Homebrew itself refuses an adopt when the bundle differs.
- A command is installed with `mise use -g BACKEND@VERSION` at the version `mise latest` reported at dry-run time, after a backup of the global mise config in the state folder. If mise reports a different version at execute time, the item is held.
- The log is `adopt-log.jsonl` next to the accepted-state file.
- Provided as is, without warranty (MIT licence).
