"""A self-contained HTML map of what is installed and who owns it (one inline script pinned by hash, no network at all).

The treemap groups things by who owns them and sizes each block by disk usage. Class is shown by fill pattern and a
glyph as well as colour, so nothing depends on colour alone. It names folders, apps and commands on this machine, so it
is a local file: it says so at the top, and nothing in it is fetched or sent anywhere.
"""
import html

import identity, mapjs
from accepted import has_control
from common import age_text, tilde

GLYPH = {'managed': '●', 'owned': '●', 'unmanaged': '◇', 'orphaned': '▲', 'referenced': '◐', 'unknown': '?', 'missing': '○', 'unattributed': '·', 'config': '–', 'system': '–', 'more': '…'}
LABEL = {'managed': 'Managed by a package manager', 'owned': 'Folder of an installed tool', 'unmanaged': 'Unmanaged (neutral)',
         'orphaned': 'Orphan candidate', 'referenced': 'Referenced or recently used', 'unknown': 'Unknown', 'missing': 'Declared but not installed', 'unattributed': 'Unattributed',
         'config': 'Personal config', 'system': 'System'}
FORMULA_GROUPS = {'brew': 'Homebrew formulae', 'mise': 'mise tools', 'npm': 'npm globals', 'pnpm': 'pnpm globals', 'uv': 'uv tools', 'rustup': 'rustup toolchains'}
APP_GROUPS = {'brew': 'Apps: Homebrew casks', 'mas': 'Apps: App Store', 'manual': 'Apps: installed by hand', 'apple': 'Apps: Apple', 'unknown': 'Apps: unknown'}
DOT_GROUPS = {'owned': 'Installed tools: dot-folders', 'orphaned': 'Orphan candidates: dot-folders', 'referenced': 'Referenced: dot-folders',
              'unattributed': 'Unattributed: dot-folders', 'config': 'Personal config: dot-folders', 'unmanaged': 'Self-installed: dot-folders', 'unknown': 'Unknown: dot-folders', 'system': 'System: dot-folders'}
MAX_LEAVES = 70
MAX_ZOOM = 400
MGR_LABEL = {'brew': 'Homebrew', 'mise': 'mise', 'npm': 'npm globals', 'pnpm': 'pnpm globals', 'uv': 'uv tools', 'cargo': 'cargo crates', 'rustup': 'rustup toolchains',
             'mas': 'App Store apps', 'apple': 'Apple apps', 'manual': 'Apps installed by hand'}


def e(s):
    return html.escape(str(s), quote=True)


def human(kb):
    v = float(kb or 0)
    for unit in ('KB', 'MB', 'GB', 'TB'):
        if v < 1024 or unit == 'TB':
            return f'{v:.0f} {unit}' if unit == 'KB' else f'{v:.1f} {unit}'
        v /= 1024


def squarify(sizes, x, y, w, h):
    """Squarified treemap (Bruls et al.): rects (x, y, w, h) for sizes sorted descending."""
    tot = float(sum(sizes)) or 1.0
    areas = [s * w * h / tot for s in sizes]
    rects = []

    def worst(row, side):
        s = sum(row)
        return max(max(side * side * r / (s * s), (s * s) / (side * side * r)) for r in row)

    def place(row, x, y, w, h):
        s = sum(row)
        if w >= h:
            cw, cy = s / h, y
            for r in row:
                rects.append((x, cy, cw, r / cw))
                cy += r / cw
            return x + cw, y, w - cw, h
        rh, cx = s / w, x
        for r in row:
            rects.append((cx, y, r / rh, rh))
            cx += r / rh
        return x, y + rh, w, h - rh

    row, i = [], 0
    while i < len(areas):
        side = min(w, h)
        if not row or worst(row + [areas[i]], side) <= worst(row, side):
            row.append(areas[i])
            i += 1
        else:
            x, y, w, h = place(row, x, y, w, h)
            row = []
    if row:
        place(row, x, y, w, h)
    return rects


def tip(i):
    a = age_text(i.get('installed_at'), i.get('date_source'))
    return '\n'.join(i['evidence']) + (f'\nthis version installed {a}' if a else '')


