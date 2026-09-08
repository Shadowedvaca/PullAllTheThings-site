const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const source = fs.readFileSync('src/guild_portal/static/js/my_characters.js', 'utf8');
const start = source.indexOf('function _gpCatalystAction(item)');
const end = source.indexOf('\n}\n\nfunction _gpUseAction', start) + 2;
const functionSource = source.slice(start, end);
const escapeHtml = value => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const renderCatalystAction = new Function(
  '_gpEsc', `${functionSource}; return _gpCatalystAction;`,
)(escapeHtml);
const goalStart = source.indexOf('function _gpGoalPresentation(item)');
const goalEnd = source.indexOf('\n}\n\nfunction _gpTimeAgo', goalStart) + 2;
const goalFunctionSource = source.slice(goalStart, goalEnd);
const presentGoal = new Function(
  `${goalFunctionSource}; return _gpGoalPresentation;`,
)();
const attrsStart = source.indexOf('function _gpGoalWowheadAttrs(item)');
const attrsEnd = source.indexOf('\n}\n\nfunction _gpTimeAgo', attrsStart) + 2;
const attrsFunctionSource = source.slice(attrsStart, attrsEnd);
const goalWowheadAttrs = new Function(
  '_gpWowheadItemUrl', '_gpEsc', `${attrsFunctionSource}; return _gpGoalWowheadAttrs;`,
)((itemId, item) => `https://www.wowhead.com/item=${itemId}${item.target_ilvl ? `?ilvl=${item.target_ilvl}` : ''}`, escapeHtml);

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
  assert.match(html, /original-item=268229/);
});

test('does not render Catalyst UI for direct recommendations', () => {
  assert.equal(renderCatalystAction({
    recommendation_type: 'direct',
    blizzard_item_id: 268229,
    catalyst_tier_item_id: null,
  }), '');
});

test('presents a selected Catalyst goal as its converted tier result', () => {
  assert.deepEqual(presentGoal({
    recommendation_type: 'catalyst',
    blizzard_item_id: 271457,
    item_name: 'Jeweled Gauntlets of the Jade Warlord',
    catalyst_base_item_id: 251214,
    catalyst_base_item_name: "Bonds of the Hash'ura",
    catalyst_base_icon_url: '/bonds.jpg',
    icon_url: '/tier-hands.jpg',
    target_ilvl: 321,
  }), {
    recommendation_type: 'catalyst',
    blizzard_item_id: 271457,
    item_name: 'Jeweled Gauntlets of the Jade Warlord',
    catalyst_base_item_id: 251214,
    catalyst_base_item_name: "Bonds of the Hash'ura",
    catalyst_base_icon_url: '/bonds.jpg',
    icon_url: '/tier-hands.jpg',
    target_ilvl: 321,
  });
});

test('presents an unselected Catalyst recommendation as its converted tier result', () => {
  const displayed = presentGoal({
    recommendation_type: 'catalyst',
    blizzard_item_id: 251214,
    item_name: "Bonds of the Hash'ura",
    icon_url: '/bonds.jpg',
    catalyst_tier_item_id: 271457,
    catalyst_tier_item_name: 'Jeweled Gauntlets of the Jade Warlord',
    catalyst_tier_icon_url: '/tier-hands.jpg',
  });

  assert.equal(displayed.blizzard_item_id, 271457);
  assert.equal(displayed.item_name, 'Jeweled Gauntlets of the Jade Warlord');
  assert.equal(displayed.icon_url, '/tier-hands.jpg');
  assert.equal(displayed.catalyst_base_item_id, 251214);
});

test('binds the planned tier tooltip to its Catalyst base item', () => {
  const attrs = goalWowheadAttrs({
    recommendation_type: 'catalyst',
    blizzard_item_id: 271457,
    catalyst_base_item_id: 251214,
    target_ilvl: 321,
  });

  assert.match(attrs, /href="https:\/\/www\.wowhead\.com\/item=271457\?ilvl=321"/);
  assert.match(attrs, /data-wowhead="item=271457&amp;original-item=251214&amp;ilvl=321"/);
});
