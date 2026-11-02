---
name: disk-reclaim
description: Report how much storage the current directory (and its subdirectories) uses, break it down by folder and by category, and work out which of it can be safely reclaimed. Read-only by default; it only proposes cleanup and never deletes without explicit confirmation. Use this whenever the user asks what is using disk space, why a project or folder is huge, how to free up space or "clean up" a directory, how big node_modules, build output, caches, logs or git worktrees have grown, or whether something is safe to delete, even if they never say "disk usage". Git-aware, so it will not suggest removing tracked files, uncommitted changes or unpushed commits. Also covers machine-wide storage outside any project (tool caches, Xcode and simulators, Android emulators, Docker/Colima volumes and images, app caches, Claude working data) when the user asks why the whole disk or volume is full, with each item tiered and marked in-use versus stale, and an optional HTML treemap.
argument-hint: "[status | system [--html FILE] | run [--bucket SAFE-USED] | review | DIR]"
---

# disk-reclaim


## Arguments

Read the first word of the arguments to pick a mode. Anything else is a directory. Read the listed reference **before acting**: it holds the commands and the checks for that mode.

| Arguments | Do | Read first |
|---|---|---|
| (none) or a path `DIR` | Directory scan of the current directory or `DIR`: sections 1 to 4 below | (this file) |
| `status` | `scan_system.py --status`; report it and say what, if anything, to do next | `references/status.md` |
| `system [--html FILE]` | Machine-wide scan | `references/system.md` |
| `run [--bucket SAFE-USED] [--id ID]` | `execute_system.py` dry run from a fresh `scan_system.py --json`; show it, and execute only after the user approves the listed items | `references/system.md`, `references/prompts.md` |
| `review` | Walk the user through the items that need a decision (VERIFY): rule on each group with multiple-choice prompts, remember what they keep, run what they approve | `references/review.md`, `references/prompts.md` |

Paths above are relative to `${CLAUDE_SKILL_DIR}`. The rules that hold in every mode, and so stay in this file: scanning is read-only; show a dry run before anything is removed; the user approves what is named; every item is re-checked immediately before it runs; never run `execute_system.py --execute` or `reclaim.py --execute` without those steps.

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


## Approvals

In an interactive session, ask for decisions with the `AskUserQuestion` tool (details and rules in `references/prompts.md`), and never from a hook, a schedule or any run where the tool is unavailable: report and stop.

## Notes

- Sizes are block usage from `du`; hard-linked or copy-on-write files (pnpm stores, APFS clones) can make real savings smaller.
- **Platforms.** Directory mode (`scan.py`, `reclaim.py`) and `--status` work on macOS and Linux. The machine-wide scan and executor are macOS-only and exit with a message elsewhere. Each tool in the scan (Xcode, Docker or Colima, Android, uv, pnpm, Homebrew) is skipped when it is not installed.
- **This tool deletes data.** It is read-only until `--execute` or `--approve ... --execute` is given, and every item is re-checked immediately before it runs, but the user still approves what is removed. Provided as is, without warranty (MIT licence).
- Standard library Python 3 only; needs `du`, and `git` for the repository-aware checks. `scan.py` and `reclaim.py` work on macOS and Linux with no configuration and no paths baked in; `scan_system.py` and `execute_system.py` know macOS tool locations and need `lsof` and `ps` (every SAFE item is held if `lsof` fails).