def item_inner(i, dup):
    """The evidence for one item, as escaped markup. Shared by the lists and the detail panel."""
    ev = ''.join(f'<li>{e(x)}</li>' for x in i['evidence'])
    refs = f'<p><b>Mentioned by:</b> {e(", ".join(i["referenced_by"]))}</p>' if i['referenced_by'] else ''
    flags = f'<p><b>Flags:</b> {e(", ".join(i["flags"]))}</p>' if i['flags'] else ''
    a = age_text(i.get('installed_at'), i.get('date_source'))
    flags += f'<p><b>This version installed:</b> {e(a)}</p>' if a else ''
    sug = ''
    if i.get('suggestions'):
        sug = ('<p><b>Candidates</b> (suggestions only, nothing is installed; a name match is not proof):</p><ul>'
               + ''.join(f"<li>{('<code>' + e(x['command']) + '</code> ') if x.get('command') else ''}{e(x['candidate'])}: {e(x['evidence'])}</li>" for x in i['suggestions']) + '</ul>')
    d = dup.get(identity.canon(i['name'])) if i.get('kind') in ('binary', 'app') else None
    dups = f'<p><b>Duplicate:</b> also installed by {e(", ".join(d))}</p>' if d else ''
    acc = ''
    if i.get('accepted'):
        acc = f"<p><b>Accepted</b> on {e(i['accepted']['date'])}: {e(i['accepted']['note'])}</p>"
    elif i.get('acceptance_void'):
        v = i['acceptance_void']
        acc = f"<p><b>An earlier acceptance no longer applies</b> (it was {e(v['cls'])}, it is {e(i['cls'])} now). Your note was: {e(v['note'])}</p>"
    return f'<ul>{ev}</ul>{refs}{flags}{sug}{dups}{acc}<p class="key"><b>Key:</b> <code>{e(i["key"])}</code></p>'


def select_button(key, cls):
    if has_control(key):
        return '<p class="note">The name has control characters, so it cannot be selected here: use --key by hand.</p>'
    return f'<button type="button" class="sel" data-key="{e(key)}" data-cls="{e(cls)}">Select</button>'


def leaves(rep):
    """Everything with a size, as {group, name, kb, cls, tip, key, acc, body}. `body` is escaped markup for the detail panel."""
    out = []
    home = rep['home']
    prunable = {i['name'] for i in rep['items'] if i['kind'] == 'mise-prunable'}
    dup = {d['tool']: sorted({c['owner'] for c in d['copies']}) for d in rep.get('duplicates', [])}
    for owner, pk in rep['inventory'].items():
        for p in pk:
            if p['kind'] in ('formula', 'tool', 'package', 'toolchain') and p.get('kb'):
                name = f"{p['name']} {p.get('version', '')}".strip()
                cls, extra, lines = 'managed', '', []
                if f"{p['name']}@{p.get('version', '')}" in prunable:
                    cls, extra = 'orphaned', "\nmise says this version is prunable (see the list below)"
                    lines.append('mise says this version is prunable (see the orphan list)')
                if p['kind'] == 'formula':
                    role = 'requested by you' if p.get('requested') else 'installed as a dependency'
                    extra += '\n' + role
                    lines.append(role)
                also = [o for o in dup.get(identity.canon(p['name']), []) if o != owner]
                if also:
                    extra += '\nduplicate: also installed by ' + ', '.join(also)
                    lines.append('duplicate: also installed by ' + ', '.join(also))
                a = age_text(p.get('installed_at'), p.get('date_source'))
                if a:
                    lines.append(f'this version installed {a}')
                body = (f'<h3>{e(name)} <span class="chip c-{cls}">{GLYPH[cls]} {e(LABEL[cls])}</span></h3><p class="note">{e(MGR_LABEL.get(owner, owner))} {e(p["kind"])}, {human(p["kb"])}</p>'
                        + (f'<p class="note">{e(tilde(p["path"], home))}</p>' if p.get('path') else '') + ''.join(f'<p>{e(x)}</p>' for x in lines))
                out.append({'group': FORMULA_GROUPS.get(owner, owner), 'name': name, 'kb': p['kb'], 'cls': cls, 'key': None, 'acc': False, 'body': body,
                            'tip': f'{owner} {p["kind"]}{extra}' + (f'\nthis version installed {a}' if a else '')})
    for i in rep['items']:
        if not i.get('kb'):
            continue
        if i['kind'] == 'app':
            key = 'manual' if i['cls'] == 'unmanaged' else i['owner'] if i['owner'] in APP_GROUPS else 'unknown'
            group = APP_GROUPS[key]
        elif i['kind'] == 'dotitem':
            group = DOT_GROUPS.get(i['cls'], 'Other: dot-folders')
        elif i['kind'] == 'binary' and i['cls'] == 'unmanaged':
            group = 'Unmanaged commands'
        else:
            continue
        body = (f'<h3>{e(i["name"])} <span class="chip c-{i["cls"]}">{GLYPH[i["cls"]]} {e(LABEL[i["cls"]])}</span>'
                + (' <span class="chip">✓ accepted</span>' if i.get('accepted') else '') + f'</h3><p class="note">{e(tilde(i["path"], home) if i["path"] else i["name"])}, {human(i["kb"])}</p>'
                + item_inner(i, dup) + select_button(i['key'], i['cls']))
        out.append({'group': group, 'name': i['name'], 'kb': i['kb'], 'cls': i['cls'], 'tip': tip(i), 'key': i['key'], 'acc': bool(i.get('accepted')), 'body': body})
    return out


