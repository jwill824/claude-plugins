"""A self-contained HTML map of what is installed and who owns it (inline CSS, no scripts, no network).

The treemap groups things by who owns them and sizes each block by disk usage. Class is shown by fill pattern and a
glyph as well as colour, so nothing depends on colour alone. It names folders, apps and commands on this machine, so it
is a local file: it says so at the top, and nothing in it is fetched or sent anywhere.
"""
import html

import identity
from common import age_text

GLYPH = {'managed': '●', 'owned': '●', 'unmanaged': '◇', 'orphaned': '▲', 'referenced': '◐', 'unknown': '?', 'unattributed': '·', 'config': '–', 'system': '–', 'more': '…'}
LABEL = {'managed': 'Managed by a package manager', 'owned': 'Folder of an installed tool', 'unmanaged': 'Unmanaged (neutral)',
         'orphaned': 'Orphan candidate', 'referenced': 'Referenced or recently used', 'unknown': 'Unknown', 'unattributed': 'Unattributed',
         'config': 'Personal config', 'system': 'System'}
FORMULA_GROUPS = {'brew': 'Homebrew formulae', 'mise': 'mise tools', 'npm': 'npm globals', 'uv': 'uv tools', 'rustup': 'rustup toolchains'}
APP_GROUPS = {'brew': 'Apps: Homebrew casks', 'mas': 'Apps: App Store', 'manual': 'Apps: installed by hand', 'apple': 'Apps: Apple', 'unknown': 'Apps: unknown'}
DOT_GROUPS = {'owned': 'Installed tools: dot-folders', 'orphaned': 'Orphan candidates: dot-folders', 'referenced': 'Referenced: dot-folders',
              'unattributed': 'Unattributed: dot-folders', 'config': 'Personal config: dot-folders', 'unmanaged': 'Self-installed: dot-folders', 'unknown': 'Unknown: dot-folders', 'system': 'System: dot-folders'}
MAX_LEAVES = 70


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


def leaves(rep):
    """Everything with a size, as {group, name, kb, cls, tip}."""
    out = []
    prunable = {i['name'] for i in rep['items'] if i['kind'] == 'mise-prunable'}
    dup = {d['tool']: sorted({c['owner'] for c in d['copies']}) for d in rep.get('duplicates', [])}
    for owner, pk in rep['inventory'].items():
        for p in pk:
            if p['kind'] in ('formula', 'tool', 'package', 'toolchain') and p.get('kb'):
                name = f"{p['name']} {p.get('version', '')}".strip()
                cls, extra = 'managed', ''
                if f"{p['name']}@{p.get('version', '')}" in prunable:
                    cls, extra = 'orphaned', "\nmise says this version is prunable (see the list below)"
                if p['kind'] == 'formula':
                    extra += '\nrequested by you' if p.get('requested') else '\ninstalled as a dependency'
                also = [o for o in dup.get(identity.canon(p['name']), []) if o != owner]
                if also:
                    extra += '\nduplicate: also installed by ' + ', '.join(also)
                a = age_text(p.get('installed_at'), p.get('date_source'))
                out.append({'group': FORMULA_GROUPS.get(owner, owner), 'name': name, 'kb': p['kb'], 'cls': cls,
                            'tip': f'{owner} {p["kind"]}{extra}' + (f'\nthis version installed {a}' if a else '')})
    for i in rep['items']:
        if not i.get('kb'):
            continue
        if i['kind'] == 'app':
            key = 'manual' if i['cls'] == 'unmanaged' else i['owner'] if i['owner'] in APP_GROUPS else 'unknown'
            out.append({'group': APP_GROUPS[key], 'name': i['name'], 'kb': i['kb'], 'cls': i['cls'], 'tip': tip(i)})
        elif i['kind'] == 'dotitem':
            out.append({'group': DOT_GROUPS.get(i['cls'], 'Other: dot-folders'), 'name': i['name'], 'kb': i['kb'], 'cls': i['cls'], 'tip': tip(i)})
        elif i['kind'] == 'binary' and i['cls'] == 'unmanaged':
            out.append({'group': 'Unmanaged commands', 'name': i['name'], 'kb': i['kb'], 'cls': 'unmanaged', 'tip': tip(i)})
    return out


