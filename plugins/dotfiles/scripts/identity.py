"""Which installed packages are the same tool.

Managers name one tool differently (Homebrew `python@3.14` and `nodejs`, mise `python` and `node`). `canon` folds the names
this table knows about onto one name. It never guesses: a name it does not know stays itself, and the safe failure is
"not reported as a duplicate". Backend-prefixed names such as mise's `npm:prettier` or `aqua:oras-project/oras` are left alone
for the same reason.
"""
import re

VERSION_SUFFIX = re.compile(r'^(.+?)@\d[\w.]*$')        # python@3.14, node@22, openjdk@21
ALIASES = {'nodejs': 'node', 'openjdk': 'java', 'golang': 'go'}
PATTERNS = ((re.compile(r'^python\d+(\.\d+)*$'), 'python'),)   # python3, python3.14
COMMANDS = {'python': ('python', 'python3'), 'java': ('java',), 'node': ('node',)}


def canon(name):
    n = (name or '').strip().lower()
    m = VERSION_SUFFIX.match(n)
    if m:
        n = m.group(1)
    n = ALIASES.get(n, n)
    for rx, to in PATTERNS:
        if rx.match(n):
            return to
    return n


def commands(tool):
    """The command names a tool is run as."""
    return COMMANDS.get(tool, (tool,))