def groups_of(items):
    groups = {}
    for it in items:
        groups.setdefault(it['group'], []).append(it)
    gsum = {g: sum(i['kb'] for i in v) for g, v in groups.items()}
    return groups, gsum, sorted(gsum, key=lambda g: -gsum[g])


def block(it, x, y, w, h, wmin=7, hmin=6):
    glyph = '✓' if it.get('acc') else GLYPH[it['cls']]
    tp = f"{it['name']}\n{human(it['kb'])} · {LABEL.get(it['cls'], 'Combined')}" + (' · accepted by you' if it.get('acc') else '') + f"\n{it['tip']}"
    label = ''
    if w > wmin and h > hmin:
        label = f"<b>{glyph} {e(it['name'][:40])}</b><i>{human(it['kb'])}</i>"
    elif w > wmin / 2 and h > hmin * 2 / 3:
        label = f"<i>{human(it['kb'])}</i>"
    act = ''
    if it.get('id'):
        act = f' data-n="{e(it["id"])}" tabindex="0" role="button"'
    elif it.get('go'):
        act = f' data-go="{e(it["go"])}" tabindex="0" role="button"'
    return (f'<div class="t c-{it["cls"]}{" acc" if it.get("acc") else ""}" title="{e(tp)}" data-name="{e(it["name"].lower())}" data-cls="{e(it["cls"])}" data-acc="{1 if it.get("acc") else 0}"{act} '
            f'style="left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%">{label}</div>')


def treemap(items):
    """The overview: every group, its biggest blocks, and one combined block for the rest (which opens the group)."""
    groups, gsum, order = groups_of(items)
    if not order:
        return ''
    out = []
    for gi, (g, (gx, gy, gw, gh)) in enumerate(zip(order, squarify([gsum[g] for g in order], 0, 0, 100, 100))):
        its = sorted(groups[g], key=lambda i: -i['kb'])
        if len(its) > MAX_LEAVES:
            rest = its[MAX_LEAVES - 1:]
            its = its[:MAX_LEAVES - 1] + [{'group': g, 'name': f'{len(rest)} smaller items', 'kb': sum(i['kb'] for i in rest), 'cls': 'more', 'go': f'g{gi}', 'tip': 'Combined: open the group to see each one'}]
        out.append(f'<div class="grp" style="left:{gx:.3f}%;top:{gy:.3f}%;width:{gw:.3f}%;height:{gh:.3f}%"><button type="button" class="gl" data-go="g{gi}" title="Open this group">{e(g)} · {human(gsum[g])}</button></div>')
        ix, iy, iw, ih = gx + 0.15, gy + 3.4, max(0.1, gw - 0.3), max(0.1, gh - 3.6)
        for it, (x, y, w, h) in zip(its, squarify([i['kb'] for i in its], ix, iy, iw, ih)):
            out.append(block(it, x, y, w, h))
    return ''.join(out)


def group_views(items):
    """One hidden, complete layout per group, so drilling in shows every item. Computed here, never in the browser."""
    groups, gsum, order = groups_of(items)
    out = []
    for gi, g in enumerate(order):
        its = sorted(groups[g], key=lambda i: -i['kb'])
        if len(its) > MAX_ZOOM:
            rest = its[MAX_ZOOM - 1:]
            its = its[:MAX_ZOOM - 1] + [{'group': g, 'name': f'{len(rest)} smaller items', 'kb': sum(i['kb'] for i in rest), 'cls': 'more', 'tip': 'Combined; see --json for each'}]
        inner = ''.join(block(it, x, y, w, h, 4, 4) for it, (x, y, w, h) in zip(its, squarify([i['kb'] for i in its], 0.2, 0.2, 99.6, 99.6)))
        out.append(f'<div class="tm" data-g="g{gi}" data-title="{e(g)} · {human(gsum[g])}" hidden>{inner}</div>')
    return ''.join(out)


