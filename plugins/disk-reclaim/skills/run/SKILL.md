---
name: run
description: Run the SAFE items from a fresh machine-wide scan after the user approves them: a dry run first, then execute with every gate re-checked right before each command. Only on the user's explicit command /disk-reclaim:run.
argument-hint: "[--bucket SAFE-USED] [--id ID]"
disable-model-invocation: true
---

# run

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`; shared guidance is in `${CLAUDE_PLUGIN_ROOT}/references/`.

The user's arguments: `$ARGUMENTS` (for example `--bucket SAFE-USED` or `--id ID`; none means the SAFE-STALE bucket). Read `${CLAUDE_PLUGIN_ROOT}/references/tiers-and-gates.md` and `${CLAUDE_PLUGIN_ROOT}/references/prompts.md` first: tiers say what the executor will and will not touch, and the prompts file says how to ask for approval.

**Non-negotiables** (full text in `${CLAUDE_PLUGIN_ROOT}/references/deletion-rules.md`, read it before removing anything):

- Show a dry run first. Remove nothing until the user has approved the items by name.
- Re-check each item immediately before it runs; skip and report any that changed.
- Prefer reversible actions. Never use `--force`, `git clean -x` or `git reset --hard`, and never delete branches, unless the user asks for that exact thing.
- Never propose KEEP items, and never promote a VERIFY item because it is old.

`scan_system.py` only proposes. After the user approves a bucket or specific items, run them with `execute_system.py`, which re-checks every gate immediately before each command (the scan describes one moment). It is a dry run unless `--execute` is given, and needs a scan file less than 4 hours old:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan_system.py --json > "$SCAN"
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/execute_system.py "$SCAN"                                # dry run of SAFE-STALE
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/execute_system.py "$SCAN" --execute --log "$LOG"          # after approval
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/execute_system.py "$SCAN" --bucket SAFE-USED               # recently used items are opt-in
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/execute_system.py "$SCAN" --id <item-id>                  # exactly this item; the default bucket is not added
```

It runs only SAFE, unblocked items whose command matches an allow-listed shape. It rebuilds each command from the path it verified rather than running the stored string, and asks tools like `pnpm store path` where they will act, holding the item if that differs from the scanned path. `npm cache clean --force` is allow-listed because npm requires the flag. It checks path sanity, size and mtime against the scan fingerprint, a targeted `lsof`, process command lines, writes within the stale window, git state and key material, and any failed check holds the item; nothing a check says can release an item the scan held. An item that shrank or vanished since the scan, usually because an earlier command already cleaned it (`pnpm store prune` also empties `~/Library/Caches/pnpm`), is logged `already-reclaimed` and not counted. The log records both how much the folder shrank and how much free space moved, because APFS clones and hard links make them differ. Free space is read after waiting for it to settle (up to 30 seconds, ending early once it has caught up or stopped moving), because some deletes finish in the background: `simctl delete` reported under 1 GB of a 10 GB gain when read immediately. The headline is the change over the whole run; per-item figures are approximate. Docker volume removals free space inside the Colima VM, so they show no Mac gain until the trim. SAFE Docker items (unused images, stopped containers, build cache) are unsupported by the executor: review and run the listed commands yourself. Docker volumes are VERIFY items and run only through `--approve`; see `/disk-reclaim:review`. Space freed inside the Colima VM only reaches the Mac after `colima ssh -- sudo fstrim -av`, so run it last. Follow `${CLAUDE_PLUGIN_ROOT}/references/deletion-rules.md`.

## Notes

- **This tool deletes data.** It is read-only until `--execute` or `--approve ... --execute` is given, and every item is re-checked immediately before it runs, but the user still approves what is removed. Provided as is, without warranty (MIT licence).
- **Platform.** The machine-wide scan and the executor are macOS-only and exit with a message elsewhere. Each tool in the scan (Xcode, Docker or Colima, Android, uv, pnpm, Homebrew) is skipped when it is not installed. They need `lsof` and `ps`: every SAFE item is held if `lsof` fails.
