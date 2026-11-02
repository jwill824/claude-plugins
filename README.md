# jw-plugins

A [Claude Code](https://code.claude.com) plugin marketplace. Each plugin lives in `plugins/<name>/` and can bundle skills, hooks and MCP servers.

| Plugin | What it does |
|---|---|
| [`disk-reclaim`](plugins/disk-reclaim) | Finds where disk space is going and reclaims it safely, for a project folder or a whole Mac |

## Install

```
/plugin marketplace add <owner>/<repo>
/plugin install disk-reclaim@jw-plugins
```

From a shell: `claude plugin marketplace add <owner>/<repo>` and `claude plugin install disk-reclaim@jw-plugins`. Update with `claude plugin update disk-reclaim`.

Without plugins, copy `plugins/disk-reclaim/skills/disk-reclaim` into `~/.claude/skills/`. You then add the optional session-start hook by hand (see the skill's `SKILL.md`).

## disk-reclaim

Type `/disk-reclaim` in Claude Code, or just ask why your disk is full.

| Command | What happens |
|---|---|
| `/disk-reclaim` or `/disk-reclaim DIR` | Scans a directory: biggest folders, build output, caches, git worktrees, large files |
| `/disk-reclaim status` | One-second check: free space, trend, last scan and cleanup |
| `/disk-reclaim system` | macOS only. Scans tool caches, Xcode simulators, Android emulators, Docker or Colima volumes, app caches. Optional HTML treemap |
| `/disk-reclaim run` | Runs the items that are safe to remove, after you approve them |
| `/disk-reclaim review` | Walks you through items that need a decision, with multiple-choice prompts, and remembers what you keep |

### How it stays safe

- **Three tiers.** SAFE (regenerable, nothing lost but rebuild time), VERIFY (a person decides), KEEP (never proposed). Old data is never promoted: a volume nobody touched in 90 days can still be the only copy of a database.
- **Gates.** Open files, process command lines, git state (uncommitted or unpushed work), key material and recent activity can each hold an item back. A gate can only make an item more cautious.
- **Dry run first, re-check at the moment of action.** Nothing runs without `--execute`. The executor rebuilds each command from what it just verified and holds the item if anything changed since the scan.
- **You name what to remove.** Items that need review run only when named with `--approve KEY`. Reviewed folders go to the Trash. Simulators and Docker volumes cannot, so they are labelled permanent in the dry run.
- **Only "keep" is remembered.** A saved "remove" would approve a later scan nobody looked at.
- **Nothing runs unattended.** The session-start hook only prints free space, and only when it is low.

### Requirements

Python 3 (standard library only), `du`, and `git` for the repository checks. Directory mode and `status` work on macOS and Linux. The machine-wide scan and the executor are macOS-only and need `lsof` and `ps`. Tools that are not installed are skipped.

State (scan summary, history, your keep rulings) lives in `$DISK_RECLAIM_STATE`, else `$XDG_STATE_HOME/disk-reclaim`, else `~/.local/state/disk-reclaim`. Scan reports name folders on your machine, so don't commit or share them.

### Disclaimer

This tool deletes data. Read the dry run, and read what the preview says before you approve. It is provided as is, without warranty (see `LICENSE`).

### Tests

```
for t in plugins/disk-reclaim/skills/disk-reclaim/tests/test_*.py; do python3 -B "$t"; done
```

The suites use a fake home folder and fake `docker`, `colima` and `xcrun` programs that only log their arguments. Nothing on your machine is touched. CI runs them on macOS.

## Adding a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and put skills in `plugins/<name>/skills/<skill>/SKILL.md` (hooks in `hooks/hooks.json`, MCP servers in `.mcp.json`).
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Run `claude plugin validate .`, then bump the plugin's `version` when you change it, since installs follow that field.

## Licence

MIT, see `LICENSE`.