def rows(items, home, empty):
    if not items:
        return f'<p class="note">{e(empty)}</p>'
    out = []
    for i in items:
        where = i['path'].replace(home, '~', 1) if i['path'].startswith(home) else (i['path'] or i['name'])
        pick = '<span class="pk"></span>' if has_control(i['key']) else (
            f'<span class="pk"><input type="checkbox" class="pick" data-js hidden data-key="{e(i["key"])}" data-cls="{e(i["cls"])}" aria-label="Select {e(i["name"])}"></span>')
        mark = '<span class="chip">✓ accepted</span>' if i.get('accepted') else ''
        out.append(f'<details class="it" data-name="{e((where + " " + i["name"]).lower())}" data-cls="{e(i["cls"])}" data-acc="{1 if i.get("accepted") else 0}"><summary>{pick}'
                   f'<span class="sz">{human(i["kb"]) if i.get("kb") else ""}</span><span class="nm">{e(where)}</span>'
                   f'<span class="chips">{mark}<span class="chip c-{i["cls"]}">{GLYPH[i["cls"]]} {e(i["cls"])}</span></span></summary>'
                   f'<div class="body">{item_inner(i, {})}</div></details>')
    ticks = '' if all(has_control(i['key']) for i in items) else (
        '<label class="all" data-js hidden><input type="checkbox" class="pickall" aria-label="Select all rows shown in this list"> <span class="allt">Select all shown</span></label>')
    return '<div class="list" data-list>' + ticks + ''.join(out) + '</div>'


def manager_panels(rep):
    """What each manager has installed, as sortable tables. Readable without scripts, in name order."""
    prunable = {i['name'] for i in rep['items'] if i['kind'] == 'mise-prunable'}
    orphans = {i['name'] for i in rep['items'] if i['kind'] == 'brew-orphan'}
    missing = {i['name'] for i in rep['items'] if i['kind'] == 'missing-dependency'}
    dup = {d['tool']: sorted({c['owner'] for c in d['copies']}) for d in rep.get('duplicates', [])}
    owners = sorted(rep['inventory'], key=lambda o: (o not in ('brew', 'mise'), ['brew', 'mise'].index(o) if o in ('brew', 'mise') else 0, o))
    out = []
    for owner in owners:
        pk = sorted(rep['inventory'][owner], key=lambda p: (p['kind'], p['name'].lower()))
        body = ''
        for p in pk:
            flags = []
            if f"{p['name']}@{p.get('version', '')}" in prunable:
                flags.append('prunable (mise)')
            if p['name'] in orphans:
                flags.append('orphan (brew autoremove would remove it)')
            if p['name'] in missing:
                flags.append('missing a dependency')
            if len(dup.get(identity.canon(p['name']), [])) > 1:
                flags.append('duplicate: ' + ', '.join(o for o in dup[identity.canon(p['name'])] if o != owner))
            role = ('requested' if p.get('requested') else 'dependency') if p['kind'] == 'formula' else ('bundled' if p.get('bundled') else '')
            cls = 'orphaned' if any(f.startswith(('prunable', 'orphan')) for f in flags) else 'unmanaged' if owner == 'manual' else 'system' if owner == 'apple' else 'managed'
            a = age_text(p.get('installed_at'), p.get('date_source'))
            ver = p.get('version') or ''
            body += (f'<tr data-name="{e((p["name"] + " " + ver).lower())}" data-cls="{cls}" data-acc="0"><td>{e(p["name"])}</td><td>{e(p["kind"])}</td><td>{e(ver)}</td><td>{role}</td>'
                     f'<td data-v="{p.get("kb") or 0}">{human(p["kb"]) if p.get("kb") else ""}</td><td data-v="{int(p.get("installed_at") or 0)}">{e(a)}</td><td>{e("; ".join(flags))}</td></tr>')
        th = ''.join(f'<th><button type="button" data-type="{t}">{c}</button></th>' for c, t in (('Name', 'text'), ('Kind', 'text'), ('Version', 'text'), ('Role', 'text'), ('Size', 'num'), ('Installed', 'num'), ('Flags', 'text')))
        out.append(f'<section class="mgr"><h3>{e(MGR_LABEL.get(owner, owner))} ({len(pk)})</h3><div class="tw"><table class="sortable"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div></section>')
    return ''.join(out)


