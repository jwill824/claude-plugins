"""The re-checks `adopt.py` does before it installs anything, shared by its dry run and its execute step so they cannot disagree.

Adopting installs software, so every gate fails closed: when a fact cannot be read, the item is held with a sentence saying why.
The functions here are pure (facts in, a reason or None out); `adopt.py` reads the facts through its own exact allow-list.
Nothing here reads file contents beyond two version strings in an app's Info.plist.
"""
import os, plistlib, re

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
VERSION_RE = re.compile(r'^\d+(\.\d+)*$')
# the only backends a command may be installed from: aqua verifies what it downloads where its registry says how, core is mise's own.
# asdf, vfox, ubi, cargo, npm and pipx run plugin scripts or build from source, or have no checksum story, so they are held.
BACKEND_RE = re.compile(r'^(aqua:[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*|core:[a-z0-9][a-z0-9._-]*)$')
# cask artifacts that only place files. Anything else (`pkg`, `installer`, `preflight`, `postflight`, `suite`, ...) runs code or
# touches the system, so the cask is held. `uninstall` and `zap` run only when the cask is removed.
SAFE_ARTIFACTS = frozenset({'app', 'binary', 'manpage', 'bash_completion', 'zsh_completion', 'fish_completion', 'zap', 'uninstall', 'target', 'command_wrapper'})
SAFE_DEPENDS = frozenset({'macos', 'arch'})   # a dependency on another cask or formula would install something the user never named


def is_accepted(item, records):
    rec = records.get(item['key'])
    rec = rec if rec and rec['cls'] == item['cls'] else item.get('accepted')
    return rec


def common_gate(item, records, status):
    """Why this item cannot be adopted at all, or None. `status` is the audit's status for the manager that would do it."""
    rec = is_accepted(item, records)
    if rec:
        return f"you accepted it on {rec.get('date', '?')} (\"keep as is\"); run accept.py --forget for it first if you want it adopted"
    if item['cls'] != 'unmanaged':
        return f"it is {item['cls']}, not unmanaged; only unmanaged items are adopted"
    if status != 'ok':
        return 'the manager could not be read in the audit, so ownership cannot be ruled out'
    return None


def app_names(artifacts):
    """The bundle names a cask installs (the `target` form wins over the source name)."""
    names = []
    for a in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(a, dict):
            continue
        for v in a.get('app', []) if isinstance(a.get('app'), list) else []:
            if isinstance(v, str):
                names.append(os.path.basename(v))
            elif isinstance(v, dict) and isinstance(v.get('target'), str) and names:
                names[-1] = os.path.basename(v['target'])
    return names


def cask_gate(cask, bundle_name):
    """Why a cask cannot be used for this installed app, or None. `cask` is one entry of `brew info --cask --json=v2`."""
    if not isinstance(cask, dict):
        return 'Homebrew gave no usable cask data'
    if cask.get('disabled') or cask.get('deprecated'):
        return 'the cask is deprecated or disabled'
    keys = {k for a in (cask.get('artifacts') or []) if isinstance(a, dict) for k in a}
    extra = sorted(keys - SAFE_ARTIFACTS)
    if extra:
        return f"the cask uses {', '.join(extra)}, which runs an installer or script; only plain app copies are adopted"
    names = app_names(cask.get('artifacts'))
    if len(names) != 1:
        return f'the cask installs {len(names)} apps, not exactly one'
    if names[0] != bundle_name:
        return f'the cask installs `{names[0]}`, but this app is `{bundle_name}`: a name match is not the same product'
    deps = cask.get('depends_on') or {}
    if not isinstance(deps, dict):
        return 'the cask\'s dependencies could not be read'
    if set(deps) - SAFE_DEPENDS:
        return 'the cask depends on other software it would install: ' + ', '.join(sorted(set(deps) - SAFE_DEPENDS))
    return None


def installed_versions(app_path):
    """(short, build) from the app's Info.plist, or (None, None). Only these two keys are read."""
    try:
        with open(os.path.join(app_path, 'Contents', 'Info.plist'), 'rb') as f:
            d = plistlib.load(f)
        s, b = d.get('CFBundleShortVersionString'), d.get('CFBundleVersion')
        return (s if isinstance(s, str) else None, b if isinstance(b, str) else None)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None, None


def mode_for(app_path, cask):
    """'adopt' when the installed app reports the cask's version (Homebrew then compares the bundles itself), else 'replace'."""
    short, _ = installed_versions(app_path)
    want = str(cask.get('version', ''))
    return 'adopt' if short and want and short in (want, want.split(',')[0]) else 'replace'


def running_gate(app_path, ps_lines):
    """Why the app cannot be touched now, or None. `ps_lines` is None when the process list could not be read."""
    if ps_lines is None:
        return 'could not tell whether it is running (the process list could not be read)'
    marker = app_path.rstrip('/') + '/Contents/'
    if any(marker in line for line in ps_lines):
        return 'it is running: quit it first'
    return None


def replace_gate(app_path, home, trash_dev, dev_of, allowed_dirs=None):
    """Why the old app cannot be moved aside before a plain install, or None."""
    if os.path.islink(app_path) or not os.path.isdir(app_path):
        return 'it is not a real folder on disk (a link, or gone)'
    parent = os.path.dirname(app_path.rstrip('/'))
    dirs = allowed_dirs or ('/Applications', os.path.join(home, 'Applications'))
    if parent not in dirs:
        return f'it is in {parent}, not /Applications or ~/Applications'
    if not (os.access(parent, os.W_OK) and os.access(app_path, os.W_OK)):
        return ('this terminal is not allowed to modify it (macOS App Management protection, or it belongs to another user): grant the terminal App Management in '
                'System Settings > Privacy & Security, or move the app yourself; this tool never uses admin rights')
    if dev_of(app_path) != trash_dev:
        return 'it is on another volume than the Trash, so it cannot be moved back by a rename'
    return None


def registry_gate(name, backend, registry):
    """Why a command cannot be installed through mise, or None. `registry` maps short name to backends, from `mise registry`."""
    if not NAME_RE.match(name or ''):
        return 'its name is not a plain tool name'
    backends = registry.get(name)
    if not backends:
        return f'`mise registry` has no tool named `{name}`'
    if backends[0] != backend:
        return f'the registry now lists `{backends[0]}`, not `{backend}`: the audit is out of date'
    if not BACKEND_RE.match(backend):
        return f"its backend `{backend}` is not aqua or core (other backends run plugin scripts or have no checksum story)"
    return None


def tool_claimed(ls_json, name):
    """The mise entry for `name` in `mise ls --json`, or None. Keys may carry a backend (`aqua:owner/name`)."""
    if not isinstance(ls_json, dict):
        return None
    for key, versions in ls_json.items():
        if key.split(':')[-1].split('/')[-1] == name and isinstance(versions, list):
            return versions
    return None
