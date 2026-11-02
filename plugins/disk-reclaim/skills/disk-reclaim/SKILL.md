---
name: disk-reclaim
description: Report how much storage the current directory (and its subdirectories) uses, break it down by folder and by category, and work out which of it can be safely reclaimed. Read-only by default; it only proposes cleanup and never deletes without explicit confirmation. Use this whenever the user asks what is using disk space, why a project or folder is huge, how to free up space or "clean up" a directory, how big node_modules, build output, caches, logs or git worktrees have grown, or whether something is safe to delete, even if they never say "disk usage". Git-aware, so it will not suggest removing tracked files, uncommitted changes or unpushed commits. Also covers machine-wide storage outside any project (tool caches, Xcode and simulators, Android emulators, Docker/Colima volumes and images, app caches, Claude working data) when the user asks why the whole disk or volume is full, with each item tiered and marked in-use versus stale, and an optional HTML treemap.
argument-hint: "[status | system [--html FILE] | run [--bucket SAFE-USED] | review | DIR]"
---

# disk-reclaim

## Arguments

Read the first word of the arguments to pick a mode. Anything else is a directory.

| Arguments | Do |
|---|---|
| (none) or a path `DIR` | Directory scan of the current directory or `DIR`: sections 1 to 4 |
| `status` | `scan_system.py --status`; report it and say what, if anything, to do next. Section "Status mode" |
| `system [--html FILE]` | Machine-wide scan: section 5 |
| `run [--bucket SAFE-USED] [--id ID]` | `execute_system.py` dry run from a fresh `scan_system.py --json`; show it, and execute only after the user approves the listed items: section 5 |
| `review` | Walk the user through the items that need a decision (VERIFY): rule on each group with multiple-choice prompts, remember what they keep, run what they approve. Section "Reviewing VERIFY items" |


Answer two questions about a directory tree: **where is the space going**, and **what can be given back without losing anything that cannot be rebuilt**. Analysis is read-only. Deleting is a separate, confirmed step.

## 1. Run the scanner

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py [DIR] [--top 15] [--min-mb 20] [--max-depth 6]
```

`DIR` defaults to the current directory. It prints markdown: largest subdirectories, reclaimable items grouped by safety tier and category, git worktrees, large files, and proposed (not executed) commands. Add `--json` if you need to post-process, `--min-mb 0 --top 200` to see small items.

On a very large tree the scan can take a few minutes (it honours `--max-seconds` and says so if it stopped early). If the user points it at a home directory or `/`, narrow it first (for example the projects folder) because a whole-disk scan mostly finds things nobody wants removed.

## 2. Read the result, then add judgment

The script classifies; you decide what to say. Present a short summary first (total, free space, the few biggest wins), then the tables. Keep it compact: sizes human-readable, top N only.

Tiers, and why they exist:

| Tier | Meaning | How to treat it |
|---|---|---|
| **SAFE** | Derived output (build dirs, `node_modules`, caches), git-ignored, no tracked files inside, a manifest next to it to rebuild from | Offer to remove; cost is only a rebuild |
| **VERIFY** | Probably reclaimable but a human should look: untracked-not-ignored dirs, logs, venvs, large files, clean-and-pushed worktrees | Explain the doubt, ask before including |
| **KEEP** | Contains tracked files, possible credentials or key material, uncommitted changes, commits that exist on no remote, locked or main worktrees | Never propose removal |

Names like `build`, `bin`, `dist` and `vendor` are source in some repos and output in others, which is why the scanner demands git-ignored status plus a nearby manifest before calling them SAFE. If the result looks surprising (a "SAFE" item you doubt, a huge KEEP), check it yourself rather than repeating the label. Read `references/safety-rules.md` when deciding anything beyond the script's labels, especially worktrees and ignored files.

## 3. Purging SAFE items (the built-in purge option)

After the report, offer to purge the SAFE items. Ask which categories (build caches, build output, dependency caches) and confirm the policy, because it is a judgment about the user's workflow, not a fact: by default the purge keeps build output in **high-traffic checkouts** (a normal clone, and any worktree named `main`) so they stay ready to build and run, and clears the same output from linked worktrees and other low-traffic copies, where it only takes up space.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py DIR --json > /tmp/scan.json
python3 ${CLAUDE_SKILL_DIR}/scripts/reclaim.py /tmp/scan.json --categories "build cache,build output" --exclude some/path   # dry run
python3 ${CLAUDE_SKILL_DIR}/scripts/reclaim.py /tmp/scan.json --categories "build cache,build output" --exclude some/path --execute
```