CSS = '''
:root{--bg:#fafaf8;--fg:#1d2127;--mut:#59616b;--card:#fff;--bd:#d9dde2;--ring:#1d2127;
--managed:#bfe3c7;--managed-fg:#10331b;--unm:#dbe6f5;--unm-fg:#13273f;--orph:#f4e0a0;--orph2:#fbf0cf;--orph-fg:#3d2c00;
--ref:#e7dcf3;--ref-fg:#2b1a42;--unk:#eceff3;--unk-fg:#1f2733;--una:#cfd6df;--una-fg:#1f2733;--cfg:#eef0f3;--cfg-fg:#3a424d}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e6e9ee;--mut:#9aa3ad;--card:#1c2025;--bd:#323840;--ring:#e6e9ee;
--managed:#1f4d2f;--managed-fg:#d6f2dd;--unm:#22364f;--unm-fg:#d6e6fb;--orph:#54421a;--orph2:#483817;--orph-fg:#ffe9ad;
--ref:#3a2c52;--ref-fg:#eadcff;--unk:#2a2f36;--unk-fg:#e6e9ee;--una:#3a424d;--una-fg:#e6e9ee;--cfg:#262b31;--cfg-fg:#c3cad3}}
:root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;overflow-wrap:anywhere}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px}.sub{color:var(--mut);margin:0 0 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:24px}
.tile{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}.tile b{display:block;font-size:22px}.tile span{color:var(--mut);font-size:13px}
.tm{position:relative;width:100%;aspect-ratio:16/10;background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;margin:8px 0 12px}
.grp{position:absolute;border:1px solid var(--ring);pointer-events:none}
.gl{position:absolute;left:6px;top:2px;font-size:12px;font-weight:600;background:var(--card);padding:0 4px;border-radius:3px;max-width:calc(100% - 12px);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.t{position:absolute;overflow:hidden;border:1px solid var(--bg);padding:3px 5px;font-size:11px;line-height:1.25}
.t b{display:block;font-weight:600;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.t i{font-style:normal;opacity:.85}
.t:hover{outline:2px solid var(--ring);outline-offset:-2px;z-index:2}
.c-managed,.c-owned{background:var(--managed);color:var(--managed-fg)}
.c-unmanaged{background:var(--unm);color:var(--unm-fg);box-shadow:inset 0 0 0 2px var(--unm-fg)}
.c-orphaned{background:repeating-linear-gradient(45deg,var(--orph) 0 7px,var(--orph2) 7px 14px);color:var(--orph-fg)}
.c-referenced{background:radial-gradient(color-mix(in srgb,var(--ring) 38%,transparent) 1px,transparent 1.6px) 0 0/9px 9px,var(--ref);color:var(--ref-fg)}
.c-unknown{background:repeating-linear-gradient(-45deg,var(--unk) 0 4px,var(--bd) 4px 5px);color:var(--unk-fg)}
.c-unattributed{background:var(--una);color:var(--una-fg)}.c-config,.c-system,.c-more{background:var(--cfg);color:var(--cfg-fg)}
.legend{display:flex;flex-wrap:wrap;gap:14px;color:var(--mut);font-size:13px;margin-bottom:20px}.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:16px;height:12px;border:1px solid var(--ring)}
table{border-collapse:collapse;margin:6px 0;display:block;max-width:100%;overflow-x:auto}.tw table{display:table}th,td{border:1px solid var(--bd);padding:6px 12px;text-align:left}th{background:var(--card)}
.warn{background:var(--orph);color:var(--orph-fg);border:1px solid var(--ring);border-radius:8px;padding:10px 14px;margin:12px 0}
.note{color:var(--mut);font-size:13px;margin:6px 0}
[hidden]{display:none!important}
.js .t[data-n],.js .t[data-go],.js .gl[data-go]{cursor:pointer}
.gl{pointer-events:auto;border:0;font:inherit;font-size:12px;font-weight:600;color:inherit;text-align:left}
.t:focus-visible,.gl:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--ring);outline-offset:2px}
.t.acc{box-shadow:inset 0 0 0 3px var(--card),inset 0 0 0 5px var(--ring)}
.acc-sw{background:var(--card);box-shadow:inset 0 0 0 2px var(--ring)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px;position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0}
.bar input[type=search]{flex:1 1 220px;min-width:160px;padding:8px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:5px 12px;cursor:pointer}
button[aria-pressed=true]{background:var(--fg);color:var(--bg);border-color:var(--fg)}button:disabled{opacity:.45;cursor:not-allowed}
#crumbs{margin:6px 0}#crumb-name{font-weight:600;margin-left:8px}
#detail{background:var(--card);border:1px solid var(--ring);border-radius:10px;padding:12px 16px;margin:10px 0}#detail h3{margin:0 0 4px;font-size:16px}
#detail-close{float:right}.key code{user-select:all}
#selbar{position:sticky;bottom:0;z-index:5;background:var(--card);border:1px solid var(--ring);border-radius:10px;padding:10px 14px;margin-top:16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
#selbar textarea{width:100%;font:12px ui-monospace,Menlo,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:6px}
.mgr{margin:10px 0}.mgr h3{font-size:15px;margin:14px 0 4px}.tw{overflow-x:auto;max-height:420px;overflow-y:auto;border:1px solid var(--bd);border-radius:8px}
.tw table{margin:0;width:100%;font-size:13px}.tw th{position:sticky;top:0}.tw th button{border:0;background:none;padding:0;font-weight:600;cursor:pointer}
th[aria-sort=ascending] button::after{content:' ▲'}th[aria-sort=descending] button::after{content:' ▼'}
.pk{display:inline-flex;justify-content:center}.chips{display:inline-flex;gap:6px}
.all{display:flex;gap:6px;align-items:center;padding:6px 2px;font-size:13px;color:var(--mut);border-bottom:1px solid var(--bd)}
.it{border-bottom:1px solid var(--bd)}.it>summary{display:grid;grid-template-columns:24px 78px 1fr auto;gap:10px;align-items:center;padding:8px 2px;cursor:pointer;list-style:none}
.it>summary::-webkit-details-marker{display:none}.sz{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}.nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--ring);white-space:nowrap}
.body{padding:4px 8px 14px 114px}.body ul{margin:4px 0 8px;padding-left:20px}.body p{margin:6px 0}
code{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:1px 6px;font:12px ui-monospace,Menlo,monospace;word-break:break-all}
@media (max-width:640px){.it>summary{grid-template-columns:24px 64px 1fr}.chips{display:none}.body{padding-left:8px}.bar{position:static}}
'''


