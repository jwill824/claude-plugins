---
name: audit
description: Audit what is installed on this machine and who owns it: Homebrew, mise, npm, pnpm, uv and cargo installs, applications, commands on PATH, and dotfiles. Reports what no package manager claims, leftovers whose tool is gone (orphan candidates, with evidence) and links that will break, and can write an HTML map of it. Read-only, except that the checker may let Homebrew refresh its own name index (`--offline` forbids it). Use when asked what is installed, which tools or dotfiles are lying around, or what could be cleaned up.
argument-hint: "[--html FILE] [--cmux] [--all] [--json] [--offline] [--check NAME]"
---

# audit

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

This skill only looks. It installs, removes and changes nothing, and it never reads file contents into its report: dotfiles can hold tokens, so the report names folders and files, never what is inside them.

```bash
SCAN="$(mktemp -d)/audit.json"
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py --save "$SCAN" $ARGUMENTS
```

It takes under a minute (most of it is sizing folders). `--all` lists every Homebrew formula, not only the ones you asked for. `--json` prints the full result instead of the report. `--html FILE` also writes a self-contained map: a treemap of what is installed by owner and size, with the orphan, referenced, unknown, unmanaged and unattributed lists underneath (it sizes every package too, so it takes a little longer). The saved JSON is what `/dotfiles:tidy` reads.

`--offline` stops Homebrew from refreshing its own name index while the checker searches it. Without it, `brew search` follows Homebrew's own staleness rule (about once a day), which may use the network and rewrites Homebrew's cache, and nothing else; the report's "Adoption candidates" section says whether the index was *refreshed* or *cached*. Tell the user when it was refreshed. `--check NAME` (repeatable) also looks for a manager that offers a name the user types.

The map is interactive but entirely offline: one file, one inline script pinned by hash under a policy (`default-src 'none'`) that blocks every network request, with no data embedded as JSON. In it you can open a group to see every item, click a block for its evidence, filter and search, sort the Homebrew and mise tables, and tick items (one by one, or a whole list with its select-all box, which respects the search and filters) to copy a `/dotfiles:accept` or `/dotfiles:tidy` command with their keys one per line. Copying changes nothing: the user pastes it, and the skill still confirms.

`--cmux` (implies `--html`; with no `--html FILE` the map goes to one fixed private file, `map.html` in the same state folder as the accepted-state file, so every run reloads the same tab instead of opening a new one) opens the map in a cmux browser split beside the terminal, without taking focus, or reloads the tab that already shows that same file. It exists only for people running Claude Code inside cmux and is opt-in: it changes the user's window layout, so it follows the user's request and nothing else. When `CMUX_WORKSPACE_ID` is set and the user asks to see, open or show the map (or the audit as a picture, or passes `--cmux`), add `--cmux` yourself and do not ask first: asking for the map is the request. Do not add it to a plain audit request that does not mention the map; you may offer it once there. It runs only `cmux tree --all --json`, `cmux open FILE --no-focus` and `cmux browser surface:N reload`; a tab is recognised by its exact URL in the current workspace, never by its title; and when cmux cannot be read it does nothing and says so. The map has no channel back to cmux, so choosing items in it still ends with the user pasting a typed command.

Open the map locally. It names folders, apps and commands on this machine, so publish it as an Artifact only if the user asks.

## How to read it

Every item is classed by who owns it, and every claim carries its evidence:

| Class | Meaning | What to say |
|---|---|---|
| **managed** | A package manager claims it (the manager's own list or folder) | Nothing to do |
| **owned** | A dot-folder that belongs to an installed tool | Nothing to do |
| **unmanaged** | Installed, but no manager claims it: a downloaded binary, an app dragged into `/Applications` | Neutral. Say how it got here, never that it should go |
| **orphaned** | A candidate: a manager's own orphan verdict (`brew autoremove -n`, `mise prune --dry-run`), a broken link, or a tool folder whose tool is installed nowhere, that no config file mentions and that nothing has written to recently | A candidate, never a verdict. Quote the evidence |
| **referenced** | Not installed, but a config file mentions it or something wrote to it recently | Not an orphan. Say what references it |
| **unknown** | An owner could not be ruled out: an adapter failed, a link was unreadable, or it lives in a manager this version does not read | Say why, and do not call it unmanaged or orphaned |
| **unattributed** | A dot-item the knowledge table has no entry for | Never guess what it belongs to |
| **config / system** | Personal config, state, credentials, OS files | Listed, never candidates |

Start with the **Sources** table. If any source failed, say so before anything else, because every "unmanaged" and "orphaned" verdict becomes "unknown" while a source is unreadable. A source that is `missing` just means the tool is not installed.

**Things to hold to:**

- Never recommend removing an unmanaged, unknown, referenced or unattributed item. Only orphan candidates can be offered, and only through `/dotfiles:tidy`.
- A tool folder can be in use without the tool being installed (a project's `gradlew`, an IDE, `uvx`). That is why recent writes and config references keep a folder out of the orphan list.
- References are searched in global config only (shell startup files, Claude settings and MCP configs under `~/.claude`, LaunchAgents, cron), not in project folders. An unmanaged command that "no config file mentions" may still be used by a project.
- Dates say when *this version* arrived: Homebrew's own record, else the creation time of the folder (macOS). A leading `~` means only a modification time was available (Linux has no creation time), so it says when something last changed. Never present a `~` date as an install date. A **stale candidate** flag marks a large item (100 MB or more) that is over a year old, not green and not mentioned anywhere. It is a flag, not a verdict, and it never changes the class.
- **Duplicate installs** are the same tool through two or more managers (node from Homebrew and mise, Python from Homebrew and mise). Names are matched by a small explicit table (`python@3.14`, `python3` and `python` are one tool; `nodejs` is `node`); a name it does not know is never reported, so a missing duplicate is possible and a false one is not. Several versions under one manager (mise node 18, 20 and 22) are only a versions line. A system copy (`/usr/bin/python3`) is a note, never a finding. **Active** is the first match on the audit's PATH, which may differ from the user's shell because the audit does not read shell startup files: say so. When a source failed the list may be incomplete, and the report says that.
- **Coverage** is the goal metric: *green* (owned by a manager), *accepted* and **unresolved** (unmanaged, orphaned, referenced, unknown, unattributed or missing, and not accepted). Config files, system files and dead PATH entries are not counted. **Accepted** items are ones the user chose to keep through `/dotfiles:accept`; they count as resolved and `/dotfiles:tidy` will not offer them. The audit only *reads* that file. An acceptance whose item has since changed class is **void** and listed as such: say so, and treat the item as unresolved again. If the state file cannot be read the report says so and nothing is treated as accepted. Point the user to `/dotfiles:accept` to record one; you cannot start it for them.
- **Adoption candidates** are suggestions for what no manager owns: a Homebrew cask or formula with the same name, or a `mise registry` entry. They are never ownership: a name match does not prove two things are the same product, so say "candidate, verify first". Nothing is installed and the command shown is text only; the user runs it themselves if they want it. A candidate never turns an unmanaged item green. "No candidate found" is said only when the search worked; "could not check" means the search failed, and says nothing about whether a candidate exists.
- **Missing toolchains** (class `missing`) are what a manager declares or expects but has not installed: `mise ls --missing`, `brew missing` (a formula whose dependency is gone) and a rustup with toolchains but no default. They are not orphans and tidy refuses them. A folder whose tool a manager declares is shown as *referenced*, not orphaned, because the tool is wanted. "None reported" is said only for sources whose check succeeded; a check that failed says "could not check", never "none".
- **Dead PATH entries** are folders on the audit's PATH that do not exist (class `config`, never a candidate). The audit's PATH may differ from the user's shell, so they are leads to look at, not proof the shell has them. OS-managed entries are left out.
- The report names things on this machine. Do not publish it or paste it anywhere shared.

## Next step

Show the orphan candidates and what references the not-quite-orphans. If the user wants commands for any candidate, tell them to type `/dotfiles:tidy`; if they want orphan folders and broken links moved to the Trash for them, `/dotfiles:fix`; if they want an unmanaged app or command brought under Homebrew or mise, `/dotfiles:adopt`; you cannot start any of them for them.