`reclaim.py` is a dry run unless `--execute` is given. It re-verifies every item at that moment (still git-ignored, no tracked files, no key material, manifest beside it), skips any checkout with a process running in it, removes only directories with known derived-output names, writes a log, and reports the free space gained. Always show the user the dry-run table first, and exclude any single item they are unsure of (a huge `build/` folder can be a packaged deliverable). Use `--no-protect` only if the user asks to clear the high-traffic checkouts too.

## 4. Other deletions (only after the user says yes)

1. Show the exact commands and the total space they free; ask which items to include.
2. **Re-check just before acting.** State changes (other sessions, running builds). Re-verify each item is still clean and still ignored; skip and report any that changed.
3. Prefer reversible actions: `git worktree remove <path>` without `--force` (it refuses on uncommitted work and keeps the branch), a patch or tarball backup before discarding anything small but authored, and `rm -rf -- <path>` only for regenerable output.
4. Never use `--force`, `git clean -x`, `git reset --hard` or delete branches unless the user explicitly asks for that exact thing.
5. Write multi-step cleanups as a bash script file rather than long interactive one-liners; interactive zsh does not word-split unquoted variables and aliases such as `g` can shadow helpers.
6. Afterwards report what was removed, what was skipped and why, and the space actually freed (`du` again).

## 5. Machine-wide scan (storage outside any project folder)

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

It runs only SAFE, unblocked items whose command matches an allow-listed shape. It rebuilds each command from the path it verified rather than running the stored string, and asks tools like `pnpm store path` where they will act, holding the item if that differs from the scanned path. `npm cache clean --force` is allow-listed because npm requires the flag. It checks path sanity, size and mtime against the scan fingerprint, a targeted `lsof`, process command lines, writes within the stale window, git state and key material, and any failed check holds the item; nothing a check says can release an item the scan held. An item that shrank or vanished since the scan, usually because an earlier command already cleaned it (`pnpm store prune` also empties `~/Library/Caches/pnpm`), is logged `already-reclaimed` and not counted. The log records both how much the folder shrank and how much free space moved, because APFS clones and hard links make them differ. Free space is read after waiting for it to settle (up to 30 seconds, ending early once it has caught up or stopped moving), because some deletes finish in the background: `simctl delete` reported under 1 GB of a 10 GB gain when read immediately. The headline is the change over the whole run; per-item figures are approximate. Docker volume removals free space inside the Colima VM, so they show no Mac gain until the trim. SAFE Docker items (unused images, stopped containers, build cache) are unsupported by the executor: review and run the listed commands yourself. Docker volumes are VERIFY items and run only through `--approve`, below. Space freed inside the Colima VM only reaches the Mac after `colima ssh -- sudo fstrim -av`, so run it last. Follow section 4.

After editing `scan_system.py`, `execute_system.py` or `decisions.py`, run the five suites in `${CLAUDE_SKILL_DIR}/tests/` with `python3 -B`:

| Suite | Checks |
|---|---|
| `test_gates.py` | Builds a fake home folder; every gate fires (open file, process command line, key material, unpushed work, symlink, shallow path) and staleness never promotes an item |
| `test_execute.py` | The executor against a fake home: each of those holds, plus stale scan files, changed or shrunken folders, mismatched commands and unsupported items, leave the data in place |
| `test_status.py` | Status mode, saved state and the quiet-if-ok behaviour for hooks |
| `test_decisions.py` | Only "keep" is stored; rulings lapse on expiry or growth; a broken file fails loudly; a ruling only moves an item toward caution |
| `test_verify.py` | `--approve` against fake `docker`, `colima` and `xcrun` programs: nothing runs unapproved, every re-check holds, groups drop changed members and run the rest |

### Reviewing VERIFY items

VERIFY holds most of the space on a typical machine, and each item needs a human decision. The flow for `review`:

1. **Scan and show.** `scan_system.py --json --save-state > "$SCAN"`, then show the VERIFY groups in a table: size, count, activity, evidence. Items marked KEEP by an earlier ruling are listed separately, not asked about again.
2. **Rule on each group** with the multiple-choice prompts above: Remove, Keep or Inspect first. Ask per item only for the big ones.
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

### Status mode, hooks and schedules

