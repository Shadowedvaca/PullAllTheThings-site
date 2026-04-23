"""Compose BIS daily sync email reports."""

import json
from datetime import datetime
from typing import Any


def compose_bis_report(
    run_data: dict[str, Any],
    guild_name: str,
    app_url: str,
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


def _group_by_spec(items: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = f"Spec {item.get('spec_id', '?')} / Source {item.get('source_id', '?')}"
        groups.setdefault(key, []).append(item)
    return groups


def _delta_section(title: str, items: list[dict]) -> str:
    if not items:
        return ""
    groups = _group_by_spec(items)
    rows = ""
    for group_label, group_items in groups.items():
        rows += f'<div class="spec-group">{group_label}</div>'
        for item in group_items:
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
) -> str:
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

    added_html = _delta_section(f"New BIS Items (+{len(delta_added)})", delta_added)
    removed_html = _delta_section(f"Removed BIS Items (\u2212{len(delta_removed)})", delta_removed)

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
