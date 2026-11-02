# Tiers, activity and gates

Tiers, and why they exist:

| Tier | Meaning | How to treat it |
|---|---|---|
| **SAFE** | Derived output (build dirs, `node_modules`, caches), git-ignored, no tracked files inside, a manifest next to it to rebuild from | Offer to remove; cost is only a rebuild |
| **VERIFY** | Probably reclaimable but a human should look: untracked-not-ignored dirs, logs, venvs, large files, clean-and-pushed worktrees | Explain the doubt, ask before including |
| **KEEP** | Contains tracked files, possible credentials or key material, uncommitted changes, commits that exist on no remote, locked or main worktrees | Never propose removal |

Names like `build`, `bin`, `dist` and `vendor` are source in some repos and output in others, which is why the scanner demands git-ignored status plus a nearby manifest before calling them SAFE. If the result looks surprising (a "SAFE" item you doubt, a huge KEEP), check it yourself rather than repeating the label. Read `safety-rules.md` (same folder) when deciding anything beyond the script's labels, especially worktrees and ignored files.

## Activity and gates (machine-wide scan)

Each item carries a tier, an **activity** class and the **gates** that ran. Read them before presenting:

| Activity | Meaning | Source, in order of trust |
|---|---|---|
| ACTIVE / RECENT / STALE | newest write within `--active-days`, within `--stale-days`, or older | the tool's own timestamp (simulator and runtime `lastUsedAt`, newest file inside a Docker volume), then a bounded `find -mtime` |
| UNKNOWN | no trustworthy timestamp (for example Docker build cache) | treated like in-use, never like stale |

Gates, all recorded per item: path sanity (real directory inside home, not a symlink), **in use** (one system-wide `lsof` plus process command lines, so a held lock or a running tool blocks the item), git state (uncommitted or unpushed work), key material, and activity. Gates only move an item toward caution. **Activity can hold a SAFE item back, it never promotes a VERIFY item to SAFE**: a stale Docker volume or simulator is still the user's call.
