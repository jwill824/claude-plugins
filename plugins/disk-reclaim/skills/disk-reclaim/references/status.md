# Status mode, hooks and schedules

`scan_system.py --status` answers "do I need to do anything?" in about a second: free space, the trend over the last two weeks, the last saved scan and the last cleanup. It scans nothing and changes nothing, and exits 1 only when space is low (under `--warn-free-gb` or `--warn-free-pct`). A missing scan, or one older than `--max-scan-age-days`, is shown but counts as a problem only when space is also low, so a hook that runs every session stays silent until it matters. State lives in `$DISK_RECLAIM_STATE`, else `$XDG_STATE_HOME/disk-reclaim`, else `~/.local/state/disk-reclaim`. A scan feeds it only when run with `--save-state`, and every `execute_system.py --execute` records its result.

Automate the cheap parts and keep deletion interactive:

| Layer | Runs | Does |
|---|---|---|
| Hook (session start) | `scan_system.py --status --quiet-if-ok` | Prints a line only when something needs attention. Never scan or execute from a hook: it is unattended, runs while other sessions hold caches open, and deletion needs approval. |
| Schedule (for example weekly, from cron or launchd on the machine itself) | `scan_system.py --json --save-state > "$SCAN"` | Keeps the scan summary fresh so the status is truthful. Read-only. A cloud scheduled agent cannot see the local disk. |
| Interactive | the skill (`review` or `run`), then `execute_system.py` | Review the tiers, approve items, execute with re-verified gates. |

Do not schedule `--execute`: tiers and gates describe one moment, and a cache that was stale last week may be in use today.

Installing the plugin adds the session-start hook for you (`hooks/hooks.json`, quiet unless space is low). If the skill was copied into `~/.claude/skills/` instead, add this to `~/.claude/settings.json` under `hooks.SessionStart` yourself, with the real path of the folder:

```json
{"matcher": "startup", "hooks": [{"type": "command", "timeout": 10,
  "command": "python3 /path/to/disk-reclaim/scripts/scan_system.py --status --quiet-if-ok || true"}]}
```
