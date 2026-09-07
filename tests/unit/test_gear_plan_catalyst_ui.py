"""Focused UI contract tests for Catalyst-aware Gear Plan recommendations."""

from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).parents[2]
JS_PATH = ROOT / "src/guild_portal/static/js/my_characters.js"
CSS_PATH = ROOT / "src/guild_portal/static/css/my_characters.css"


def _render_catalyst_action(item: dict) -> str:
    source = JS_PATH.read_text(encoding="utf-8")
    start = source.index("function _gpCatalystAction(item)")
    end = source.index("\n}\n\n// Merge BIS items", start) + 2
    function_source = source[start:end]
    script = f"""
const _gpEsc = value => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
{function_source}
process.stdout.write(_gpCatalystAction({json.dumps(item)}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_catalyst_action_renders_result_but_not_as_primary_item() -> None:
    html = _render_catalyst_action({
        "recommendation_type": "catalyst",
        "blizzard_item_id": 268229,
        "catalyst_tier_item_id": 271456,
        "catalyst_tier_item_name": "Tempered Horns of the Jade Warlord",
        "catalyst_tier_direct_available": True,
    })

    assert "Catalyze &rarr;" in html
    assert "item=271456" in html
    assert "Tempered Horns of the Jade Warlord" in html
    assert "May also be obtained directly" in html
    assert "item=268229" not in html


def test_direct_recommendation_has_no_catalyst_action() -> None:
    assert _render_catalyst_action({
        "recommendation_type": "direct",
        "blizzard_item_id": 268229,
        "catalyst_tier_item_id": None,
    }) == ""


def test_row_actions_and_metadata_remain_keyed_to_base_item() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    assert "const bid      = item.blizzard_item_id;" in source
    assert "mcnGpSetDesiredItem('${_gpEsc(dbSlot)}',${bid})" in source
    assert "mcnGpExcludeItem('${_gpEsc(dbSlot)}',${bid}" in source
    assert "_gpRenderSourceSub(item.sources || [])" in source
    assert "item.primary_stats" in source


def test_catalyst_action_has_compact_row_styles() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".mcn-catalyst-action" in css
    assert ".mcn-catalyst-action__label" in css
    assert ".mcn-catalyst-action__note" in css
