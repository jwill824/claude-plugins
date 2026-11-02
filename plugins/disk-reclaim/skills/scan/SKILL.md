---
name: scan
description: Report how much storage a directory or project folder uses, break it down by folder and by category, and work out which of it can be safely reclaimed (build output, node_modules, caches, logs, git worktrees). Use when the user asks why a project or folder is huge, how big node_modules or build output has grown, how to clean up a directory, or whether something in it is safe to delete. Read-only by default; it never deletes without explicit confirmation. Git-aware: it will not suggest removing tracked files, uncommitted changes or unpushed commits. For the whole machine (System Data, Docker, simulators), use /disk-reclaim:system instead.
argument-hint: "[DIR]"
---

# scan

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`; shared guidance is in `${CLAUDE_PLUGIN_ROOT}/references/`.

Scan the directory in `$ARGUMENTS` (default: the current directory). Answer two questions: **where is the space going**, and **what can be given back without losing anything that cannot be rebuilt**. Analysis is read-only. Deleting is a separate, confirmed step.

**Non-negotiables** (full text in `${CLAUDE_PLUGIN_ROOT}/references/deletion-rules.md`, read it before removing anything):

- Show a dry run first. Remove nothing until the user has approved the items by name.
- Re-check each item immediately before it runs; skip and report any that changed.
- Prefer reversible actions. Never use `--force`, `git clean -x` or `git reset --hard`, and never delete branches, unless the user asks for that exact thing.
- Never propose KEEP items, and never promote a VERIFY item because it is old.

## 1. Run the scanner

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py [DIR] [--top 15] [--min-mb 20] [--max-depth 6]
```

`DIR` defaults to the current directory. It prints markdown: largest subdirectories, reclaimable items grouped by safety tier and category, git worktrees, large files, and proposed (not executed) commands. Add `--json` if you need to post-process, `--min-mb 0 --top 200` to see small items.

On a very large tree the scan can take a few minutes (it honours `--max-seconds` and says so if it stopped early). If the user points it at a home directory or `/`, narrow it first (for example the projects folder) because a whole-disk scan mostly finds things nobody wants removed.

## 2. Read the result, then add judgment

The script classifies; you decide what to say. Present a short summary first (total, free space, the few biggest wins), then the tables. Keep it compact: sizes human-readable, top N only.

Read `${CLAUDE_PLUGIN_ROOT}/references/tiers-and-gates.md` for what SAFE, VERIFY and KEEP mean and how to treat each. If the result looks surprising (a "SAFE" item you doubt, a huge KEEP), check it yourself rather than repeating the label.

## 3. Purging SAFE items (the built-in purge option)

After the report, offer to purge the SAFE items. Ask which categories (build caches, build output, dependency caches) and confirm the policy, because it is a judgment about the user's workflow, not a fact: by default the purge keeps build output in **high-traffic checkouts** (a normal clone, and any worktree named `main`) so they stay ready to build and run, and clears the same output from linked worktrees and other low-traffic copies, where it only takes up space.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py DIR --json > /tmp/scan.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/reclaim.py /tmp/scan.json --categories "build cache,build output" --exclude some/path   # dry run
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/reclaim.py /tmp/scan.json --categories "build cache,build output" --exclude some/path --execute
```

`reclaim.py` is a dry run unless `--execute` is given. It re-verifies every item at that moment (still git-ignored, no tracked files, no key material, manifest beside it), skips any checkout with a process running in it, removes only directories with known derived-output names, writes a log, and reports the free space gained. Always show the user the dry-run table first, and exclude any single item they are unsure of (a huge `build/` folder can be a packaged deliverable). Use `--no-protect` only if the user asks to clear the high-traffic checkouts too.

## 4. Other deletions (only after the user says yes)

Read `${CLAUDE_PLUGIN_ROOT}/references/deletion-rules.md` and follow it: show the exact commands and the space they free, re-check just before acting, prefer reversible actions, and report what was removed, what was skipped and why.

## Notes

- Sizes are block usage from `du`; hard-linked or copy-on-write files (pnpm stores, APFS clones) can make real savings smaller.
- Works on macOS and Linux with no configuration and no paths baked in. Standard library Python 3 only; needs `du`, and `git` for the repository-aware checks.
- **This tool deletes data** once `reclaim.py --execute` is given, after the dry run and the user's approval. Provided as is, without warranty (MIT licence).
