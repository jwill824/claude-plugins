# Changelog

# [1.0.0](https://github.com/jwill824/claude-plugins/compare/disk-reclaim-v0.1.0...disk-reclaim-v1.0.0) (2026-11-02)


* feat(disk-reclaim)!: split into per-mode skills ([#5](https://github.com/jwill824/claude-plugins/issues/5)) ([211e4ae](https://github.com/jwill824/claude-plugins/commit/211e4ae31201fab9cbe09fcb7d3d04ecb307903a))


### BREAKING CHANGES

* /disk-reclaim:disk-reclaim is removed, and copying the
skill folder into ~/.claude/skills no longer works.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## disk-reclaim 0.1.0

First packaged release.

- Directory scan and purge (`scan.py`, `reclaim.py`), git-aware, with worktree handling.
- Machine-wide scan (`scan_system.py`) with tiers, activity classes, gates and an optional HTML treemap.
- Executor (`execute_system.py`) that re-verifies every gate before each command, for SAFE items by bucket and for reviewed items by `--approve KEY`.
- Keep rulings (`decisions.py`) that lapse on expiry or growth.
- `status` mode and an optional quiet session-start hook.
- Multiple-choice review flow.