def treemap(items):
    groups = {}
    for it in items:
        groups.setdefault(it['group'], []).append(it)
    gsum = {g: sum(i['kb'] for i in v) for g, v in groups.items()}
    order = sorted(gsum, key=lambda g: -gsum[g])
    if not order:
        return ''
    out = []
    for g, (gx, gy, gw, gh) in zip(order, squarify([gsum[g] for g in order], 0, 0, 100, 100)):
        its = sorted(groups[g], key=lambda i: -i['kb'])
        if len(its) > MAX_LEAVES:
            rest = its[MAX_LEAVES - 1:]
            its = its[:MAX_LEAVES - 1] + [{'group': g, 'name': f'{len(rest)} smaller items', 'kb': sum(i['kb'] for i in rest), 'cls': 'more', 'tip': 'Combined; see --json for each'}]
        out.append(f'<div class="grp" style="left:{gx:.3f}%;top:{gy:.3f}%;width:{gw:.3f}%;height:{gh:.3f}%"><span class="gl">{e(g)} · {human(gsum[g])}</span></div>')
        ix, iy, iw, ih = gx + 0.15, gy + 3.4, max(0.1, gw - 0.3), max(0.1, gh - 3.6)
        for it, (x, y, w, h) in zip(its, squarify([i['kb'] for i in its], ix, iy, iw, ih)):
            tip = f"{it['name']}\n{human(it['kb'])} · {LABEL.get(it['cls'], 'Combined')}\n{it['tip']}"
            label = ''
            if w > 7 and h > 6:
                label = f"<b>{GLYPH[it['cls']]} {e(it['name'][:40])}</b><i>{human(it['kb'])}</i>"
            elif w > 3.5 and h > 4:
                label = f"<i>{human(it['kb'])}</i>"
            out.append(f'<div class="t c-{it["cls"]}" title="{e(tip)}" style="left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%">{label}</div>')
    return ''.join(out)


