const {test, expect} = require('@playwright/test');
const {catalog} = require('./catalog-fixtures.cjs');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

test('embedded XSD retains its publication in cards and journal after a DOCX moves and returns', async ({page}) => {
  const root = path.resolve(__dirname, '../..');
  const venv = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const script = [
    'import contextlib, io, json, tempfile',
    'from pathlib import Path',
    'from tests.test_source_provenance import embedded_alias_catalog',
    'with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):',
    "    catalogs = [embedded_alias_catalog(Path(tmp), key) for key in ('second', 'first')]",
    'print(json.dumps(catalogs))'
  ].join('\n');
  const python = process.env.SEDO_PYTHON || (fs.existsSync(venv) ? venv : 'python');
  const both = JSON.parse(execFileSync(python, ['-c', script], {cwd:root, encoding:'utf8'}));
  let published;
  await page.route('**/catalog-data.js*', route => route.fulfill({
    contentType:'application/javascript', body:'window.SEDO_CATALOG='+JSON.stringify(published)
  }));
  for (const data of both) {
    published = data;
    await page.goto('/site/#message=14&env=prod');
    for (const [index, name] of ['first', 'second'].entries()) {
      const doc = 'https://lk.sfr.gov.ru/specs_sedo/'+name+'.docx';
      const source = page.locator('#versions .edition-source').nth(index);
      await expect(source.locator('.schema-origin a').nth(1)).toHaveAttribute('href', doc);
      await expect(source.locator('.schema-origin')).toContainText('Вложение sample.bin');
    }
    await page.goto('/site/#updates=1');
    for (const [type, name, publication] of [['schema','first','sedo'], ['source_added','second','prab']]) {
      const links = page.locator('.update-card[data-event-type="'+type+'"] .schema-origin a');
      await expect(links.nth(0)).toHaveAttribute('href', 'https://lk.sfr.gov.ru/'+publication+'.html');
      await expect(links.nth(1)).toHaveAttribute('href', 'https://lk.sfr.gov.ru/specs_sedo/'+name+'.docx');
      await expect(links.nth(2)).toHaveAttribute('href', data.revisions[0].schema_file_download);
    }
    await page.goto('about:blank');
  }
});

test('journal searches and displays the title recorded for an added source', async ({page}) => {
  await page.goto('/site/');
  const revision = catalog.revisions.find(row => row.number === '14' && row.environment === 'prod');
  await page.evaluate(id => {
    const row = window.SEDO_CATALOG.revisions.find(item => item.revision_id === id);
    window.SEDO_CATALOG.source_updates = [{event_type:'source_added', event_id:'added-title', revision_id:id,
      observed_at:'2026-11-17T08:00:00Z', source:{...row, title:'Контрольное название обмена',
        document_version:'6.0', document_url:'https://lk.sfr.gov.ru/specs_sedo/v6.docx'}}];
    location.hash = new URLSearchParams({updates:'1', message:'14', env:'prod', q:'Контрольное название обмена'}).toString();
  }, revision.revision_id);
  const card = page.locator('.update-card[data-event="added-title"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Контрольное название обмена');
  await expect(card.getByRole('link', {name:'Открыть существующую редакцию'})).toHaveAttribute('href', new RegExp(revision.revision_id));
});

test('122 preserves the complete document change history with its exact source', async ({page}) => {
  await page.goto('/site/#message=122&env=test');
  const panel = page.locator('.version-panel').filter({has: page.getByRole('heading', {name: 'Тестовый контур'})});
  const source = panel.locator('.edition-source').filter({hasText: 'версия 2.49'});
  const history = source.locator('.document-change-history');
  const summary = history.locator('summary');
  await expect(summary).toHaveText('История изменений документа (49)');
  await expect(history.locator('.document-version-entry').first()).toBeHidden();
  await summary.click();
  await expect(history.locator('.document-version-entry')).toHaveCount(49);
  await expect(history).toContainText('Версия документа · 2.1');
  await expect(history).toContainText('Дата изменения · 02.12.2021');
  await expect(history).toContainText('Версия документа · 2.49');
  await expect(history).toContainText('Дата изменения · 18.12.2025');
  const revision = catalog.revisions.find(row => row.number === '122' && row.environment === 'test' && row.document_version === '2.49');
  await expect(history.getByRole('link', {name: 'Источник истории изменений — DOCX'})).toHaveAttribute('href', revision.document_url);
});
