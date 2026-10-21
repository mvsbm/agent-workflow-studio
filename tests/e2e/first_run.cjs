// A newcomer's first look: no Templates dialog, the agent open on the empty canvas with example
// requests that fill the box (not send), the connection in one line, and a file attached in the chat
// lands in ComfyUI's input folder, named for the agent in the next message.
// Verifies: R40
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.sidebarTab?.activeSidebarTabId === 'pi-chat', null, {timeout: 60000});
    assert.equal(await page.locator('[role=dialog]', {hasText: 'Templates'}).count(), 0, 'no Templates dialog on a first visit');
    await page.getByRole('heading', {name: 'Build a workflow by describing it'}).waitFor({timeout: 15000});
    assert.match(await page.locator('.aw-pi-connection > summary').innerText(), /^Not connected · /);
    const message = page.getByRole('textbox', {name: 'Message the agent'});
    await page.getByRole('button', {name: 'Total my sales per region from a CSV file'}).click();
    assert.equal(await message.inputValue(), 'Total my sales per region from a CSV file');
    assert.equal(await page.locator('.aw-pi-user').count(), 0, 'a starter fills the box; it does not send');
    await page.screenshot({path: path.join(artifacts, 'first-run.png')});

    // A file attached in the chat goes into ComfyUI's input folder (its own upload).
    await page.locator('.aw-pi-chat input[type=file]').setInputFiles({name: 'orders.csv', mimeType: 'text/csv', buffer: Buffer.from('order_id,region,amount\n1,North,10\n')});
    const chip = page.locator('.aw-pi-chip');
    await chip.waitFor({timeout: 15000});
    const attached = await chip.innerText();
    assert.match(attached, /^upload_\d{8}_\d{4}\/orders\.csv$/);  // an unsaved workflow: a dated folder
    const [folder, filename] = attached.split('/');
    const stored = await page.evaluate(async ({folder, filename}) => (await fetch(`/view?${new URLSearchParams({filename, subfolder: folder, type: 'input'})}`)).text(), {folder, filename});
    assert.equal(stored, 'order_id,region,amount\n1,North,10\n');
    await page.screenshot({path: path.join(artifacts, 'first-run-attached.png')});
    assert.deepEqual(errors, []);
    console.log('PASS first_run');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'first-run-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
