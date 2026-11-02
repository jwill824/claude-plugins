# Safety rules for reclaiming space

Read this when deciding something the scanner did not label clearly. Every rule exists because the opposite loses work that cannot be rebuilt.

## Never recommend removing

- **Anything git tracks.** `git ls-files` inside the path must be empty. A directory named `bin/` or `build/` can hold tracked source even when its siblings are output.
- **Uncommitted changes**: modified tracked files (staged or not), and untracked files that are not git-ignored. Untracked is not the same as disposable: it can be the only copy of a new file, test or script.
- **Commits that exist on no remote branch.** Count them with `git rev-list --count HEAD --not --remotes`. After a prune, branches that were pushed then merged and deleted also look unpushed, so say "cannot confirm" rather than "safe" when the number is non-zero.
- **Credentials and key material**: `.env*`, `.npmrc`, `.netrc`, `*.p12`, `*.pem`, `*.key`, `*.jks`. Never copy them into backups either.
- **A locked worktree, or a repository's main checkout.**

## Git worktrees

`git worktree remove <path>` (no `--force`) deletes only the checkout directory and keeps the branch, so commits survive. It refuses if there are modified or untracked files, which is the check you want. It does delete git-ignored files without asking, so look at what is ignored first: build output, `node_modules` and generated config are fine, but authored local files (design notes, local config, local databases with real data) deserve a backup copy first. Detached worktrees are the exception to "the branch keeps the commits": confirm the commit is on some branch or remote, or the commit becomes unreachable.

## Ignored files inside a repository

`git status --ignored --porcelain` lists them. Regenerable categories (dependency caches, compiled output, logs, generated icons) are safe. Treat unknown ignored content as VERIFY, not SAFE: local databases, key stores, `.spec`/notes folders and local settings files are ignored precisely because they are personal, not because they are disposable.

## Case-insensitive filesystems

On macOS a repository can hold two branches that differ only in case. `git fetch --prune` and `git remote prune` can then fail on the colliding refs. If cleanup involves pruning remote-tracking refs and it fails with a lock error naming a ref, check for case-colliding names before assuming a stale lock file.

## Before and after

- Re-scan immediately before deleting; verify each item again at that moment.
- Prefer a recorded way back: a patch for tracked changes, a tarball for small authored files, a table of branch and commit for removed worktrees.
- Report skipped items and the reason, not just what was removed.

## Machine-wide items (scan_system.py)

- **Staleness never promotes.** Old data is not disposable data: a volume nobody wrote to in 90 days can still be the only copy of a database. Activity (ACTIVE, RECENT, STALE, UNKNOWN) may hold a SAFE item back or help the user judge a VERIFY item; it must never move an item from VERIFY to SAFE.
- **"Reclaimable" figures from tools are not safe lists.** `docker system df` counts images that stopped containers still use, and `brew cleanup` also removes superseded versions, so size from the tool's dry run or an explicit list and say when a number is an upper bound.
- **Explicit beats re-selecting.** `docker image prune -a`, `container prune` and `volume prune` choose their targets when they run, which can differ from what was reviewed (another session may have pulled an image a minute ago). Emit named images, containers and volumes, keep an age floor, and never pass `-f` to `docker image rm`.
- **Unused is not orphaned.** A Docker volume with no container is only "orphaned" if its compose project also has no containers; a volume whose newest file is recent was probably attached to a container removed minutes earlier, for example by a prune you ran.
- **Process-name matching is a guess; open files are evidence.** Use one system-wide `lsof` prefix match plus command lines that name the path. If `lsof` fails, hold every SAFE item instead of assuming nothing is in use.
- **A "cannot tell" is not a "no".** An unreadable `.git` inside a tool cache is recorded as unknown and does not demote; definite uncommitted or unpushed work does (KEEP for authored data such as `~/.claude`, VERIFY elsewhere).
- Space freed inside a VM-backed Docker returns to the Mac only after the sparse disk image is trimmed (`fstrim`), and a small gap between image size and in-VM use is filesystem overhead, not recoverable space.
- **Re-verify at the moment of action, with the argv you verified.** The scan is a snapshot. The executor rebuilds each command from the path it just checked, asks tools where they will act (`pnpm store path`, `uv cache dir`) and holds on any mismatch, and re-checks size, mtime, open files, recent writes, git state and key material. A check only holds an item; the executor never clears a block the scan recorded.
- **One command can change another item.** `pnpm store prune` empties `~/Library/Caches/pnpm` too. An item that shrank or vanished since the scan is logged `already-reclaimed` and not counted, and the scan's total is an upper bound.
- **Report two numbers.** Folder shrinkage (`du`) and free-space change (`df`) differ on APFS because clones and hard links share blocks; show both and flag a large gap.
- **Persist "keep", never "remove".** A saved keep only ever makes a later scan more cautious. A saved remove would be approval for a state nobody looked at, and would break the rule that nothing deletes unattended. Approval for removal is given per run, for named keys.
- **A keep ruling lapses; it does not rot.** It expires, and it lapses when the item has grown a lot, so a 2 GB keep cannot quietly become 40 GB. An unreadable ruling file stops the executor instead of being read as "nothing is kept".
- **Approval covers a state, not an item.** For VERIFY items the user approved what they were shown, so a change since the scan (size, mtime, a volume written, a simulator used) holds the item, and a group runs only the members that still pass.
- **Prefer reversible removal where one exists.** Reviewed folders go to the Trash rather than being deleted. Simulators and Docker volumes have no such option; say so in the preview and never offer a `-f` that overrides docker's own in-use refusal.
