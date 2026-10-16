// Run with Playwright available on NODE_PATH; only browser-local reading state is changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

const base = process.env.IRELAND_NEWS_TEST_URL || 'http://127.0.0.1:8201';
const key = 'irelandNewsReadEditionsV1';
const output = process.env.IRELAND_NEWS_SCREENSHOT_DIR || 'outputs';

async function badge(page, seen) {
  await page.waitForFunction(seen => [...document.querySelectorAll('.read-badge')]
    .every(el => el.textContent === (seen ? '✓ 已读' : '● 未读')), seen);
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1100, height: 800 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    const editions = (await (await page.request.get(base + '/api/editions')).json()).editions;
    assert.ok(editions.length >= 3, 'Use a preview containing at least three editions');
    const [latest, middle, oldest] = editions;
    await badge(page, false);
    assert.equal(await page.locator('.pager').count(), 2);
    assert.equal(await page.locator('.pager-top .left').getAttribute('href'), '/edition/' + middle.slug);
    assert.equal(await page.locator('.pager-top .right').getAttribute('aria-disabled'), 'true');

    fs.mkdirSync(output, { recursive: true });
    for (const width of [1440, 1100, 821, 820, 601, 600, 390, 320]) {
      await page.setViewportSize({ width, height: 800 });
      const problems = await page.evaluate(() => {
        const problems = [];
        if (document.documentElement.scrollWidth > innerWidth) problems.push('page overflow');
        document.querySelectorAll('.meta').forEach((el, index) => {
          const rect = el.getBoundingClientRect();
          if (rect.height > 20 || el.scrollWidth > el.clientWidth + 1) problems.push('metadata overflow ' + index);
          for (const child of el.children) {
            const childRect = child.getBoundingClientRect();
            if (childRect.right > rect.right + 1 || childRect.top < rect.top - 1 || childRect.bottom > rect.bottom + 1)
              problems.push('metadata wrapped ' + index);
          }
        });
        return problems;
      });
      assert.deepEqual(problems, [], 'Layout at ' + width + 'px');
      if (width === 1100 || width === 390) await page.screenshot({ path: `${output}/reading-${width}-top.png` });
    }
    console.log('PASS: single-line metadata and no overflow at eight desktop/mobile widths');

    await page.setViewportSize({ width: 1100, height: 800 });
    await page.locator('.read-toggle').first().click();
    await badge(page, true);
    await page.reload();
    await badge(page, true);
    await page.locator('.read-toggle').first().click();
    await badge(page, false);
    await page.locator('[data-reading-end]').scrollIntoViewIfNeeded();
    // Give IntersectionObserver a frame to prove manual unread is not immediately undone.
    await page.waitForTimeout(150);
    await badge(page, false);
    await page.evaluate(() => scrollTo(0, 0));
    await page.reload();
    await badge(page, false);
    await page.locator('[data-reading-end]').scrollIntoViewIfNeeded();
    await badge(page, true);
    await page.screenshot({ path: `${output}/reading-desktop-bottom.png` });
    console.log('PASS: manual read/unread, persistence, and automatic completion at the end');

    const archive = await context.newPage();
    await archive.goto(base + '/archive');
    const latestRow = archive.locator(`[data-slug="${latest.slug}"]`);
    assert.equal(await latestRow.locator('.unread-label').textContent(), '已读');
    assert.equal(await latestRow.locator('.unread-dot').isVisible(), false);
    await page.locator('.read-toggle').last().click();
    await badge(page, false);
    await archive.waitForFunction(slug => document.querySelector(`[data-slug="${slug}"] .unread-label`).textContent === '未读', latest.slug);
    assert.equal(await latestRow.locator('.unread-dot').isVisible(), true);
    await archive.locator(`[data-slug="${middle.slug}"]`).click();
    await badge(archive, false);
    for (const position of ['top', 'bottom']) {
      assert.equal(await archive.locator(`.pager-${position} .left`).getAttribute('href'), '/edition/' + oldest.slug);
      assert.equal(await archive.locator(`.pager-${position} .right`).getAttribute('href'), '/edition/' + latest.slug);
    }
    await archive.locator('.read-toggle').first().click();
    await badge(archive, true);
    await archive.goBack();
    assert.equal(await archive.locator(`[data-slug="${middle.slug}"] .unread-label`).textContent(), '已读');
    await archive.goto(base + '/edition/' + middle.slug);
    await archive.locator('.pager-bottom .left').click();
    assert.ok(archive.url().endsWith('/edition/' + oldest.slug));
    await archive.locator('.pager-bottom .right').click();
    assert.ok(archive.url().endsWith('/edition/' + middle.slug));
    await archive.setViewportSize({ width: 390, height: 844 });
    await archive.locator('.pager-bottom').scrollIntoViewIfNeeded();
    await archive.screenshot({ path: `${output}/reading-mobile-bottom.png` });
    assert.ok(!(await archive.locator('.hero p').innerText()).includes('&amp;'));
    assert.ok((await archive.locator('.hero p').innerText()).includes('住房 IT 优先'));
    console.log('PASS: archive markers, cross-tab updates, browser back, and bottom navigation');

    await page.goto(base + '/?category=housing');
    assert.equal(await page.locator('[data-reading-end]').count(), 0);
    await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
    await badge(page, false);
    assert.equal(await page.locator('.read-hint').first().textContent(), '可手动标记本期阅读状态');
    console.log('PASS: category-only reading does not mark the whole edition read');

    const legacy = await browser.newContext();
    await legacy.addInitScript(({ key, slug }) => localStorage.setItem(key, JSON.stringify([slug])), { key, slug: latest.slug });
    const legacyPage = await legacy.newPage();
    await legacyPage.goto(base);
    await badge(legacyPage, true);
    await legacy.close();

    const blocked = await browser.newContext();
    await blocked.addInitScript(() => Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked') } }));
    const blockedPage = await blocked.newPage();
    await blockedPage.goto(base);
    assert.equal(await blockedPage.locator('.read-badge').first().textContent(), '阅读状态不可用');
    assert.equal(await blockedPage.locator('.read-toggle').first().isDisabled(), true);
    await blocked.close();
    assert.deepEqual(errors, []);
    console.log('PASS: existing reading records, unavailable storage, and no page errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