`scan_system.py --status` answers "do I need to do anything?" in about a second: free space, the trend over the last two weeks, the last saved scan and the last cleanup. It scans nothing and changes nothing, and exits 1 only when space is low (under `--warn-free-gb` or `--warn-free-pct`). A missing scan, or one older than `--max-scan-age-days`, is shown but counts as a problem only when space is also low, so a hook that runs every session stays silent until it matters. State lives in `$DISK_RECLAIM_STATE`, else `$XDG_STATE_HOME/disk-reclaim`, else `~/.local/state/disk-reclaim`. A scan feeds it only when run with `--save-state`, and every `execute_system.py --execute` records its result.

Automate the cheap parts and keep deletion interactive:

| Layer | Runs | Does |
|---|---|---|
| Hook (session start) | `scan_system.py --status --quiet-if-ok` | Prints a line only when something needs attention. Never scan or execute from a hook: it is unattended, runs while other sessions hold caches open, and deletion needs approval. |
| Schedule (for example weekly, from cron or launchd on the machine itself) | `scan_system.py --json --save-state > "$SCAN"` | Keeps the scan summary fresh so the status is truthful. Read-only. A cloud scheduled agent cannot see the local disk. |
| Interactive | `/disk-reclaim`, then `execute_system.py` | Review the tiers, approve items, execute with re-verified gates. |

Do not schedule `--execute`: tiers and gates describe one moment, and a cache that was stale last week may be in use today.

Installing the plugin adds the session-start hook for you (`hooks/hooks.json`, quiet unless space is low). If the skill was copied into `~/.claude/skills/` instead, add this to `~/.claude/settings.json` under `hooks.SessionStart` yourself, with the real path of the folder:

```json
{"matcher": "startup", "hooks": [{"type": "command", "timeout": 10,
  "command": "python3 /path/to/disk-reclaim/scripts/scan_system.py --status --quiet-if-ok || true"}]}
```

### Asking with multiple-choice prompts

In an interactive session, use the `AskUserQuestion` tool for decisions with three or more choices. It makes each approval explicit instead of a bare "yes" to a long message. For one or two choices, a plain question is faster. Never ask from a hook, a schedule or any run where the tool is unavailable: report and stop.

Shape of the tool: 1 to 4 questions per call, 2 to 4 options each, single or multi-select, and an "Other" free-text choice is added automatically. A header is at most 12 characters.

| Moment | Question |
|---|---|
| What to run | One multi-select over the groups that have items (for example SAFE stale, SAFE recently used, each VERIFY group). Unticked means skipped. |
| Ruling on a review group | One single-select per group, up to 4 groups per call: **Remove**, **Keep**, **Inspect first**. Put the group name and total in the question. |
| Large single items | One question per item over 1 GB, same options. Smaller items stay in their group. |
| Final approval | One single-select: run these N items / change the selection / cancel. Use the option `preview` to list the exact commands and the total, since previews work on single-select only. |

Rules that keep the prompts from weakening the safety model:

1. **Show the dry-run table in the reply first.** The dialog may be all the user sees, so each question also carries the facts needed to decide alone: size, last activity, what holds it and what would be rebuilt. Never ask about something the user has not been shown.
2. **An answer covers only the items named in that question.** Free text from "Other" is an instruction to interpret, not approval of anything unlisted. A dismissed dialog or no answer means no.
3. **No option is pre-recommended when it deletes data.** Do not mark Remove as the recommended choice, and never offer "remove everything" over VERIFY items that were not each listed.
4. **Group before asking.** With more than a handful of items, group by kind (app caches, simulators, Docker volumes, Maven versions) and ask per group, then per item only for the big ones. A question with 30 options is a wall, not a choice.
5. **The executor still re-verifies.** Approval says what the user wants, never that it is still safe. Run it through the executor, and report anything it holds.

## Notes

- Sizes are block usage from `du`; hard-linked or copy-on-write files (pnpm stores, APFS clones) can make real savings smaller.
- **Platforms.** Directory mode (`scan.py`, `reclaim.py`) and `--status` work on macOS and Linux. The machine-wide scan and executor are macOS-only and exit with a message elsewhere. Each tool in the scan (Xcode, Docker or Colima, Android, uv, pnpm, Homebrew) is skipped when it is not installed.
- **This tool deletes data.** It is read-only until `--execute` or `--approve ... --execute` is given, and every item is re-checked immediately before it runs, but the user still approves what is removed. Provided as is, without warranty (MIT licence).
- Standard library Python 3 only; needs `du`, and `git` for the repository-aware checks. `scan.py` and `reclaim.py` work on macOS and Linux with no configuration and no paths baked in; `scan_system.py` and `execute_system.py` know macOS tool locations and need `lsof` and `ps` (every SAFE item is held if `lsof` fails).