def candidates_section(rep):
    ck = rep.get('checker') or {}
    if not ck.get('ran'):
        return ''
    cand = [i for i in rep['items'] if i['cls'] == 'unmanaged' and i['suggestions']]
    h = (f'<h2>Adoption candidates ({len(cand)})</h2><p class="note">Suggestions only: nothing is installed, and a name match is not proof that two things are the same product. '
         f"Checked against Homebrew's name index ({e(ck.get('index', 'unknown'))}) and the mise registry.</p>")
    for k, label in (('brew', 'Homebrew'), ('mise', 'mise registry')):
        if ck.get(k) not in (None, 'ok', 'not needed'):
            h += f'<div class="warn"><b>{label}: {e(ck[k])}.</b> "No candidate" is not said for what it would have checked.</div>'
    if cand:
        h += ('<table><thead><tr><th>Item</th><th>Candidate</th><th>Command (not run)</th><th>Basis</th></tr></thead><tbody>'
              + ''.join(f"<tr><td>{e(i['name'])} ({e(i['kind'])})</td><td>{e(s_['candidate'])}</td><td>{e(s_.get('command', ''))}</td><td>{e(s_['evidence'])}</td></tr>"
                        for i in sorted(cand, key=lambda i: i['name'].lower()) for s_ in i['suggestions']) + '</tbody></table>')
    return h