def rows(items, home, empty):
    if not items:
        return f'<p class="note">{e(empty)}</p>'
    out = []
    for i in items:
        ev = ''.join(f'<li>{e(x)}</li>' for x in i['evidence'])
        refs = f'<p><b>Mentioned by:</b> {e(", ".join(i["referenced_by"]))}</p>' if i['referenced_by'] else ''
        flags = f'<p><b>Flags:</b> {e(", ".join(i["flags"]))}</p>' if i['flags'] else ''
        a = age_text(i.get('installed_at'), i.get('date_source'))
        flags += f'<p><b>This version installed:</b> {e(a)}</p>' if a else ''
        where = i['path'].replace(home, '~', 1) if i['path'].startswith(home) else (i['path'] or i['name'])
        out.append(f'<details class="it"><summary><span class="sz">{human(i["kb"]) if i.get("kb") else ""}</span>'
                   f'<span class="nm">{e(where)}</span><span class="chip c-{i["cls"]}">{GLYPH[i["cls"]]} {e(i["cls"])}</span></summary>'
                   f'<div class="body"><ul>{ev}</ul>{refs}{flags}<p class="key"><b>Key:</b> <code>{e(i["key"])}</code></p></div></details>')
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
main{max-width:1100px;margin:0 auto}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px}.sub{color:var(--mut);margin:0 0 20px}
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
table{border-collapse:collapse;margin:6px 0}th,td{border:1px solid var(--bd);padding:6px 12px;text-align:left}th{background:var(--card)}
.warn{background:var(--orph);color:var(--orph-fg);border:1px solid var(--ring);border-radius:8px;padding:10px 14px;margin:12px 0}
.note{color:var(--mut);font-size:13px;margin:6px 0}
.it{border-bottom:1px solid var(--bd)}.it>summary{display:grid;grid-template-columns:78px 1fr auto;gap:10px;align-items:center;padding:8px 2px;cursor:pointer;list-style:none}
.it>summary::-webkit-details-marker{display:none}.sz{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}.nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--ring);white-space:nowrap}
.body{padding:4px 8px 14px 90px}.body ul{margin:4px 0 8px;padding-left:20px}.body p{margin:6px 0}
code{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:1px 6px;font:12px ui-monospace,Menlo,monospace;word-break:break-all}
@media (max-width:640px){.it>summary{grid-template-columns:64px 1fr}.chip{display:none}.body{padding-left:8px}}
'''


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
    orph, ref, unk = cls('orphaned'), cls('referenced'), cls('unknown')
    unm = cls('unmanaged')
    sized = sum(i['kb'] for i in lv)
    tiles = ''.join(f'<div class="tile"><b>{v}</b><span>{k}</span></div>' for k, v in (
        ('sized on disk', human(sized)), ('orphan candidates', f"{len(orph)} · {human(sum(i['kb'] or 0 for i in orph))}"),
        ('referenced or in use', len(ref)), ('unmanaged', len(unm)), ('unknown', len(unk))))
    src = ''.join(f"<tr><td>{e(a['name'])}</td><td>{e(a['status'])}</td><td>{a['packages']}</td><td>{e(a['detail'][:100])}</td></tr>" for a in rep['adapters'])
    banner = ''
    if rep['uncertain']:
        banner = '<div class="warn"><b>Some sources could not be read,</b> so every "unmanaged" and "orphaned" verdict below is shown as unknown: ' + e('; '.join(rep['uncertain'])) + '</div>'
    legend = ''.join(f'<span><i class="sw c-{c}"></i>{GLYPH[c]} {e(LABEL[c])}</span>' for c in ('managed', 'unmanaged', 'orphaned', 'referenced', 'unknown', 'unattributed', 'config'))
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><title>Dotfiles map</title><style>' + CSS + '</style></head><body><main>'
            '<h1>What is installed, and who owns it</h1>'
            f'<p class="sub">Read-only audit, {e(rep["generated"][:10])}. This page names folders, apps and commands on this machine: keep it local. Nothing in it was changed.</p>'
            + banner + f'<div class="tiles">{tiles}</div>'
            '<h2>Where the space is, by owner</h2>'
            f'<div class="tm" role="img" aria-label="Treemap of installed things by owner and size">{treemap(lv)}</div>'
            f'<div class="legend">{legend}</div>'
            '<p class="note">Block size is disk usage. Only things with a measured size are drawn; commands from managers are inside their package.</p>'
            + dup_section(rep) +
            f'<h2>Sources</h2><table><thead><tr><th>Source</th><th>Status</th><th>Packages</th><th>Note</th></tr></thead><tbody>{src}</tbody></table>'
            f'<p class="note">Not read by this version: {e(", ".join(rep["not_covered"]))}. Anything that resolves into their folders is shown as unknown.</p>'
            f'<h2>Orphan candidates ({len(orph)})</h2><p class="note">Candidates, not verdicts. Click a row for the evidence and the key that /dotfiles:tidy takes.</p>'
            + rows(sorted(orph, key=lambda i: -(i['kb'] or 0)), home, 'None.')
            + f'<h2>Referenced or recently used, but not installed ({len(ref)})</h2>' + rows(sorted(ref, key=lambda i: -(i['kb'] or 0)), home, 'None.')
            + f'<h2>Unknown ({len(unk)})</h2>' + rows(unk[:60], home, 'None.')
            + f'<h2>Unmanaged ({len(unm)})</h2><p class="note">Installed, but no package manager claims them. Neutral: how they got here, not a to-do.</p>'
            + rows(sorted(unm, key=lambda i: -(i['kb'] or 0))[:80], home, 'None.')
            + '</main></body></html>')
