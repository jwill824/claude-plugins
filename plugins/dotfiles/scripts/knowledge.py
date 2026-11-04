"""The knowledge table: which tool a dotfile or dot-folder belongs to, and how to tell the tool is installed.

Paths are relative to the home folder. Entries come in three kinds:

  keep=True      personal config, state, shell history, system files and anything that can hold credentials. They are
                 listed for the inventory and are never orphan candidates, whatever is installed.
  needles=[...]  a tool's own folder. The tool counts as installed when any needle (a binary, formula, cask, mise tool,
                 npm package, crate, app name or bundle id, in lower case) is found. If none is found, and no config
                 file references the folder, the folder is an orphan candidate.
  needles=[]     a folder made by a tool that installs itself (a `curl | sh` installer) with nothing we can detect.
                 It gets a neutral verdict, never "orphaned", because the absence of a detector proves nothing.

Anything not in this table is reported as unattributed. It is never guessed.
"""
from collections import namedtuple

Entry = namedtuple('Entry', 'path tool kind needles keep prefix')


def E(path, tool, kind, *needles, keep=False, prefix=False):
    return Entry(path, tool, kind, tuple(n.lower() for n in needles), keep, prefix)


_ENTRIES = [
    # shells, editors and their state: personal, never orphans
    E('.zshrc', 'zsh', 'shell', keep=True), E('.zprofile', 'zsh', 'shell', keep=True), E('.zshenv', 'zsh', 'shell', keep=True),
    E('.zlogin', 'zsh', 'shell', keep=True), E('.zsh_history', 'zsh', 'shell', keep=True), E('.zsh_sessions', 'zsh', 'shell', keep=True),
    E('.zcompdump', 'zsh', 'shell', keep=True, prefix=True), E('.zshrc.pre-', 'zsh', 'shell', keep=True, prefix=True),
    E('.bashrc', 'bash', 'shell', keep=True), E('.bash_profile', 'bash', 'shell', keep=True), E('.bash_history', 'bash', 'shell', keep=True),
    E('.profile', 'sh', 'shell', keep=True), E('.tcshrc', 'tcsh', 'shell', keep=True),
    E('.viminfo', 'vim', 'state', keep=True), E('.node_repl_history', 'node', 'state', keep=True),
    E('.gitconfig', 'git', 'config', keep=True), E('.gitignore_global', 'git', 'config', keep=True),
    E('.yarnrc.yml', 'yarn', 'config', keep=True), E('.testcontainers.properties', 'testcontainers', 'config', keep=True),
    # system files and umbrella folders
    E('.DS_Store', 'macOS', 'system', keep=True), E('.Trash', 'macOS', 'system', keep=True), E('.CFUserTextEncoding', 'macOS', 'system', keep=True),
    E('.cups', 'macOS', 'system', keep=True), E('.config', 'XDG', 'system', keep=True), E('.local', 'XDG', 'system', keep=True),
    E('.cache', 'XDG', 'system', keep=True), E('.local/share', 'XDG', 'system', keep=True), E('.local/state', 'XDG', 'system', keep=True),
    E('.local/bin', 'XDG', 'system', keep=True),
    # credentials and anything that can hold them: never offered
    E('.ssh', 'ssh', 'secrets', keep=True), E('.gnupg', 'gpg', 'secrets', keep=True), E('.aws', 'aws cli', 'secrets', keep=True),
    E('.azure', 'azure cli', 'secrets', keep=True), E('.azure-devops', 'azure devops', 'secrets', keep=True),
    E('.kube', 'kubectl', 'secrets', keep=True), E('.docker', 'docker', 'secrets', keep=True), E('.npmrc', 'npm', 'secrets', keep=True),
    E('.netrc', 'curl', 'secrets', keep=True), E('.emulator_console_auth_token', 'android emulator', 'secrets', keep=True),
    # tool folders
    E('.android', 'Android SDK', 'tool-home', 'adb', 'emulator', 'android-studio', 'android-commandlinetools', 'android studio'),
    E('.ansible', 'Ansible', 'tool-home', 'ansible'),
    E('.anydesk', 'AnyDesk', 'tool-home', 'anydesk'),
    E('.cargo', 'Cargo', 'tool-home', 'cargo', 'rustup'),
    E('.rustup', 'rustup', 'tool-home', 'rustup'),
    E('.cocoapods', 'CocoaPods', 'tool-home', 'pod', 'cocoapods'),
    E('.codex', 'Codex CLI', 'tool-home', 'codex', 'openai/codex'),
    E('.colima', 'Colima', 'tool-home', 'colima'),
    E('.copilot', 'GitHub Copilot CLI', 'tool-home', 'copilot', 'github/copilot'),
    E('.electron-gyp', 'node-gyp', 'cache', 'node-gyp', 'node'),
    E('.fly', 'flyctl', 'tool-home', 'flyctl', 'fly'),
    E('.gradle', 'Gradle', 'tool-home', 'gradle'),
    E('.homebrew', 'Homebrew', 'tool-home', 'brew'),
    E('.m2', 'Maven', 'tool-home', 'mvn', 'maven'),
    E('.minikube', 'minikube', 'tool-home', 'minikube'),
    E('.npm', 'npm', 'cache', 'npm'),
    E('.oh-my-zsh', 'Oh My Zsh', 'tool-home'),
    E('.ollama', 'Ollama', 'tool-home', 'ollama'),
    E('.pdfbox.cache', 'PDFBox', 'cache', 'java', 'pdfbox'),
    E('.serena', 'Serena', 'tool-home', 'serena', 'serena-agent'),
    E('.sonar', 'SonarScanner', 'tool-home', 'sonar-scanner', 'sonarlint'),
    E('.sts4', 'Spring Tools', 'tool-home', 'spring tools', 'sts', 'spring-tool-suite-4'),
    E('.swiftpm', 'Swift Package Manager', 'tool-home', 'swift', 'xcode'),
    E('.terraform.d', 'Terraform', 'tool-home', 'terraform', 'tofu'),
    E('.tflint.d', 'TFLint', 'tool-home', 'tflint'),
    E('.vaadin', 'Vaadin', 'tool-home', 'vaadin'),
    E('.vfox', 'vfox', 'tool-home', 'vfox'),
    E('.claude', 'Claude Code', 'tool-home', 'claude', 'claude code'),
    E('.claude.json', 'Claude Code', 'state', 'claude', 'claude code'),
    E('.cmux', 'cmux', 'tool-home', 'cmux'), E('.cmuxterm', 'cmux', 'tool-home', 'cmux'),
    E('.vscode', 'Visual Studio Code', 'tool-home', 'code', 'visual studio code'),
    E('.vscode-shared', 'Visual Studio Code', 'tool-home', 'code', 'visual studio code'),
    E('.vscode-insiders', 'VS Code Insiders', 'tool-home', 'code-insiders', 'visual studio code - insiders'),
    E('.vscode-insiders-shared', 'VS Code Insiders', 'tool-home', 'code-insiders', 'visual studio code - insiders'),
    E('.vscode-agents-insiders', 'VS Code Insiders', 'tool-home', 'code-insiders', 'visual studio code - insiders'),
    E('.vscode-server', 'VS Code Server', 'tool-home', 'code-server', 'code'),
    # XDG config / data / cache folders, one level down
    E('.config/gh', 'GitHub CLI', 'config', 'gh'), E('.config/nvim', 'Neovim', 'config', 'nvim'), E('.config/mise', 'mise', 'config', 'mise'),
    E('.config/git', 'git', 'config', 'git'), E('.config/uv', 'uv', 'config', 'uv'), E('.config/htop', 'htop', 'config', 'htop'),
    E('.config/fish', 'fish', 'config', 'fish'), E('.config/tmux', 'tmux', 'config', 'tmux'), E('.config/bat', 'bat', 'config', 'bat'),
    E('.config/karabiner', 'Karabiner-Elements', 'config', 'karabiner-elements', 'karabiner-elements.app'),
    E('.config/ghostty', 'Ghostty', 'config', 'ghostty'), E('.config/zed', 'Zed', 'config', 'zed'),
    E('.config/lazygit', 'lazygit', 'config', 'lazygit'), E('.config/yazi', 'yazi', 'config', 'yazi'),
    E('.config/op', '1Password CLI', 'config', 'op', '1password-cli'), E('.config/helix', 'Helix', 'config', 'hx', 'helix'),
    E('.config/wezterm', 'WezTerm', 'config', 'wezterm'), E('.config/alacritty', 'Alacritty', 'config', 'alacritty'),
    E('.config/starship.toml', 'Starship', 'config', 'starship'), E('.config/configstore', 'configstore', 'state', keep=True),
    E('.local/share/mise', 'mise', 'tool-home', 'mise'), E('.local/share/uv', 'uv', 'tool-home', 'uv'),
    E('.local/share/nvim', 'Neovim', 'tool-home', 'nvim'), E('.local/share/pipx', 'pipx', 'tool-home', 'pipx'),
    E('.local/state/mise', 'mise', 'state', 'mise'), E('.local/state/nvim', 'Neovim', 'state', 'nvim'),
    E('.cache/nvim', 'Neovim', 'cache', 'nvim'), E('.cache/uv', 'uv', 'cache', 'uv'), E('.cache/pre-commit', 'pre-commit', 'cache', 'pre-commit'),
    E('.cache/huggingface', 'Hugging Face', 'cache', 'huggingface-cli', 'hf', 'huggingface_hub'), E('.cache/gh', 'GitHub CLI', 'cache', 'gh'),
    E('.cache/act', 'act', 'cache', 'act'), E('.cache/codex-runtimes', 'Codex CLI', 'cache', 'codex', 'openai/codex'),
    # more public tools. A folder that can hold credentials is kept (listed, never offered); the rest are owned while the tool is installed
    E('.config/gcloud', 'Google Cloud CLI', 'secrets', keep=True), E('.config/github-copilot', 'GitHub Copilot', 'secrets', keep=True),
    E('.context7', 'Context7 CLI', 'secrets', keep=True), E('.ccs', 'CCS', 'secrets', keep=True),
    E('.local/state/lesshst', 'less', 'state', keep=True), E('.local/state/dotfiles', 'dotfiles plugin', 'state', keep=True),
    E('.config/raycast', 'Raycast', 'config', 'raycast'),
    E('.local/share/gh', 'GitHub CLI', 'tool-home', 'gh'), E('.local/state/gh', 'GitHub CLI', 'state', 'gh'),
    E('.config/direnv', 'direnv', 'config', 'direnv'), E('.local/share/direnv', 'direnv', 'tool-home', 'direnv'),
    E('.config/lnav', 'lnav', 'config', 'lnav'),
    E('.config/powershell', 'PowerShell', 'config', 'pwsh', 'powershell'), E('.local/share/powershell', 'PowerShell', 'tool-home', 'pwsh', 'powershell'),
    E('.cache/powershell', 'PowerShell', 'cache', 'pwsh', 'powershell'),
    E('.config/fresh', 'Fresh', 'config', 'fresh', 'fresh-editor'), E('.local/state/fresh', 'Fresh', 'state', 'fresh', 'fresh-editor'),
    E('.cache/helix', 'Helix', 'cache', 'hx', 'helix'), E('.cache/tree-sitter', 'tree-sitter', 'cache', 'tree-sitter'),
    E('.local/state/pnpm', 'pnpm', 'state', 'pnpm'), E('.bun', 'Bun', 'tool-home', 'bun'), E('.cache/actcache', 'act', 'cache', 'act'),
    E('.config/openspec', 'OpenSpec', 'config', 'openspec', '@fission-ai/openspec'),
    E('.config/cmux', 'cmux', 'config', 'cmux'), E('.local/state/cmux', 'cmux', 'state', 'cmux'),
]

_EXACT = {e.path: e for e in _ENTRIES if not e.prefix}
_PREFIX = [e for e in _ENTRIES if e.prefix]
TABLE_SIZE = len(_ENTRIES)


def lookup(rel):
    """The entry for a path relative to the home folder, or None."""
    e = _EXACT.get(rel)
    if e:
        return e
    for p in _PREFIX:
        if rel.startswith(p.path):
            return p
    return None