def missing_section(rep):
    items = rep['items']
    miss = [i for i in items if i['cls'] == 'missing']
    dead = [i for i in items if i['kind'] == 'path-dead']
    checks = rep.get('missing_checks', [])
    done = [c['source'] for c in checks if c['status'] == 'checked']
    bad = [c['source'] for c in checks if c['status'] == 'failed']
    h = f'<h2>Missing toolchains and dead PATH entries ({len(miss)} missing)</h2>'
    if miss:
        h += ('<p class="note">Declared or expected by a manager, but not installed. Not orphans.</p><table><thead><tr><th>What</th><th>Reported by</th><th>Evidence</th></tr></thead><tbody>'
              + ''.join(f"<tr><td>{e(i['name'])}</td><td>{e(i['owner'])}</td><td>{e('; '.join(i['evidence']))}</td></tr>" for i in miss) + '</tbody></table>')
    elif done:
        h += f'<p class="note">None reported by {e(", ".join(done))}.</p>'
    if bad:
        h += f'<div class="warn"><b>Could not check {e(", ".join(bad))},</b> so nothing is said about what they might be missing.</div>'
    if dead:
        h += ("<p class=\"note\">PATH entries (on the audit's PATH, which may differ from your shell's) that point nowhere: "
              + e('; '.join(tilde(i['path'], rep['home']) + ' (' + i['evidence'][0] + ')' for i in dead)) + '</p>')
    return h


def dup_section(rep):
    dups, vers = rep.get('duplicates', []), rep.get('versions', [])
    h = f'<h2>Duplicate installs ({len(dups)})</h2>'
    if rep.get('duplicates_incomplete'):
        h += '<div class="warn"><b>May be incomplete:</b> ' + e(', '.join(rep['duplicates_incomplete'])) + ' could not be read.</div>'
    if not dups:
        return h + '<p class="note">None across managers.</p>'
    rows_ = ''
    for d in dups:
        a = d['active']
        act = e((a['owner'] + ' ' + a['version']).strip()) if a else 'none found on PATH'
        rows_ += (f"<tr><td>{e(d['tool'])}</td><td>{act}</td><td>{e(', '.join((c['owner'] + ' ' + c['version']).strip() for c in d['copies']))}</td>"
                  f"<td>{e('; '.join(d['notes']))}</td></tr>")
    h += ("<p class=\"note\">The same tool through more than one manager. Active is the first match on the audit's PATH, which may differ from your shell's.</p>"
          f'<table><thead><tr><th>Tool</th><th>Active</th><th>Copies</th><th>Notes</th></tr></thead><tbody>{rows_}</tbody></table>')
    if vers:
        h += '<p class="note">Several versions under one manager: ' + e('; '.join(f"{v['tool']}: {v['owner']} {', '.join(v['versions'])}" for v in vers)) + '</p>'
    return h


