---
name: system
description: Scan the whole machine for storage outside any project folder: tool caches, Xcode and simulators, Android emulators, Docker or Colima volumes and images, app caches, and Claude working data. Use when the user asks why the whole disk or volume is full, what the System Data, Docker or simulator space is, or runs /disk-reclaim:system. Read-only: it tiers each item (SAFE, VERIFY, KEEP), marks it in use or stale, and can write an HTML treemap. macOS only.
argument-hint: "[--html FILE] [--save-state]"
---

# system

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`; shared guidance is in `${CLAUDE_PLUGIN_ROOT}/references/`.

This skill only looks. It removes nothing, and it cannot: removal belongs to `/disk-reclaim:run` and `/disk-reclaim:review`, which the user starts by typing them.

Read `${CLAUDE_PLUGIN_ROOT}/references/tiers-and-gates.md` before presenting: each item carries a tier, an activity class and the gates that ran.

When the question is about the whole machine ("why is my disk full", "what is the System Data / Docker / simulator space"), run the system scanner instead of pointing `scan.py` at the home folder:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan_system.py [--html "$(mktemp -d)/report.html"] [--json] [--min-mb 100] [--active-days 7] [--stale-days 30]
```

Pass the user's arguments (`$ARGUMENTS`) through. Add `--save-state` to feed `/disk-reclaim:status`. Read-only, about a minute. It reports tool caches, Xcode and simulators, Android emulators, Docker/Colima, `~/.claude` data and `~/Library/Caches`, with bar charts, a tier by activity matrix and, with `--html`, a self-contained treemap page (open it locally; publish it as an Artifact only if the user asks, since item names can reveal customer or project names). Tools that are not installed are skipped and noted.

Present SAFE in three groups: stale (reclaim freely), recently used (reclaimable, but the user will feel the rebuild) and blocked right now (say what holds it, for example live `uvx` processes holding the uv cache lock). Docker commands list explicit images, containers and volumes with an age floor and no `-f`, so what was reviewed is what runs. Sizes come from the tool's own dry run where one exists (`brew cleanup -n`) and carry a `~` when they are an upper bound.

## Next steps

Tell the user what to type next, since you cannot start these yourself:

- **SAFE items:** `/disk-reclaim:run` (dry run first, then execute after approval).
- **VERIFY items:** `/disk-reclaim:review` (a decision per group; remembers what they keep).

## Notes

- **Platform.** The machine-wide scan is macOS-only and exits with a message elsewhere. Each tool in the scan (Xcode, Docker or Colima, Android, uv, pnpm, Homebrew) is skipped when it is not installed. It needs `lsof` and `ps`: every SAFE item is held if `lsof` fails.
- Standard library Python 3 only.
