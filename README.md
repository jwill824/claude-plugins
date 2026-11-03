# thingstead

A [Claude Code](https://code.claude.com) plugin marketplace. Each plugin lives in `plugins/<name>/` and can bundle skills, hooks and MCP servers.

| Plugin | What it does |
|---|---|
| [`disk-reclaim`](plugins/disk-reclaim) | Finds where disk space is going and reclaims it safely, for a project folder or a whole Mac |

## Install

```
/plugin marketplace add jwill824/claude-plugins
/plugin install disk-reclaim@thingstead
```

From a shell: `claude plugin marketplace add jwill824/claude-plugins` and `claude plugin install disk-reclaim@thingstead`. Update with `claude plugin update disk-reclaim`.

The plugin route is the only supported one. The skills share scripts and reference files at the plugin root (`${CLAUDE_PLUGIN_ROOT}`), which exists only for installed plugins, so copying a single skill folder into `~/.claude/skills/` no longer works. To try a local checkout without installing, run `claude --plugin-dir plugins/disk-reclaim`.

## disk-reclaim

Five commands, one per job. Ask in plain words ("why is my disk full", "why is this project huge") and Claude picks `system` or `scan`. `run` and `review` delete data, so they only start when you type them.

| Command | What happens |
|---|---|
| `/disk-reclaim:scan [DIR]` | Scans a project folder: biggest folders, build output, caches, git worktrees, large files |
| `/disk-reclaim:status` | One-second check: free space, trend, last scan and cleanup |
| `/disk-reclaim:system` | macOS only, read-only. Scans tool caches, Xcode simulators, Android emulators, Docker or Colima volumes, app caches. Optional HTML treemap |
| `/disk-reclaim:run` | Runs the items that are safe to remove, after a dry run and your approval |
| `/disk-reclaim:review` | Walks you through items that need a decision, with multiple-choice prompts, and remembers what you keep |

Upgrading from 0.1.x: the single `/disk-reclaim:disk-reclaim` skill is gone, replaced by the five above. Your keep rulings and history carry over, since the state folder is unchanged.

### Quick start

1. **Check where you stand:** `/disk-reclaim:status`.
2. **See where the space went:** `/disk-reclaim:system` (read-only, about a minute). For one project, `/disk-reclaim:scan path/to/project`.
3. **Remove the safe items:** `/disk-reclaim:run`. Read the dry run first; nothing is removed until you approve. Items used recently are opt-in with `--bucket SAFE-USED`.
4. **Decide the rest:** `/disk-reclaim:review`. For each group choose Remove, Keep or Inspect first, then approve the dry run. Keeps are remembered for 90 days.
5. **Finish up:** empty the Trash, because moved folders only free space then. If Docker volumes were removed, run `colima ssh -- sudo fstrim -av` last.

What an approved removal does:

| Item | Action | Reversible? |
|---|---|---|
| App cache folders, `~/.npm/_npx`, Android emulators | Moved to the Trash | Yes, until you empty it |
| iOS simulators | `xcrun simctl delete` | No |
| Docker volumes | `docker volume rm`, no `-f` | No |
| Simulator runtimes, Xcode archives, `~/.claude` data, `~/.m2` | Refused: do these by hand | n/a |

### How it stays safe

- **Three tiers.** SAFE (regenerable, nothing lost but rebuild time), VERIFY (a person decides), KEEP (never proposed). Old data is never promoted: a volume nobody touched in 90 days can still be the only copy of a database.
- **Gates.** Open files, process command lines, git state (uncommitted or unpushed work), key material and recent activity can each hold an item back. A gate can only make an item more cautious.
- **Dry run first, re-check at the moment of action.** Nothing runs without `--execute`. The executor rebuilds each command from what it just verified and holds the item if anything changed since the scan.
- **You name what to remove.** Items that need review run only when named with `--approve KEY`. Reviewed folders go to the Trash. Simulators and Docker volumes cannot, so they are labelled permanent in the dry run.
- **Only "keep" is remembered.** A saved "remove" would approve a later scan nobody looked at.
- **Nothing runs unattended.** The session-start hook scans nothing and is silent while space is fine. When free space is low (under 50 GB or 10% free by default; set `DISK_RECLAIM_WARN_FREE_GB` or `DISK_RECLAIM_WARN_FREE_PCT` to change it) it shows a one-line status and asks Claude to offer options, about 150 tokens per session start. Removal still starts only when you type `/disk-reclaim:run` or `/disk-reclaim:review`.

### Requirements

Python 3 (standard library only), `du`, and `git` for the repository checks. Directory mode and `status` work on macOS and Linux. The machine-wide scan and the executor are macOS-only and need `lsof` and `ps`. Tools that are not installed are skipped.

State (scan summary, history, your keep rulings) lives in `$DISK_RECLAIM_STATE`, else `$XDG_STATE_HOME/disk-reclaim`, else `~/.local/state/disk-reclaim`. Scan reports name folders on your machine, so don't commit or share them.

### Disclaimer

This tool deletes data. Read the dry run, and read what the preview says before you approve. It is provided as is, without warranty (see `LICENSE`).

### Tests

```
for t in plugins/disk-reclaim/tests/test_*.py; do python3 -B "$t"; done
```

The suites use a fake home folder and fake `docker`, `colima` and `xcrun` programs that only log their arguments. Nothing on your machine is touched. CI runs them on macOS.

## Adding a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and put skills in `plugins/<name>/skills/<skill>/SKILL.md` (hooks in `hooks/hooks.json`, MCP servers in `.mcp.json`).
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Copy `plugins/disk-reclaim/.releaserc.json` to `plugins/<name>/.releaserc.json` and replace `disk-reclaim` with `<name>` everywhere (tag format, rule scopes, changelog and `plugin.json` paths). That is what releases the plugin.
4. Run `claude plugin validate .`.

## Releases

Releases are automatic on merge to `main`, with [semantic-release](https://semantic-release.gitbook.io) and conventional commits, as in the other repos. Each plugin is versioned and tagged on its own (`disk-reclaim-v0.2.0`), and its `version` in `plugin.json` is bumped for you, since installs follow that field.

A plugin releases only for commits **scoped to it**. Squash-merged PR titles become the commit message, so title PRs like this:

| PR title | Release |
|---|---|
| `feat(disk-reclaim): add a review mode` | minor |
| `fix(disk-reclaim): hold items a container mounts` | patch |
| `perf(disk-reclaim): ...` | patch |
| `feat(disk-reclaim)!: ...` or a `BREAKING CHANGE:` footer | major |
| `feat: ...`, `feat(other-plugin): ...`, `docs`, `ci`, `chore`, `test`, `refactor` | none |

An unscoped `feat:` deliberately does not release anything, because in a repo with several plugins it would release all of them. The release notes for a plugin list every `feat` and `fix` since its last tag, so keep titles scoped. The release commit (`chore(release): ...`) updates `plugin.json` and the plugin's `CHANGELOG.md` directly on `main` with the `GH_TOKEN` secret, which the factory provides.

## Licence

MIT, see `LICENSE`.
