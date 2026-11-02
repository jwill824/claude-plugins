# Asking with multiple-choice prompts

In an interactive session, use the `AskUserQuestion` tool for decisions with three or more choices. It makes each approval explicit instead of a bare "yes" to a long message. For one or two choices, a plain question is faster. Never ask from a hook, a schedule or any run where the tool is unavailable: report and stop.

Shape of the tool: 1 to 4 questions per call, 2 to 4 options each, single or multi-select, and an "Other" free-text choice is added automatically. A header is at most 12 characters.

| Moment | Question |
|---|---|
| What to run | One multi-select over the groups that have items (for example SAFE stale, SAFE recently used, each VERIFY group). Unticked means skipped. |
| Ruling on a review group | One single-select per group, up to 4 groups per call: **Remove**, **Keep**, **Inspect first**. Put the group name and total in the question. |
| Large single items | One question per item over 1 GB, same options. Smaller items stay in their group. |
| Final approval | One single-select: run these N items / change the selection / cancel. Use the option `preview` to list the exact commands and the total, since previews work on single-select only. |

Rules that keep the prompts from weakening the safety model:

1. **Show the dry-run table in the reply first.** The dialog may be all the user sees, so each question also carries the facts needed to decide alone: size, last activity, what holds it and what would be rebuilt. Never ask about something the user has not been shown.
2. **An answer covers only the items named in that question.** Free text from "Other" is an instruction to interpret, not approval of anything unlisted. A dismissed dialog or no answer means no.
3. **No option is pre-recommended when it deletes data.** Do not mark Remove as the recommended choice, and never offer "remove everything" over VERIFY items that were not each listed.
4. **Group before asking.** With more than a handful of items, group by kind (app caches, simulators, Docker volumes, Maven versions) and ask per group, then per item only for the big ones. A question with 30 options is a wall, not a choice.
5. **The executor still re-verifies.** Approval says what the user wants, never that it is still safe. Run it through the executor, and report anything it holds.
