# Machine-wide scan and executor

Read this before `system` or `run`. Dry run first, execute only after the user approves the listed items, and ask for approval as described in `prompts.md`.

When the question is about the whole machine ("why is my disk full", "what is the System Data / Docker / simulator space"), run the system scanner instead of pointing `scan.py` at the home folder:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan_system.py [--html "$(mktemp -d)/report.html"] [--json] [--min-mb 100] [--active-days 7] [--stale-days 30]
```

Read-only, about a minute. It reports tool caches, Xcode and simulators, Android emulators, Docker/Colima, `~/.claude` data and `~/Library/Caches`, with bar charts, a tier by activity matrix and, with `--html`, a self-contained treemap page (open it locally; publish it as an Artifact only if the user asks, since item names can reveal customer or project names). Tools that are not installed are skipped and noted.

Each item carries a tier, an **activity** class and the **gates** that ran. Read them before presenting:

| Activity | Meaning | Source, in order of trust |
|---|---|---|
| ACTIVE / RECENT / STALE | newest write within `--active-days`, within `--stale-days`, or older | the tool's own timestamp (simulator and runtime `lastUsedAt`, newest file inside a Docker volume), then a bounded `find -mtime` |
| UNKNOWN | no trustworthy timestamp (for example Docker build cache) | treated like in-use, never like stale |

Gates, all recorded per item: path sanity (real directory inside home, not a symlink), **in use** (one system-wide `lsof` plus process command lines, so a held lock or a running tool blocks the item), git state (uncommitted or unpushed work), key material, and activity. Gates only move an item toward caution. **Activity can hold a SAFE item back, it never promotes a VERIFY item to SAFE**: a stale Docker volume or simulator is still the user's call.

Present SAFE in three groups: stale (reclaim freely), recently used (reclaimable, but the user will feel the rebuild) and blocked right now (say what holds it, for example live `uvx` processes holding the uv cache lock). Docker commands list explicit images, containers and volumes with an age floor and no `-f`, so what was reviewed is what runs. Sizes come from the tool's own dry run where one exists (`brew cleanup -n`) and carry a `~` when they are an upper bound.

`scan_system.py` only proposes. After the user approves a bucket or specific items, run them with `execute_system.py`, which re-checks every gate immediately before each command (the scan describes one moment). It is a dry run unless `--execute` is given, and needs a scan file less than 4 hours old:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan_system.py --json > "$SCAN"
python3 ${CLAUDE_SKILL_DIR}/scripts/execute_system.py "$SCAN"                                # dry run of SAFE-STALE
python3 ${CLAUDE_SKILL_DIR}/scripts/execute_system.py "$SCAN" --execute --log "$LOG"          # after approval
python3 ${CLAUDE_SKILL_DIR}/scripts/execute_system.py "$SCAN" --bucket SAFE-USED               # recently used items are opt-in
python3 ${CLAUDE_SKILL_DIR}/scripts/execute_system.py "$SCAN" --id <item-id>                  # exactly this item; the default bucket is not added
```

It runs only SAFE, unblocked items whose command matches an allow-listed shape. It rebuilds each command from the path it verified rather than running the stored string, and asks tools like `pnpm store path` where they will act, holding the item if that differs from the scanned path. `npm cache clean --force` is allow-listed because npm requires the flag. It checks path sanity, size and mtime against the scan fingerprint, a targeted `lsof`, process command lines, writes within the stale window, git state and key material, and any failed check holds the item; nothing a check says can release an item the scan held. An item that shrank or vanished since the scan, usually because an earlier command already cleaned it (`pnpm store prune` also empties `~/Library/Caches/pnpm`), is logged `already-reclaimed` and not counted. The log records both how much the folder shrank and how much free space moved, because APFS clones and hard links make them differ. Free space is read after waiting for it to settle (up to 30 seconds, ending early once it has caught up or stopped moving), because some deletes finish in the background: `simctl delete` reported under 1 GB of a 10 GB gain when read immediately. The headline is the change over the whole run; per-item figures are approximate. Docker volume removals free space inside the Colima VM, so they show no Mac gain until the trim. SAFE Docker items (unused images, stopped containers, build cache) are unsupported by the executor: review and run the listed commands yourself. Docker volumes are VERIFY items and run only through `--approve`; see `review.md`. Space freed inside the Colima VM only reaches the Mac after `colima ssh -- sudo fstrim -av`, so run it last. Follow section 4 of `SKILL.md`.

## Changing the scripts

After editing `scan_system.py`, `execute_system.py` or `decisions.py`, run the five suites in `${CLAUDE_SKILL_DIR}/tests/` with `python3 -B`:

| Suite | Checks |
|---|---|
| `test_gates.py` | Builds a fake home folder; every gate fires (open file, process command line, key material, unpushed work, symlink, shallow path) and staleness never promotes an item |
| `test_execute.py` | The executor against a fake home: each of those holds, plus stale scan files, changed or shrunken folders, mismatched commands and unsupported items, leave the data in place |
| `test_status.py` | Status mode, saved state and the quiet-if-ok behaviour for hooks |
| `test_decisions.py` | Only "keep" is stored; rulings lapse on expiry or growth; a broken file fails loudly; a ruling only moves an item toward caution |
| `test_verify.py` | `--approve` against fake `docker`, `colima` and `xcrun` programs: nothing runs unapproved, every re-check holds, groups drop changed members and run the rest |
