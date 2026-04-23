"""Compose BIS daily sync email reports."""

import json
from datetime import datetime
from typing import Any


def compose_bis_report(
    run_data: dict[str, Any],
    guild_name: str,
    app_url: str,
    spec_map: dict[int, dict] | None = None,
    source_map: dict[int, str] | None = None,
) -> tuple[str, str]:
    """Return (subject, html_body) for a BIS daily sync run report."""
    delta_added = run_data.get("delta_added") or []
    delta_removed = run_data.get("delta_removed") or []

    if isinstance(delta_added, str):
        delta_added = json.loads(delta_added)
    if isinstance(delta_removed, str):
        delta_removed = json.loads(delta_removed)

    n_added = len(delta_added)
    n_removed = len(delta_removed)
    targets_changed = run_data.get("targets_changed", 0)
    targets_failed = run_data.get("targets_failed", 0)
    patch_signal = bool(run_data.get("patch_signal", False))
    has_changes = targets_changed > 0 or n_added > 0 or n_removed > 0

    run_at = run_data.get("run_at")
    if isinstance(run_at, datetime):
        date_str = run_at.strftime("%Y-%m-%d")
        time_str = run_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        date_str = str(run_at)[:10] if run_at else "unknown"
        time_str = date_str

    if has_changes or patch_signal:
        subject = (
            f"[PATT BIS] Daily Update \u2014 {date_str} \u2014 "
            f"+{n_added}/\u2212{n_removed} items"
        )
    else:
        subject = f"[PATT BIS] Daily Update \u2014 {date_str} \u2014 No changes"

    html = _build_html(
        run_data, delta_added, delta_removed,
        guild_name, app_url, time_str, patch_signal, has_changes,
        spec_map=spec_map or {},
        source_map=source_map or {},
    )
    return subject, html


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

_STYLE = """
body { margin: 0; padding: 0; background: #0a0a0b; color: #c0b48a; font-family: Arial, sans-serif; font-size: 14px; }
.wrap { max-width: 640px; margin: 0 auto; padding: 24px 16px; }
.header { border-bottom: 2px solid #d4a84b; padding-bottom: 12px; margin-bottom: 20px; }
.header h1 { margin: 0; font-size: 1.4rem; color: #d4a84b; letter-spacing: 0.05em; }
.header p { margin: 4px 0 0; color: #888; font-size: 0.85rem; }
.stat-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.stat { background: #1a1a1e; border: 1px solid #2a2a2e; border-radius: 6px; padding: 10px 14px; min-width: 90px; text-align: center; }
.stat-num { font-size: 1.5rem; font-weight: bold; color: #d4a84b; display: block; }
.stat-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.04em; }
.section { background: #141416; border: 1px solid #2a2a2e; border-radius: 6px; padding: 14px 16px; margin-bottom: 16px; }
.section h2 { margin: 0 0 10px; font-size: 0.95rem; color: #d4a84b; text-transform: uppercase; letter-spacing: 0.05em; }
.item-row { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #1e1e22; font-size: 0.88rem; }
.item-row:last-child { border-bottom: none; }
.item-slot { color: #888; font-size: 0.8rem; }
.spec-group { font-weight: bold; color: #c0b48a; margin: 8px 0 4px; font-size: 0.85rem; }
.alert { background: #1e1400; border: 1px solid #8a6a20; border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; color: #d4a84b; font-size: 0.9rem; }
.quiet { background: #0e1a0e; border: 1px solid #2a4a2a; border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; color: #4ade80; font-size: 0.9rem; }
.footer { border-top: 1px solid #2a2a2e; padding-top: 12px; margin-top: 20px; text-align: center; font-size: 0.8rem; color: #555; }
.btn { display: inline-block; background: #d4a84b; color: #0a0a0b; padding: 8px 20px; border-radius: 4px; text-decoration: none; font-weight: bold; font-size: 0.9rem; margin: 12px 0; }
"""


