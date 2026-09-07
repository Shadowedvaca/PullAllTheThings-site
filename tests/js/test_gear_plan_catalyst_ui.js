const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const source = fs.readFileSync('src/guild_portal/static/js/my_characters.js', 'utf8');
const start = source.indexOf('function _gpCatalystAction(item)');
const end = source.indexOf('\n}\n\n// Merge BIS items', start) + 2;
const functionSource = source.slice(start, end);
const escapeHtml = value => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const renderCatalystAction = new Function(
  '_gpEsc', `${functionSource}; return _gpCatalystAction;`,
)(escapeHtml);

test('renders explicit Catalyst result as an inline action', () => {
  const html = renderCatalystAction({
    recommendation_type: 'catalyst',
    blizzard_item_id: 268229,
    catalyst_tier_item_id: 271456,
    catalyst_tier_item_name: 'Tempered Horns of the Jade Warlord',
    catalyst_tier_direct_available: true,
  });

  assert.match(html, /Catalyze &rarr;/);
  assert.match(html, /item=271456/);
  assert.match(html, /Tempered Horns of the Jade Warlord/);
  assert.match(html, /May also be obtained directly/);
  assert.doesNotMatch(html, /item=268229/);
});

test('does not render Catalyst UI for direct recommendations', () => {
  assert.equal(renderCatalystAction({
    recommendation_type: 'direct',
    blizzard_item_id: 268229,
    catalyst_tier_item_id: null,
  }), '');
});
