# Changing the scripts

After editing `scan_system.py`, `execute_system.py` or `decisions.py`, run the five suites in `plugins/disk-reclaim/tests/` with `python3 -B`:

| Suite | Checks |
|---|---|
| `test_gates.py` | Builds a fake home folder; every gate fires (open file, process command line, key material, unpushed work, symlink, shallow path) and staleness never promotes an item |
| `test_execute.py` | The executor against a fake home: each of those holds, plus stale scan files, changed or shrunken folders, mismatched commands and unsupported items, leave the data in place |
| `test_status.py` | Status mode, saved state and the quiet-if-ok behaviour for hooks |
| `test_decisions.py` | Only "keep" is stored; rulings lapse on expiry or growth; a broken file fails loudly; a ruling only moves an item toward caution |
| `test_verify.py` | `--approve` against fake `docker`, `colima` and `xcrun` programs: nothing runs unapproved, every re-check holds, groups drop changed members and run the rest |
