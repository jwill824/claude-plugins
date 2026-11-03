// A release-notes generator that lists only the commits scoped to one plugin.
//
// semantic-release has no per-plugin filter: it hands every plugin the commits since that plugin's last tag, so a
// plugin's notes would list another plugin's `feat(other): ...` lines. This wraps the stock generator and passes it
// only the commits whose conventional-commit scope matches. (Filtering `context.commits` in a separate plugin does
// not work: semantic-release gives each plugin a deep clone of the context.)
//
//   ["./release/scope-notes.mjs", { "scope": "disk-reclaim" }]
//
// The version bump does not need this: the commit-analyzer's releaseRules already ignore other scopes.
import { generateNotes as stock } from '@semantic-release/release-notes-generator';

const scopeOf = (commit) => {
  const m = /^[a-z]+\(([^)]+)\)!?:/i.exec(commit.message || commit.subject || '');
  return m ? m[1] : null;
};

export async function generateNotes(pluginConfig, context) {
  const { scope, ...rest } = pluginConfig;
  if (!scope) throw new Error('scope-notes: the "scope" option is required');
  const commits = (context.commits || []).filter((c) => scopeOf(c) === scope);
  return stock(rest, { ...context, commits });
}
