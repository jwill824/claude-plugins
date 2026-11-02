# Changelog

## disk-reclaim 0.1.0

First packaged release.

- Directory scan and purge (`scan.py`, `reclaim.py`), git-aware, with worktree handling.
- Machine-wide scan (`scan_system.py`) with tiers, activity classes, gates and an optional HTML treemap.
- Executor (`execute_system.py`) that re-verifies every gate before each command, for SAFE items by bucket and for reviewed items by `--approve KEY`.
- Keep rulings (`decisions.py`) that lapse on expiry or growth.
- `status` mode and an optional quiet session-start hook.
- Multiple-choice review flow.