_SOURCE_ABBREV = {
    "u.gg Raid":        "ugg<br>Ra",
    "u.gg M+":          "ugg<br>M+",
    "u.gg Overall":     "ugg<br>Ov",
    "Wowhead Raid":     "WH<br>Ra",
    "Wowhead M+":       "WH<br>M+",
    "Wowhead Overall":  "WH<br>Ov",
    "Icy Veins Raid":   "IV<br>Ra",
    "Icy Veins M+":     "IV<br>M+",
    "Icy Veins Overall":"IV<br>Ov",
    "Method Overall":   "Me<br>Ov",
    "Method Raid":      "Me<br>Ra",
    "Method M+":        "Me<br>M+",
    "Archon M+":        "Ar<br>M+",
    "Archon Raid":      "Ar<br>Ra",
}


def _delta_matrix(
    delta_added: list[dict],
    delta_removed: list[dict],
    spec_map: dict[int, dict],
    source_map: dict[int, str],
) -> str:
    """Return an HTML table: rows = class/spec, columns = source, cells = +added/-removed."""
    if not delta_added and not delta_removed:
        return ""

    counts: dict[tuple, list[int]] = {}
    for item in delta_added:
        k = (item.get("spec_id"), item.get("source_id"))
        counts.setdefault(k, [0, 0])[0] += 1
    for item in delta_removed:
        k = (item.get("spec_id"), item.get("source_id"))
        counts.setdefault(k, [0, 0])[1] += 1

    source_ids = sorted({k[1] for k in counts if k[1] is not None})
    spec_ids = sorted(
        {k[0] for k in counts if k[0] is not None},
        key=lambda s: (spec_map.get(s, {}).get("class_name", "z"), spec_map.get(s, {}).get("spec_name", "z")),
    )
    if not source_ids or not spec_ids:
        return ""

    class_groups: dict[str, list] = {}
    for spec_id in spec_ids:
        cls = spec_map.get(spec_id, {}).get("class_name", "Unknown")
        class_groups.setdefault(cls, []).append(spec_id)

    n = len(source_ids)
    TH   = "padding:4px 2px;font-size:0.7rem;color:#888;text-align:center;border:1px solid #2a2a2e;background:#0e0e10;line-height:1.3"
    THL  = "padding:4px 6px;font-size:0.72rem;color:#888;text-align:left;border:1px solid #2a2a2e;background:#0e0e10"
    THTL = "padding:4px 2px;font-size:0.7rem;color:#d4a84b;text-align:center;border:1px solid #2a2a2e;background:#0e0e10;font-weight:bold"
    CLS  = "padding:3px 6px;font-size:0.76rem;font-weight:bold;color:#d4a84b;background:#1a1a1e;border:1px solid #2a2a2e"
    SPE  = "padding:3px 6px;font-size:0.76rem;color:#c0b48a;border:1px solid #1e1e22;white-space:nowrap"
    CEL  = "padding:3px 2px;font-size:0.73rem;text-align:center;border:1px solid #1e1e22"
    TOT  = "padding:3px 2px;font-size:0.73rem;text-align:center;border:1px solid #2a2a2e;background:#1a1a1e;font-weight:bold"
    TOTL = "padding:3px 6px;font-size:0.73rem;color:#d4a84b;border:1px solid #2a2a2e;background:#1a1a1e;font-weight:bold"

    def _cell(added: int, removed: int, style: str) -> str:
        if added == 0 and removed == 0:
            return f'<td style="{style}"><span style="color:#333">—</span></td>'
        parts = []
        if added:
            parts.append(f'<span style="color:#4ade80">+{added}</span>')
        if removed:
            parts.append(f'<span style="color:#f87171">-{removed}</span>')
        return f'<td style="{style}">{"/" .join(parts)}</td>'

    # Pre-compute per-source totals and per-spec totals
    src_totals: dict[int, list[int]] = {sid: [0, 0] for sid in source_ids}
    spec_totals: dict[int, list[int]] = {spid: [0, 0] for spid in spec_ids}
    for (spid, sid), (a, r) in counts.items():
        if sid in src_totals:
            src_totals[sid][0] += a
            src_totals[sid][1] += r
        if spid in spec_totals:
            spec_totals[spid][0] += a
            spec_totals[spid][1] += r
    grand_added   = sum(v[0] for v in src_totals.values())
    grand_removed = sum(v[1] for v in src_totals.values())

    header = f'<th style="{THL}">Spec</th>'
    for sid in source_ids:
        name = source_map.get(sid, f"Src {sid}")
        label = _SOURCE_ABBREV.get(name, name.replace(" ", "<br>", 1))
        header += f'<th style="{TH}">{label}</th>'
    header += f'<th style="{THTL}">Total</th>'

    body = ""
    for cls_name, cls_specs in sorted(class_groups.items()):
        body += f'<tr><td colspan="{n + 2}" style="{CLS}">{cls_name}</td></tr>'
        for spec_id in cls_specs:
            spec_name = spec_map.get(spec_id, {}).get("spec_name", f"Spec {spec_id}")
            row = f'<td style="{SPE}">{spec_name}</td>'
            for sid in source_ids:
                row += _cell(*counts.get((spec_id, sid), [0, 0]), CEL)
            row += _cell(*spec_totals[spec_id], TOT)
            body += f"<tr>{row}</tr>"

    # Totals footer row
    footer_row = f'<td style="{TOTL}">Total</td>'
    for sid in source_ids:
        footer_row += _cell(*src_totals[sid], TOT)
    footer_row += _cell(grand_added, grand_removed, TOT)
    body += f"<tr>{footer_row}</tr>"

    col_w = f"calc((100% - 88px) / {n + 1})"
    colgroup = '<col style="width:88px">' + f'<col style="width:{col_w}">' * (n + 1)

    return (
        '<div class="section"><h2>Changes by Spec &amp; Source</h2>'
        '<div style="overflow-x:auto">'
        f'<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
        f'<colgroup>{colgroup}</colgroup>'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{body}</tbody>'
        '</table></div></div>'
    )


