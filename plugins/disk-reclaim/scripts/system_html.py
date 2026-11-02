"""Self-contained HTML report for scan_system.py (inline CSS, no scripts, no network).

Tier is encoded by fill pattern as well as colour (SAFE solid, needs-review striped,
leave-alone dotted) and activity by a glyph, so nothing depends on colour alone.
"""
import html

GLYPH = {'ACTIVE': '●', 'RECENT': '◐', 'STALE': '○', 'UNKNOWN': '?'}
TIER_NAME = {'SAFE': 'Safe', 'VERIFY': 'Review first', 'KEEP': 'Leave alone'}
GROUPS = ['Tool caches', 'Xcode and simulators', 'Android', 'Docker / Colima', 'Claude data', 'App caches']


def human(kb):
    v = float(kb or 0)
    for unit in ('KB', 'MB', 'GB', 'TB'):
        if v < 1024 or unit == 'TB':
            return f'{v:.0f} {unit}' if unit == 'KB' else f'{v:.1f} {unit}'
        v /= 1024


def e(s):
    return html.escape(str(s), quote=True)


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
        if w >= h:                       # column on the left
            cw, cy = s / h, y
            for r in row:
                rects.append((x, cy, cw, r / cw))
                cy += r / cw
            return x + cw, y, w - cw, h
        rh, cx = s / w, x                # row along the top
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


def treemap(items):
    """Groups laid out first, then items inside each group. Percent coordinates."""
    gsum = {g: sum(i['kb'] or 0 for i in items if i['group'] == g) for g in GROUPS}
    gs = sorted((g for g in GROUPS if gsum[g] > 0), key=lambda g: -gsum[g])
    if not gs:
        return ''
    out = []
    for g, (gx, gy, gw, gh) in zip(gs, squarify([gsum[g] for g in gs], 0, 0, 100, 100)):
        its = sorted((i for i in items if i['group'] == g and (i['kb'] or 0) > 0), key=lambda i: -i['kb'])
        out.append(f'<div class="grp" style="left:{gx:.3f}%;top:{gy:.3f}%;width:{gw:.3f}%;height:{gh:.3f}%"><span class="gl">{e(g)} · {human(gsum[g])}</span></div>')
        pad = 1.2  # keep the group caption readable
        ix, iy, iw, ih = gx + 0.15, gy + pad + 2.2, max(0.1, gw - 0.3), max(0.1, gh - pad - 2.4)
        for i, (x, y, w, h) in zip(its, squarify([i['kb'] for i in its], ix, iy, iw, ih)):
            a = i['activity']
            cls = f't t-{i["tier"]}' + (' blocked' if i.get('blocked') else '')
            tip = f'{i["label"]}\n{human(i["kb"])} · {TIER_NAME[i["tier"]]} · {a["class"].lower()} ({a["source"]})\n{i["why"]}'
            if i.get('blocked'):
                tip += f'\nBLOCKED: {i["blocked"]}'
            label = ''
            if w > 7 and h > 6:
                label = f'<b>{GLYPH[a["class"]]} {e(i["label"][:44])}</b><i>{human(i["kb"])}</i>'
            elif w > 3.5 and h > 4:
                label = f'<i>{human(i["kb"])}</i>'
            out.append(f'<a class="{cls}" href="#{e(i["id"])}" title="{e(tip)}" style="left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%">{label}</a>')
    return ''.join(out)


def matrix(items, th):
    cls = ('ACTIVE', 'RECENT', 'STALE', 'UNKNOWN')
    head = ''.join(f'<th>{GLYPH[c]} {c.title()}</th>' for c in cls)
    rows = ''
    for t in ('SAFE', 'VERIFY', 'KEEP'):
        cells = ''.join(f'<td>{human(sum(i["kb"] or 0 for i in items if i["tier"] == t and i["activity"]["class"] == c))}</td>' for c in cls)
        rows += f'<tr><th class="rowh"><span class="sw t-{t}"></span>{TIER_NAME[t]}</th>{cells}</tr>'
    return (f'<table class="mx"><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>'
            f'<p class="note">Active = written within {th["active_days"]} days, recent = within {th["stale_days"]}, stale = longer. '
            f'Activity can hold a safe item back; it never promotes an item to safe.</p>')


