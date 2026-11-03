# thingstead

A [Claude Code](https://code.claude.com) plugin marketplace. Each plugin lives in `plugins/<name>/` and can bundle skills, hooks and MCP servers.

| Plugin | What it does |
|---|---|
| [`disk-reclaim`](plugins/disk-reclaim) | Finds where disk space is going and reclaims it safely, for a project folder or a whole Mac |
| [`dotfiles`](plugins/dotfiles) | Audits what is installed and who owns it: package managers, apps, commands and dotfiles. Finds what nothing claims and leftovers whose tool is gone |

## Install

```
/plugin marketplace add jwill824/claude-plugins
/plugin install disk-reclaim@thingstead
/plugin install dotfiles@thingstead
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

## dotfiles

Audits what is installed on a Mac and who owns it. It **does not manage dotfiles** (use `chezmoi`, `stow` or `brew bundle` for that): it reads, and it reports what nothing owns.

| Command | What happens |
|---|---|
| `/dotfiles:audit [--html FILE]` | Lists what is installed by source (Homebrew, mise, npm, uv, cargo, apps), which commands and dotfiles no package manager claims, which manager could own each (suggestions only, never installed), and leftovers whose tool is gone, and tools installed through more than one manager (which copy is active, and which system copy is shadowed), toolchains a manager says are missing, and PATH entries that point nowhere. `--html` also writes a self-contained, interactive map: a treemap by owner and size you can open group by group, with the Homebrew and mise tables, filters, and select-and-copy of item keys for `/dotfiles:accept` and `/dotfiles:tidy`. It is one file with no network access (a policy blocks every request and pins its one script). Read-only. Claude can start it from a plain question ("what's installed on this machine?") |
| `/dotfiles:tidy` | Prints, and never runs, the commands for the orphan candidates you pick. Typed-only |
| `/dotfiles:accept` | Marks items you looked at and chose to keep (a company VPN client) as accepted, with a note, so they count as resolved. Writes one private state file and nothing else. Typed-only |

Every item is classed by who owns it, and every claim carries its evidence:

| Class | Meaning |
|---|---|
| **managed** | A package manager claims it (its own list or folder) |
| **unmanaged** | Installed, but no manager claims it: a downloaded binary, an app dragged into `/Applications`. Neutral, not a to-do |
| **orphaned** | A candidate: a manager's own verdict (`brew autoremove -n`, `mise prune --dry-run`), a broken link, or a tool folder whose tool is installed nowhere, that no config file mentions and that nothing has written to recently |
| **referenced** | Not installed, but a config file mentions it or something wrote to it recently, so it may still be in use |
| **unknown** | An owner could not be ruled out (a source failed, a link was unreadable, or it lives in a manager that is not read yet) |

How it stays safe:

- **A failed check is never evidence.** If a source cannot be read, "unmanaged" and "orphaned" become "unknown" and the report says why, at the top.
- **Absence of a tool is weak evidence.** A folder can be in use without the tool being installed (a project's `gradlew`, an IDE, `uvx`), so recent writes and config references keep it out of the orphan list. These only ever make a verdict more cautious.
- **Dates are honest.** "Installed" is Homebrew's own record, else the folder's creation time. Where only a modification time exists (Linux), the report marks it `~` and says so, and it never invents a date. A large, old, unmanaged item is flagged as a *stale candidate*, which is a flag and never changes its class.
- **Hints are not proof.** An app counts as owned by a Homebrew cask only on exact evidence: the cask's `app` artifact, or the app's bundle id in the list of packages the cask installs (`pkgutil`). A bundle id in a cask's `quit` list, or a cask that merely shares the app's name, only becomes a suggestion, because a cask can quit apps it does not install.
- **Accepted state is the only thing written.** `/dotfiles:accept` records your choice in `accepted.json` (`$DOTFILES_STATE`, else `$XDG_STATE_HOME/dotfiles`, else `~/.local/state/dotfiles`; private, replaced atomically). The audit only reads it, and an acceptance goes void if the item's class later changes. Only "accept" is ever recorded, never "remove".
- **Read-only, with an allow-list, except for one thing.** The audit only runs list and dry-run commands, and the tests check the commands the fake managers actually received. The exception is the item checker: its `brew search` may let Homebrew refresh its own name index (Homebrew's staleness rule, about once a day, which can use the network and rewrites Homebrew's cache). The report says whether it did, and `--offline` forbids it. Nothing is ever installed.
- **Contents are never read into the report.** Dotfiles can hold tokens, so reports name files and folders, never what is inside. References are found by searching shell startup files, Claude settings and MCP configs, LaunchAgents and cron, and only the *name of the file that matched* is reported. Project folders are not searched. The HTML map is a local file with no scripts and nothing fetched, and it names things on your machine, so keep it local.
- **`tidy` prints.** Folders and links go to the Trash, manager removals come after a snapshot command, and nothing is printed with `--zap`, `--force` or `--ignore-dependencies`. Each candidate is re-checked first.

Not read yet, and shown as **unknown** when a command resolves into them: `pipx`, `gem`, `go`, `bun`, `pnpm` and version managers like `nvm`, `asdf` and `pyenv`. Which tool owns which dot-folder comes from a hand-maintained table; anything not in it is reported as unattributed, never guessed. Requires Python 3 (standard library only). The application scan is macOS-only; the rest also runs on Linux.

## Adding a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and put skills in `plugins/<name>/skills/<skill>/SKILL.md` (hooks in `hooks/hooks.json`, MCP servers in `.mcp.json`).
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Copy `plugins/disk-reclaim/.releaserc.json` to `plugins/<name>/.releaserc.json` and replace `disk-reclaim` with `<name>` everywhere (tag format, rule scopes, the `scope` of the notes generator, changelog and `plugin.json` paths). That is what releases the plugin.
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

An unscoped `feat:` deliberately does not release anything, because in a repo with several plugins it would release all of them. A plugin's release notes list only the commits scoped to it, through `release/scope-notes.mjs` (semantic-release has no per-plugin filter of its own), so another plugin's commits never show up in them. The release commit (`chore(release): ...`) updates `plugin.json` and the plugin's `CHANGELOG.md` directly on `main` with the `GH_TOKEN` secret, which the factory provides.

## Licence

MIT, see `LICENSE`.