def _group_items(
    items: list[dict],
    spec_map: dict[int, dict],
    source_map: dict[int, str],
) -> dict[str, dict[str, dict[str, list[dict]]]]:
    """Return {source_name: {class_name: {spec_name: [items]}}} sorted at each level."""
    tree: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for item in items:
        sid = item.get("source_id")
        spid = item.get("spec_id")
        source_name = source_map.get(sid, f"Source {sid}") if sid is not None else "Unknown Source"
        spec_info = spec_map.get(spid, {}) if spid is not None else {}
        class_name = spec_info.get("class_name", f"Class ?")
        spec_name = spec_info.get("spec_name", f"Spec {spid}")
        tree.setdefault(source_name, {}).setdefault(class_name, {}).setdefault(spec_name, []).append(item)
    return {
        src: {
            cls: dict(sorted(specs.items()))
            for cls, specs in sorted(classes.items())
        }
        for src, classes in sorted(tree.items())
    }


def _delta_section(
    title: str,
    items: list[dict],
    spec_map: dict[int, dict],
    source_map: dict[int, str],
) -> str:
    if not items:
        return ""
    tree = _group_items(items, spec_map, source_map)
    rows = ""
    for source_name, classes in tree.items():
        rows += f'<div style="font-size:0.8rem;color:#888;text-transform:uppercase;letter-spacing:0.06em;margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid #2a2a2e">{source_name}</div>'
        for class_name, specs in classes.items():
            rows += f'<div style="font-weight:bold;color:#d4a84b;margin:8px 0 2px;font-size:0.85rem">{class_name}</div>'
            for spec_name, spec_items in specs.items():
                rows += f'<div class="spec-group">{spec_name}</div>'
                for item in spec_items:
                    slot = item.get("slot", "")
                    name = item.get("item_name", f"Item {item.get('blizzard_item_id', '?')}")
                    rows += (
                        f'<div class="item-row">'
                        f'<span>{name}</span>'
                        f'<span class="item-slot">{slot}</span>'
                        f'</div>'
                    )
    return f'<div class="section"><h2>{title}</h2>{rows}</div>'