def item_row(i, mx):
    a = i['activity']
    pct = max(1, round(100 * (i['kb'] or 0) / mx))
    gates = ''.join(f'<li class="g-{g["result"]}"><b>{e(g["name"])}</b> {e(g["result"])}: {e(g["detail"])}</li>' for g in i['gates'])
    ev = ''.join(f'<li>{e(x)}</li>' for x in i['evidence'])
    cmds = ''.join(f'<code>{e(c if len(c) < 600 else c[:560] + " … (full list in --json)")}</code>' for c in i['cmds'])
    blocked = f'<p class="blk">⛔ Blocked now: {e(i["blocked"])}</p>' if i.get('blocked') else ''
    return (f'<details class="it" id="{e(i["id"])}"><summary>'
            f'<span class="sz">{human(i["kb"])}{"~" if i.get("estimate") else ""}</span>'
            f'<span class="bar"><span class="fill t-{i["tier"]}" style="width:{pct}%"></span></span>'
            f'<span class="nm">{e(i["label"])}</span>'
            f'<span class="chip t-{i["tier"]}">{TIER_NAME[i["tier"]]}</span>'
            f'<span class="chip act">{GLYPH[a["class"]]} {a["class"].lower()}</span></summary>'
            f'<div class="body"><p><b>Why:</b> {e(i["why"])}</p>'
            f'<p><b>Activity:</b> {e(a["class"].lower())} — {e(a["source"])}</p>{blocked}'
            f'{"<p><b>Evidence</b></p><ul>" + ev + "</ul>" if ev else ""}'
            f'{"<p><b>Gates</b></p><ul class=gates>" + gates + "</ul>" if gates else ""}'
            f'{"<p><b>Would run</b></p>" + cmds if cmds else ""}</div></details>')


def render(data):
    items, th = data['items'], data['thresholds']
    free, total = data['free_kb'], data['total_kb']
    b = {k: sum(i['kb'] or 0 for i in items if i.get('bucket') == k) for k in ('SAFE-STALE', 'SAFE-USED', 'SAFE-BLOCKED')}
    verify = sum(i['kb'] or 0 for i in items if i['tier'] == 'VERIFY')
    tiles = ''.join(f'<div class="tile"><b>{v}</b><span>{k}</span></div>' for k, v in (
        ('free on this Mac', human(free)), ('safe and stale', human(b['SAFE-STALE'])),
        ('safe, recently used', human(b['SAFE-USED'])), ('safe, blocked now', human(b['SAFE-BLOCKED'])), ('needs your review', human(verify))))
    sections = ''
    for g in GROUPS:
        gi = [i for i in items if i['group'] == g]
        if gi:
            mx = max(i['kb'] or 0 for i in gi) or 1
            sections += f'<details open class="grpsec"><summary><h2>{e(g)} <small>{human(sum(i["kb"] or 0 for i in gi))}</small></h2></summary>' + ''.join(item_row(i, mx) for i in gi) + '</details>'
    notes = ''.join(f'<p class="note">{e(n)}</p>' for n in data.get('notes', []))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>System storage report</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d2127;--mut:#59616b;--card:#fff;--bd:#d9dde2;--safe:#bfe3c7;--safe-fg:#10331b;--ver:#f4e0a0;--ver2:#fbf0cf;--ver-fg:#3d2c00;--keep:#cfd6df;--keep-fg:#1f2733;--ring:#1d2127}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14171b;--fg:#e6e9ee;--mut:#9aa3ad;--card:#1c2025;--bd:#323840;--safe:#1f4d2f;--safe-fg:#d6f2dd;--ver:#54421a;--ver2:#483817;--ver-fg:#ffe9ad;--keep:#333c48;--keep-fg:#dfe5ee;--ring:#e6e9ee}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:0;display:inline}}h2 small{{color:var(--mut);font-weight:400;margin-left:8px}}
