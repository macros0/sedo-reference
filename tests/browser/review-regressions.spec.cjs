const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'site/catalog-data.js'), 'utf8').replace(/^window.SEDO_CATALOG=/, '').replace(/;\s*$/, ''));

test.beforeEach(async ({ context }) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:4173' ? route.continue() : route.abort());
});

test('catalog search uses titles of the selected contour', async ({ page }) => {
  await page.goto('/site/');
  for (const [environment, query, found] of [
    ['prod', 'заработной плате', false], ['test', 'заработной плате', true],
    ['test', 'первых трех', false], ['prod', 'первых трех', true],
    ['', 'заработной плате', true],
  ]) {
    await page.locator('#catalog-env').selectOption(environment);
    await page.locator('#message-query').fill(query);
    await expect(page.locator('.message-card[data-number="32210"]')).toHaveCount(found ? 1 : 0);
  }
});

test('added and removed fields are searchable by their constraints', async ({ page }) => {
  const edition = number => catalog.revisions.find(r => r.number === number && r.environment === 'prod' && r.ready);
  const a = edition('10'), b = edition('1010');
  for (const [left, right] of [[a,b],[b,a]]) {
    const query = new URLSearchParams({ message: '1010', compare: '1', left: left.revision_id, right: right.revision_id });
    await page.goto('/site/#'+query);
    const search = page.getByPlaceholder('Тег, путь, описание или ограничение');
    await expect(search).toBeVisible();
    await search.fill('[0-9a-fA-F]{8}');
    await expect(page.locator('.differences')).toContainText('notice/@messageUuid101');
  }
});

test('a pattern containing a backslash can be pasted into comparison search', async ({ page }) => {
  const edition = number => catalog.revisions.find(r => r.number === number && r.environment === 'test' && r.ready);
  const a = edition('84'), b = edition('8410');
  for (const [left,right] of [[a,b],[b,a]]) {
    await page.goto('/site/#'+new URLSearchParams({ message:'8410', compare:'1', left:left.revision_id, right:right.revision_id }));
    await page.getByPlaceholder('Тег, путь, описание или ограничение').fill('[C|E|P]_\\d{10}_');
    await expect(page.locator('.differences')).toContainText('paymentStatusRequest/batchNo');
  }
});