def _build_html(
    run_data: dict[str, Any],
    delta_added: list[dict],
    delta_removed: list[dict],
    guild_name: str,
    app_url: str,
    time_str: str,
    patch_signal: bool,
    has_changes: bool,
    spec_map: dict[int, dict] | None = None,
    source_map: dict[int, str] | None = None,
) -> str:
    spec_map = spec_map or {}
    source_map = source_map or {}
    targets_checked = run_data.get("targets_checked", 0)
    targets_changed = run_data.get("targets_changed", 0)
    targets_failed = run_data.get("targets_failed", 0)
    targets_skipped = run_data.get("targets_skipped", 0)
    bis_before = run_data.get("bis_entries_before", 0)
    bis_after = run_data.get("bis_entries_after", 0)
    duration = run_data.get("duration_seconds")
    triggered_by = run_data.get("triggered_by", "scheduled")
    notes = run_data.get("notes") or ""

    duration_str = f"{duration:.1f}s" if duration is not None else "—"
    admin_url = f"{app_url.rstrip('/')}/admin/gear-plan" if app_url else "/admin/gear-plan"

    stats_html = f"""
    <div class="stat-row">
        <div class="stat"><span class="stat-num">{targets_checked}</span><span class="stat-label">Checked</span></div>
        <div class="stat"><span class="stat-num">{targets_changed}</span><span class="stat-label">Changed</span></div>
        <div class="stat"><span class="stat-num">{targets_failed}</span><span class="stat-label">Failed</span></div>
        <div class="stat"><span class="stat-num">{targets_skipped}</span><span class="stat-label">Skipped</span></div>
        <div class="stat"><span class="stat-num">{bis_after}</span><span class="stat-label">BIS entries</span></div>
    </div>
    """

    patch_html = ""
    if patch_signal:
        patch_html = (
            '<div class="alert">'
            '<strong>Patch signal detected</strong> — new WoW content found. '
            'Guide sources have been reset to daily monitoring.'
            '</div>'
        )

    matrix_html = _delta_matrix(delta_added, delta_removed, spec_map, source_map)
    added_html = _delta_section(f"New BIS Items (+{len(delta_added)})", delta_added, spec_map, source_map)
    removed_html = _delta_section(f"Removed BIS Items (\u2212{len(delta_removed)})", delta_removed, spec_map, source_map)

    quiet_html = ""
    if not has_changes and not patch_signal:
        quiet_html = '<div class="quiet">&#10003; No changes detected \u2014 pipeline healthy</div>'

    notes_html = ""
    if notes:
        notes_html = f'<div class="section"><h2>Notes</h2><pre style="color:#f87171;font-size:0.82rem;margin:0;white-space:pre-wrap">{notes}</pre></div>'

    failures_html = ""
    if targets_failed > 0:
        failures_html = (
            f'<div class="section"><h2>Target Failures ({targets_failed})</h2>'
            f'<p style="color:#f87171;margin:0;font-size:0.88rem">'
            f'{targets_failed} active target(s) failed during this run. '
            f'Check the BIS admin panel for details.'
            f'</p></div>'
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{_STYLE}</style></head>
<body>
<div class="wrap">
  <div class="header">
    <h1>{guild_name} \u2014 BIS Daily Sync</h1>
    <p>{time_str} &middot; triggered by {triggered_by} &middot; {duration_str}</p>
  </div>
  {patch_html}
  {stats_html}
  {quiet_html}
  {matrix_html}
  {added_html}
  {removed_html}
  {failures_html}
  {notes_html}
  <div style="text-align:center">
    <a class="btn" href="{admin_url}">View BIS Admin Panel</a>
  </div>
  <div class="footer">
    {guild_name} &mdash; automated BIS sync report
  </div>
</div>
</body>
</html>"""
