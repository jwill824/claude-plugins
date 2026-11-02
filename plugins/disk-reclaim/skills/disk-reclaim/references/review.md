# Reviewing VERIFY items

VERIFY holds most of the space on a typical machine, and each item needs a human decision. The flow for `review`:

1. **Scan and show.** `scan_system.py --json --save-state > "$SCAN"`, then show the VERIFY groups in a table: size, count, activity, evidence. Items marked KEEP by an earlier ruling are listed separately, not asked about again.
2. **Rule on each group** with the multiple-choice prompts in `prompts.md` (read it first): Remove, Keep or Inspect first. Ask per item only for the big ones.
3. **Inspect first** means look before deciding: sample a Docker volume with `docker run --rm -v NAME:/v alpine ls /v`, check which projects pin a Maven version, see whether an app is running. Report what you found and ask again.
4. **Remember keeps.** `decisions.py keep --scan "$SCAN" --id ID [--note WHY]` records a ruling (90 days by default; it also lapses if the item grows past 2x and +1 GB, or +5 GB). Only "keep" is ever stored: a saved "remove" would become approval for a later scan nobody reviewed. `decisions.py list` and `forget KEY` manage them.
5. **Dry run what was approved.** `execute_system.py "$SCAN" --approve KEY [--approve KEY ...]`. The key is the item's identity (shown in the output and in `--json`); ids shift between scans, keys do not. Show the result, including anything held, then ask for final approval with the commands in a preview.
6. **Execute** with `--execute --log "$LOG"`, report the two numbers (folder shrink and free space), and run the Colima trim last if volumes were removed.

What `--approve` runs, and what happens to the data:

| Item | Action | Reversible |
|---|---|---|
| App cache folder in `~/Library/Caches`, `~/.npm/_npx`, an Android AVD | Moved to `~/.Trash` | Yes, but the space returns only when the Trash is emptied |
| iOS simulator | `xcrun simctl delete UDID` | No |
| Docker volume or group | `docker volume rm` (no `-f`) | No |
| Simulator runtimes, Xcode archives, `~/.claude` data, `~/.m2` folders, anything else | Refused or unsupported: do it by hand | n/a |

Each approved item is re-checked immediately before it runs, and a failed check holds it. Folders hold if their size or modification time changed since the scan (the user approved a particular state), a file was written in the last 10 minutes, a process has a file open or names the path, the app looks to be running, or a git repository or key material is inside. Simulators hold if booted or used since the scan. Docker volumes are checked one by one: any that a container now mounts, that was written since the scan, or that has a keep ruling is dropped, and the rest still run, so the result can be `partial`. A live keep ruling refuses an item even when it is approved, and an unreadable ruling file stops the whole run, because "cannot read the keep list" must not be read as "nothing is kept".
