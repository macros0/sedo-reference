const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'site/catalog-data.js'), 'utf8')
  .replace(/^window\.SEDO_CATALOG=/, '').replace(/;\s*$/, ''));

function edition(number, environment) {
  const candidates = catalog.revisions.filter(r => r.number === number && r.environment === environment
    && r.ready && (!r.display_revision_id || r.display_revision_id === r.revision_id));
  const timestamp = r => {
    if (!r.label_date) return 0;
    const [day, month, year] = r.label_date.split('.').map(Number);
    return Date.UTC(year < 100 ? 2000 + year : year, month - 1, day);
  };
  const revision = candidates.sort((a, b) => timestamp(b) - timestamp(a)
    || a.revision_id.localeCompare(b.revision_id))[0];
  if (!revision) throw Error(`Missing browser fixture edition ${number}/${environment}`);
  return revision;
}

function archivedEdition(number, version) {
  // Synthetic XML targets an immutable historical contract. New SFR schemas
  // can add mandatory fields without invalidating this regression fixture.
  const revision = catalog.revisions.find(r => r.number === number && r.environment === 'prod'
    && r.document_version === version && r.ready);
  if (!revision) throw Error(`Missing archived XML fixture ${number}/${version}`);
  return revision;
}

const currentResult = edition('10510', 'prod');
const result = archivedEdition('10510', '3.0');
const previous = edition('105', 'prod');
const response = archivedEdition('10110', '3.0');
const validXml = `<${result.root} xmlns="${result.namespace}" specVersion="${result.document_version}" responseOn="00000000-0000-0000-0000-000000000000" messageDateTime="2026-11-13T12:00:00Z">
  <processNum>TEST</processNum>
  <status>RECEIVED</status>
</${result.root}>`;

async function openValidator(page, revision) {
  const params = new URLSearchParams({ message: revision.number, env: revision.environment,
    rev: revision.revision_id, validate: '1' });
  await page.goto(`#${params}`);
  await expect(page.getByRole('region', { name: 'Локальная проверка XML', exact: true })
    .getByRole('status')).toContainText('Готово.');
  await expect(page.frameLocator('#frame').getByRole('heading', { name: revision.root, exact: true })).toBeVisible();
  const validator = page.frameLocator('iframe[title="Приватный валидатор XML: файл и результаты остаются в браузере"]');
  await validator.getByText('Или вставьте XML текстом', { exact: true }).click();
  return validator;
}

async function validate(validator, xml) {
  await validator.getByRole('textbox', { name: 'XML для локальной проверки', exact: true }).fill(xml);
  await validator.getByRole('button', { name: 'Проверить XML', exact: true }).click();
  await expect(validator.getByRole('status')).toContainText('XML ');
}

test.beforeEach(async ({ context }) => {
  // Serve the built site under a subpath, with no dependency on live external services.
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === 'http://127.0.0.1:4173') return route.continue();
    return route.abort();
  });
});

test('default comparison chooses different production and test editions', async ({ page }) => {
  await page.goto('#message=10510');
  await page.getByRole('link', { name: 'Сравнить редакции', exact: true }).click();
  const before = page.getByRole('combobox', { name: 'Было', exact: true });
  const after = page.getByRole('combobox', { name: 'Стало', exact: true });
  await expect(before).toHaveValue(currentResult.revision_id);
  await expect(after).toHaveValue(edition('10510', 'test').revision_id);
  expect(await before.inputValue()).not.toBe(await after.inputValue());
  await expect(page.locator('.compare-counts')).toBeVisible();
});

test('legacy comparison swaps counts and opens a field in its own edition', async ({ page }) => {
  await page.goto('#message=10510');
  await page.getByRole('link', { name: 'Сравнить 10510 → 105', exact: true }).click();
  await page.getByRole('button', { name: 'Поменять местами', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Сравнение сообщений 105 → 10510', exact: true })).toBeVisible();
  const added = Number((await page.locator('.compare-counts .added').innerText()).split('·')[1]);
  const removed = Number((await page.locator('.compare-counts .removed').innerText()).split('·')[1]);
  expect(added).toBeGreaterThan(0);
  expect(removed).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Поменять местами', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Сравнение сообщений 10510 → 105', exact: true })).toBeVisible();
  await expect(page.locator('.compare-counts .added')).toHaveText(`Добавлено · ${removed}`);
  await expect(page.locator('.compare-counts .removed')).toHaveText(`Удалено · ${added}`);
  const fieldLink = page.getByRole('link', { name: 'Поле в «Стало»', exact: true }).first();
  const route = new URLSearchParams((await fieldLink.getAttribute('href')).slice(1));
  expect(route.get('rev')).toBe(previous.revision_id);
  await fieldLink.click();
  await expect(page.frameLocator('#frame').getByRole('region', { name: 'Карточка поля' })
    .locator('code').first()).toHaveText(route.get('field'));
});

test('error links select the XML tag and show its dictionary restrictions', async ({ page }) => {
  const validator = await openValidator(page, result);
  await validate(validator, validXml.replace('RECEIVED', 'INVALID'));
  const cards = validator.locator('#errors > li');
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText('Допустимые значения: «RECEIVED», «ERROR», «PROCESSED».');
  await cards.getByText('Подробнее', { exact: true }).click();
  await expect(cards.locator('pre')).toContainText("[facet 'enumeration']");
  await cards.getByRole('button', { name: 'Показать тег в XML', exact: true }).click();
  await expect(validator.getByRole('spinbutton', { name: 'Номер строки XML' })).toHaveValue('3');
  await expect(validator.locator('mark')).toHaveText(['status', 'status']);
  await cards.getByRole('button', { name: 'Посмотреть ограничения', exact: true }).click();
  const detail = page.frameLocator('#frame').getByRole('region', { name: 'Карточка поля' });
  await expect(detail.getByRole('heading', { name: 'status', exact: true })).toBeVisible();
  await expect(detail).toContainText('RECEIVED');
  await expect(detail.locator('code').first()).toHaveText(`${result.root}/status`);
});

