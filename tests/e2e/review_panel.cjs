// The Review tab: a tool's review.json shows as pairs and customers; decisions are versioned.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const output = process.env.OUTPUT;

const REVIEW = {
  title: 'Nayose check',
  pairs: [
    {id: 'p1', score: 0.55, suggestion: 'same', reason: 'Same reading and birth date; surname differs (marriage?).',
     a: {source: 'policies/holder', name: '鈴木 恵子', name_kana: 'スズキ ケイコ', birth: 'S45.03.12'},
     b: {source: 'claims/claimant', name: '佐藤 恵子', name_kana: 'サトウ ケイコ', birth: '1970/03/12'}},
    {id: 'p2', score: 0.4, a: {name: '山田 太郎'}, b: {name: '山田 一郎'}},
  ],
  groups: [{id: 'C0001', label: '鈴木 恵子', records: [{id: 'policies/0000001/holder', name: '鈴木 恵子'}, {id: 'payments/0000044/account_holder', name: 'ｽｽﾞｷ ｹｲｺ'}]}],
};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    const run = path.join(output, 'agent_tools', id, 'match-20261014-120000');
    fs.mkdirSync(run, {recursive: true});
    fs.writeFileSync(path.join(run, 'review.json'), JSON.stringify(REVIEW));
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-review'); });
    const status = page.locator('.aw-review-status');
    await status.filter({hasText: 'Pairs: 0 of 2 decided · Customers: 0 of 1 checked'}).waitFor({timeout: 30000});

    await page.getByRole('group', {name: 'Is pair p1 the same person?'}).getByRole('button', {name: 'Same'}).click();
    await status.filter({hasText: /Saved · version [0-9a-f]{8}/}).waitFor({timeout: 30000});
    // Undecided only: the decided pair leaves the list.
    assert.equal(await page.getByRole('group', {name: 'Is pair p1 the same person?'}).count(), 0);

    await page.getByRole('button', {name: /Matched customers/}).click();
    await page.getByText('C0001 · 2 records · 鈴木 恵子').click();
    await page.getByRole('checkbox', {name: 'Record 2 is not this person'}).check();
    await page.getByRole('group', {name: 'Is C0001 one person?'}).getByRole('button', {name: 'Has someone else'}).click();
    await status.filter({hasText: 'Customers: 1 of 1 checked · Saved'}).waitFor({timeout: 30000});

    const saved = await page.evaluate(async id => {
      const prefix = `/agent-workflow-studio/projects/${encodeURIComponent(id)}`;
      const review = await (await fetch(`${prefix}/review`)).json();
      const log = await (await fetch(`${prefix}/log`)).json();
      return {labels: review.labels, top: log.commits[0]};
    }, id);
    assert.deepEqual(saved.labels, {p1: 'same', 'C0001/payments/0000044/account_holder': 'not_this_person', C0001: 'wrong'});
    assert.equal(saved.top.author, 'user');
    assert.match(saved.top.message, /^Review · 3 decisions/);
    await page.screenshot({path: path.join(artifacts, 'review-panel.png')});
    assert.deepEqual(errors, []);
    console.log('PASS review_panel');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'review-panel-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