.sub{{color:var(--mut);margin:0 0 20px}}.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:24px}}
.tile{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}}.tile b{{display:block;font-size:22px}}.tile span{{color:var(--mut);font-size:13px}}
.tm{{position:relative;width:100%;aspect-ratio:16/10;background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;margin:8px 0 12px}}
.grp{{position:absolute;border:1px solid var(--ring);pointer-events:none}}.gl{{position:absolute;left:6px;top:2px;font-size:12px;font-weight:600;background:var(--card);padding:0 4px;border-radius:3px;max-width:96%;overflow:hidden;white-space:nowrap}}
.t{{position:absolute;overflow:hidden;border:1px solid var(--bg);padding:3px 5px;font-size:11px;line-height:1.25;text-decoration:none;display:block}}
.t b{{display:block;font-weight:600;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}}.t i{{font-style:normal;opacity:.85}}
.t-SAFE{{background:var(--safe);color:var(--safe-fg)}}.t-VERIFY{{background:repeating-linear-gradient(45deg,var(--ver) 0 7px,var(--ver2) 7px 14px);color:var(--ver-fg)}}
.t-KEEP{{background:radial-gradient(color-mix(in srgb,var(--ring) 38%,transparent) 1px,transparent 1.6px) 0 0/9px 9px,var(--keep);color:var(--keep-fg)}}
.t.blocked{{outline:2px dashed var(--ring);outline-offset:-3px}}.t:hover,.t:focus{{outline:2px solid var(--ring);outline-offset:-2px;z-index:2}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;color:var(--mut);font-size:13px;margin-bottom:24px}}.legend span{{display:inline-flex;align-items:center;gap:6px}}
.sw{{display:inline-block;width:16px;height:12px;border:1px solid var(--ring);vertical-align:middle;margin-right:6px}}
.mx{{border-collapse:collapse;margin:6px 0}}.mx th,.mx td{{border:1px solid var(--bd);padding:6px 12px;text-align:right}}.mx th{{background:var(--card);font-weight:600}}.mx .rowh{{text-align:left}}
.note{{color:var(--mut);font-size:13px;margin:6px 0}}h3{{margin:28px 0 8px;font-size:18px}}
.grpsec{{margin:18px 0}}.grpsec>summary{{cursor:pointer;padding:6px 0;border-bottom:2px solid var(--bd)}}
.it{{border-bottom:1px solid var(--bd)}}.it>summary{{display:grid;grid-template-columns:78px 120px 1fr auto auto;gap:10px;align-items:center;padding:8px 2px;cursor:pointer;list-style:none}}
.it>summary::-webkit-details-marker{{display:none}}.sz{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}.bar{{height:10px;background:var(--card);border:1px solid var(--bd);border-radius:5px;overflow:hidden}}
.fill{{display:block;height:100%}}.nm{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.chip{{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--ring);white-space:nowrap}}.chip.act{{background:var(--card);color:var(--fg)}}
.body{{padding:4px 8px 14px 90px;color:var(--fg)}}.body p{{margin:6px 0}}.body ul{{margin:4px 0 8px;padding-left:20px}}.body li{{margin:2px 0}}
.g-hold{{font-weight:600}}.blk{{font-weight:600}}code{{display:block;background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;margin:4px 0;font:12px/1.4 ui-monospace,Menlo,monospace;overflow-x:auto;white-space:pre-wrap;word-break:break-all}}
@media (max-width:640px){{.it>summary{{grid-template-columns:64px 1fr;}}.bar,.chip.act{{display:none}}.body{{padding-left:8px}}}}
</style></head><body><main>
<h1>System storage report</h1>
<p class="sub">Read-only scan. {human(free)} free of {human(total)}. Nothing in this report was changed or deleted.</p>
<div class="tiles">{tiles}</div>
<h3>What is taking the space</h3>
<div class="tm" role="img" aria-label="Treemap of reclaimable and protected storage by group and item">{treemap(items)}</div>
<div class="legend"><span><i class="sw t-SAFE"></i>Safe to reclaim</span><span><i class="sw t-VERIFY"></i>Review first</span><span><i class="sw t-KEEP"></i>Leave alone</span>
<span>● active</span><span>◐ recent</span><span>○ stale</span><span>? unknown</span><span>dashed outline = blocked right now</span><span>~ = estimate (upper bound)</span></div>
<h3>How much of it is in use</h3>{matrix(items, th)}
<h3>Every item, with the reason and the checks behind it</h3>
<p class="note">Click a row for why it is in its tier, the evidence, each safety gate and the command that would run.</p>
{sections}{notes}
</main></body></html>'''