def render(rep):
    home, items = rep['home'], rep['items']
    cls = lambda c, *kinds: [i for i in items if i['cls'] == c and (not kinds or i['kind'] in kinds)]
    lv = leaves(rep)
    for n, node in enumerate(lv):
        node['id'] = f'n{n}'
    orph, ref, unk = cls('orphaned'), cls('referenced'), cls('unknown')
    unm = cls('unmanaged')
    una = sorted(cls('unattributed'), key=lambda i: -(i['kb'] or 0))
    cov = rep.get('coverage')
    sized = sum(i['kb'] for i in lv)
    tl = [('sized on disk', human(sized))]
    if cov:
        tl.append((f"coverage: {cov['green']} green · {cov['accepted']} accepted", f"{cov['unresolved']} unresolved"))
    tl += [('orphan candidates', f"{len(orph)} · {human(sum(i['kb'] or 0 for i in orph))}"), ('referenced or in use', len(ref)), ('unmanaged', len(unm)), ('unknown', len(unk))]
    tiles = ''.join(f'<div class="tile"><b>{e(v)}</b><span>{e(k)}</span></div>' for k, v in tl)
    src = ''.join(f"<tr><td>{e(a['name'])}</td><td>{e(a['status'])}</td><td>{a['packages']}</td><td>{e(a['detail'][:100])}</td></tr>" for a in rep['adapters'])
    banner = ''
    if rep['uncertain']:
        banner = '<div class="warn"><b>Some sources could not be read,</b> so every "unmanaged" and "orphaned" verdict below is shown as unknown: ' + e('; '.join(rep['uncertain'])) + '</div>'
    if rep.get('accepted_warning'):
        banner += '<div class="warn"><b>Accepted state:</b> ' + e(rep['accepted_warning']) + '</div>'
    legend = ''.join(f'<span><i class="sw c-{c}"></i>{GLYPH[c]} {e(LABEL[c])}</span>' for c in ('managed', 'unmanaged', 'orphaned', 'referenced', 'unknown', 'unattributed', 'config')) \
        + '<span><i class="sw acc-sw"></i>✓ Accepted by you (kept on purpose)</span>'
    chips = ''.join(f'<button type="button" data-f="{c}" aria-pressed="false">{e(l)}</button>' for c, l in (
        ('orphaned', 'Orphan candidates'), ('unmanaged', 'Unmanaged'), ('unattributed', 'Unattributed'), ('referenced', 'Referenced'), ('unknown', 'Unknown'), ('missing', 'Missing'), ('managed', 'Managed'), ('accepted', 'Accepted')))
    toolbar = (f'<div class="bar" data-js hidden role="search"><input id="q" type="search" placeholder="Search names" aria-label="Search names">{chips}'
               '<span id="shown" role="status" aria-live="polite" class="note"></span></div>')
    store = ''.join(f'<div data-d="{e(n["id"])}">{n["body"]}</div>' for n in lv)
    selbar = ('<div id="selbar" hidden role="region" aria-label="Selection"><span id="selcount"></span>'
              '<button type="button" id="copy-accept">Copy accept command</button><button type="button" id="copy-tidy">Copy tidy command</button>'
              '<button type="button" id="clear">Clear</button><span id="msg" role="status" aria-live="polite" class="note"></span>'
              '<textarea id="copybox" readonly hidden rows="5" aria-label="Text to copy"></textarea></div>')
    return ('<!doctype html>\n<html lang="en"><head>'
            f'<meta http-equiv="Content-Security-Policy" content="{e(mapjs.csp())}"><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><title>Dotfiles map</title><style>' + CSS + '</style></head><body><main>'
            '<h1>What is installed, and who owns it</h1>'
            f'<p class="sub">Read-only audit, {e(rep["generated"][:10])}. This page names folders, apps and commands on this machine: keep it local. It makes no network requests, and nothing in it changes anything: it only helps you choose and copy.</p>'
            + banner + f'<div class="tiles">{tiles}</div>' + toolbar +
            '<h2>Where the space is, by owner</h2>'
            '<p class="note">Click a group name to open it in full, and a block for its details.</p>'
            '<nav id="crumbs" hidden><button type="button" data-back>← All groups</button><span id="crumb-name"></span></nav>'
            f'<div id="views"><div class="tm" id="tm-overview" role="group" aria-label="Treemap of installed things by owner and size">{treemap(lv)}</div>{group_views(lv)}</div>'
            '<aside id="detail" hidden><button type="button" id="detail-close">Close</button><div id="detail-body"></div></aside>'
            f'<div id="dstore" hidden>{store}</div>'
            f'<div class="legend">{legend}</div>'
            '<p class="note">Block size is disk usage. Only things with a measured size are drawn; commands from managers are inside their package.</p>'
            + candidates_section(rep) + missing_section(rep) + dup_section(rep) +
            '<h2>What each manager has installed</h2><p class="note">Sorted by name; click a heading to sort.</p>' + manager_panels(rep) +
            f'<h2>Sources</h2><table><thead><tr><th>Source</th><th>Status</th><th>Packages</th><th>Note</th></tr></thead><tbody>{src}</tbody></table>'
            f'<p class="note">Not read by this version: {e(", ".join(rep["not_covered"]))}. Anything that resolves into their folders is shown as unknown.</p>'
            f'<h2>Orphan candidates ({len(orph)})</h2><p class="note">Candidates, not verdicts. Tick rows to copy the keys of /dotfiles:tidy or /dotfiles:accept.</p>'
            + rows(sorted(orph, key=lambda i: -(i['kb'] or 0)), home, 'None.')
            + f'<h2>Referenced or recently used, but not installed ({len(ref)})</h2>' + rows(sorted(ref, key=lambda i: -(i['kb'] or 0)), home, 'None.')
            + f'<h2>Unknown ({len(unk)})</h2>' + rows(unk[:60], home, 'None.')
            + f'<h2>Unmanaged ({len(unm)})</h2><p class="note">Installed, but no package manager claims them. Neutral: how they got here, not a to-do.</p>'
            + rows(sorted(unm, key=lambda i: -(i['kb'] or 0))[:80], home, 'None.')
            + f'<h2>Unattributed dot-items ({len(una)})</h2><p class="note">Folders and files the knowledge table has no entry for, so nothing says who made them. Never guessed, never offered for removal: tick the ones you recognise and copy /dotfiles:accept.'
            + (f' Showing the largest 80 of {len(una)}.' if len(una) > 80 else '') + '</p>'
            + rows(una[:80], home, 'None.')
            + selbar + '</main><script>' + mapjs.SCRIPT + '</script></body></html>')
