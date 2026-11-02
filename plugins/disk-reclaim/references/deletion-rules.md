# Deletion rules

These apply to every removal, in every mode.

1. Show the exact commands and the total space they free; ask which items to include.
2. **Re-check just before acting.** State changes (other sessions, running builds). Re-verify each item is still clean and still ignored; skip and report any that changed.
3. Prefer reversible actions: `git worktree remove <path>` without `--force` (it refuses on uncommitted work and keeps the branch), a patch or tarball backup before discarding anything small but authored, and `rm -rf -- <path>` only for regenerable output.
4. Never use `--force`, `git clean -x`, `git reset --hard` or delete branches unless the user explicitly asks for that exact thing.
5. Write multi-step cleanups as a bash script file rather than long interactive one-liners; interactive zsh does not word-split unquoted variables and aliases such as `g` can shadow helpers.
6. Afterwards report what was removed, what was skipped and why, and the space actually freed (`du` again).