test('local editing keeps line numbers and validates the corrected XML without requests', async ({ page, context }) => {
  const validator = await openValidator(page, result);
  const requests = [];
  const record = request => requests.push({ method: request.method(), url: request.url() });
  context.on('request', record);
  await validate(validator, validXml.replace('RECEIVED', 'INVALID'));
  await validator.getByRole('button', { name: 'Редактировать XML', exact: true }).click();
  const editor = validator.getByRole('textbox', { name: 'XML для редактирования', exact: true });
  expect(await editor.evaluate(e => e.value.slice(e.selectionStart, e.selectionEnd))).toBe('status');
  await editor.fill(validXml);
  await expect(validator.locator('.editor-gutter-line')).toHaveText(['1', '2', '3', '4']);
  await validator.getByRole('button', { name: 'Проверить исправленный XML', exact: true }).click();
  await expect(validator.getByRole('status')).toHaveText('XML соответствует XSD выбранного сообщения и редакции.');
  await expect(editor).toBeVisible();
  await expect(editor).toHaveValue(validXml);
  await expect(validator.locator('#errors > li')).toHaveCount(0);
  context.off('request', record);
  expect(requests).toEqual([]);
});

test('long-line navigation moves a collapsed caret and typing preserves XML', async ({ page }) => {
  const validator = await openValidator(page, result);
  const xml = validXml.replace(/\n/g, '').replace(`</${result.root}>`,
    `<responseInfo>${'a'.repeat(5500)}</responseInfo></${result.root}>`);
  await validate(validator, xml);
  await validator.getByRole('button', { name: 'Редактировать XML', exact: true }).click();
  const editor = validator.getByRole('textbox', { name: 'XML для редактирования', exact: true });
  await validator.getByRole('spinbutton', { name: 'Номер строки XML' }).fill('1');
  await validator.getByRole('button', { name: 'К строке', exact: true }).click();
  for (let step = 1; step <= 3; step++) {
    await validator.getByRole('button', { name: 'Далее в строке', exact: true }).click();
    await expect.poll(() => editor.evaluate(e => [e.selectionStart, e.selectionEnd]))
      .toEqual([step * 1024, step * 1024]);
  }
  await editor.press('x');
  await expect(editor).toHaveValue(xml.slice(0, 3072) + 'x' + xml.slice(3072));
  await validator.getByRole('button', { name: 'Проверить исправленный XML', exact: true }).click();
  await expect(validator.getByRole('status')).toHaveText('XML соответствует XSD выбранного сообщения и редакции.');
});

test('a syntax diagnostic retains its source and caret in one card', async ({ page }) => {
  const validator = await openValidator(page, result);
  await validate(validator, validXml.replace('</status>', ''));
  const cards = validator.locator('#errors > li');
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText('Нарушений: 1');
  await cards.getByText('Подробнее', { exact: true }).click();
  await expect(cards.locator('pre')).toContainText('Opening and ending tag mismatch');
  await expect(cards.locator('pre')).toContainText(`</${result.root}>`);
  await expect(cards.locator('pre')).toContainText('^');
});

test('two ELN restrictions share a card, path and navigation controls', async ({ page }) => {
  const fields = JSON.parse(fs.readFileSync(path.join(root, 'data/fields', `${response.revision_id}.json`), 'utf8')).fields;
  const eln = fields.find(f => f.path === `${response.root}/benefit1/elnInfo/lnNumber`);
  expect(eln).toBeTruthy();
  const validator = await openValidator(page, response);
  const xml = `<${response.root} xmlns="${response.namespace}" xmlns:p="${eln.ns}" specVersion="${response.document_version}" responseOn="00000000-0000-0000-0000-000000000000">
<processNum>1</processNum>
<benefit1>
<elnInfo>
<p:lnNumber>0000000000000000</p:lnNumber>
</elnInfo>
</benefit1>
</${response.root}>`;
  await validate(validator, xml);
  const card = validator.locator('#errors > li').filter({ hasText: `/${eln.path}` });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('Строка 5 · Нарушений: 2');
  await expect(card).toContainText('Точная длина по XSD — 12; фактическая — 16.');
  await expect(card).toContainText('Ожидается 12 цифр, указано 16.');
  await expect(card.getByRole('button', { name: 'Показать тег в XML', exact: true })).toHaveCount(1);
  await expect(card.getByRole('button', { name: 'Посмотреть ограничения', exact: true })).toHaveCount(1);
  await card.getByRole('button', { name: 'Строка 5 · Нарушений: 2', exact: true }).click();
  await expect(validator.getByRole('spinbutton', { name: 'Номер строки XML' })).toHaveValue('5');
  await expect(validator.locator('mark')).toHaveText(['p:lnNumber', 'p:lnNumber']);
  await card.getByRole('button', { name: 'Посмотреть ограничения', exact: true }).click();
  const detail = page.frameLocator('#frame').getByRole('region', { name: 'Карточка поля' });
  await expect(detail.getByRole('heading', { name: 'lnNumber', exact: true })).toBeVisible();
  await expect(detail).toContainText('12');
  await expect(detail).toContainText('\\d{12}');
});
