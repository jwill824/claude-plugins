"""Reference search: which config files mention a folder or a command, so "not installed" is never mistaken for "not used".

It reads shell startup files, the git config, Claude Code settings and MCP configs, mise's global config, LaunchAgents and
the crontab. These can hold tokens, so the contract is strict: the text stays in memory, and the only thing that ever
leaves this module is the *label of a file that matched*. Never a line, never a value. (The tests plant a sentinel string
next to a reference and fail if it shows up anywhere.)

A file that exists but cannot be read is recorded in `unreadable`: an unread file might hold the reference, so the
audit treats every orphan verdict as unknown while any source is unreadable.
"""
import glob, os, re
from common import run, tilde

FILES = ('.zshrc', '.zprofile', '.zshenv', '.zlogin', '.bashrc', '.bash_profile', '.profile', '.tcshrc', '.gitconfig',
         '.claude/settings.json', '.claude.json', '.config/mise/config.toml', '.config/fish/config.fish', '.config/git/config')
MAX_BYTES = 2_000_000


class Refs:
    def __init__(self, home):
        self.home = home
        self.texts = {}        # label -> text, in memory only
        self.unreadable = []   # labels of sources that exist but could not be read
        for rel in FILES:
            self._load(os.path.join(home, rel))
        for p in sorted(glob.glob(os.path.join(home, 'Library', 'LaunchAgents', '*.plist'))):
            self._load(p)
        for p in self._mcp_files():
            self._load(p)
        st, out = run(['crontab', '-l'])
        if st == 'ok':
            self.texts['crontab'] = out
        elif st == 'failed' and 'no crontab' not in out.lower():
            self.unreadable.append('crontab')

    def _mcp_files(self, limit=300):
        found, base = [], os.path.join(self.home, '.claude', 'plugins')
        if not os.path.isdir(base):
            return found
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in ('node_modules', '.git') and dp[len(base):].count(os.sep) < 6]
            if '.mcp.json' in fns:
                found.append(os.path.join(dp, '.mcp.json'))
                if len(found) >= limit:
                    break
        return found

    def _load(self, path):
        if not os.path.isfile(path):
            return
        try:
            with open(path, 'rb') as f:
                self.texts[tilde(path, self.home)] = f.read(MAX_BYTES).decode('utf-8', 'ignore')
        except OSError:
            self.unreadable.append(tilde(path, self.home))

    def find_path(self, rel):
        """Labels of files that mention ~/rel, $HOME/rel, ${HOME}/rel or the absolute path."""
        needles = (f'~/{rel}', f'$HOME/{rel}', f'${{HOME}}/{rel}', f'{self.home}/{rel}')
        return sorted(label for label, text in self.texts.items() if any(n in text for n in needles))

    def find_name(self, name):
        """Labels of files that mention a command by name (a whole word, so `gh` does not match `ghost`)."""
        rx = re.compile(r'(?<![A-Za-z0-9_-])' + re.escape(name) + r'(?![A-Za-z0-9_-])')
        return sorted(label for label, text in self.texts.items() if rx.search(text))
