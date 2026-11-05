// Invoke with playwright-cli -s=bookweb-ui run-code --filename=<this file>.
// Requires browser_fixture.py on 18789. All data is synthetic and temporary.
async (page) => {
 const origin = 'http://127.0.0.1:18789';
 const out = 'engine/reports/ui-redesign-20261104/shots/';
 const results = { viewport: 1440, checks: {}, shots: [] };
 const check = (name, value) => {
  results.checks[name] = value;
  if (!value) throw new Error(name + ' failed');
 };
 const shot = async name => {
  await page.screenshot({path: out + name + '.png', fullPage: true});
  results.shots.push(name + '.png');
 };
 const visit = async hash => {
  await page.goto(origin + '/#/' + hash);
  const view = hash.startsWith('read/') ? '#reader' : hash.startsWith('book/') ? '#detail' : hash === 'new' ? '#create' : '#library';
  await page.locator(view).waitFor({state:'visible'});
  if (view === '#reader') await page.locator('#chapter-title').filter({hasText:'渡口的'}).waitFor();
  if (view === '#detail') await page.locator('#book-hero h1').filter({hasText:'渡口归途'}).waitFor();
 };
 const read = 'read/webui%2Fsample/1';
 await page.setViewportSize({width:1440, height:1000});
 await page.goto(origin);
 await page.evaluate(() => {
  localStorage.removeItem('bookweb-reader');
  localStorage.removeItem('bookweb-theme');
 });
 await visit('library');
 await page.locator('.book-card').first().waitFor();
 await shot('01-library');
 check('budget_visible', (await page.locator('#spending-value').innerText()).includes('$20.00'));
 await page.route(origin + '/api/books', route => route.fulfill({json:{books:[],spending:0,limit:20}}));
 await page.reload();
 await page.locator('.new-tile').waitFor();
 check('empty_library_has_new_tile', await page.locator('.book-card').count() === 0);
 await page.unroute(origin + '/api/books');
 await visit('library');
 await visit('new');
 await page.locator('#genre option').first().waitFor({state:'attached'});
 await shot('02-wizard-direction');
 await page.locator('#genre').selectOption('cozy-mystery');
 await page.locator('#generate-topics').click();
 await page.locator('.candidate').nth(11).waitFor();
 check('twelve_topic_cards', await page.locator('.candidate').count() === 12);
 const initialId = await page.locator('#topic-id').inputValue();
 await page.locator('#refresh-topics').click();
 await page.waitForFunction(id => document.querySelector('#topic-id').value !== id, initialId);
 check('new_batch_id', await page.locator('#topic-id').inputValue() !== initialId);
 await page.locator('[data-direction=both]').first().click();
 await page.locator('.candidate').nth(11).waitFor();
 await page.locator('.candidate-editor summary').first().click();
 await page.locator('#edit-0-title').fill('渡口的新灯');
 await page.locator('input[name=candidate]').first().check();
 await shot('03-wizard-topics');
 await page.locator('#to-settings').click();
 await page.locator('#budget').fill('0.24');
 await page.locator('#deai-budget').fill('0.71');
 await page.locator('#polish').check();
 check('independent_budget_estimate', (await page.locator('#estimate').innerText()).includes('$0.71'));
 await shot('04-wizard-settings');
 // Actual create API writes only into the temporary root; scheduler is disabled.
 await page.locator('#create-book').click();
 await page.waitForURL(/#\/book\/webui%2F[0-9a-f-]+$/);
 await page.locator('#book-hero h1').filter({hasText:'渡口的新灯'}).waitFor();
 check('created_from_edited_card', await page.locator('#book-hero h1').innerText() === '渡口的新灯');
 const created = await page.evaluate(async () => {
  const id = decodeURIComponent(location.hash.split('/').pop());
  return await (await fetch('/api/book?id=' + encodeURIComponent(id))).json();
 });
 check('separate_budgets_reach_backend', created.meta.budget === .24 && created.meta.deai_budget === .71);
 await visit('book/webui%2Fsample');
 await shot('05-book-detail');
 await page.locator('#book-tabs button').filter({hasText:'故事圣经'}).click();
 await page.locator('#book-content .chapter-grid h3').filter({hasText:'林舟'}).waitFor();
 check('character_cards', await page.locator('#book-content .chapter-grid h3').count() === 2);
 await shot('06-character-cards');
 await page.reload();
 await page.locator('#book-content .chapter-grid h3').filter({hasText:'林舟'}).waitFor();
 check('detail_tab_survives_reload', true);
 await visit(read);
 await page.locator('#aa').click();
 await page.locator('[data-pref=width][data-value=medium]').click();
 await page.locator('#settings-close').click();
 results.medium = await page.locator('#final').evaluate(el => el.getBoundingClientRect().width);
 await shot('07-reader-medium');
 await page.locator('#aa').click();
 await page.locator('[data-pref=width][data-value=full]').click();
 await page.locator('#settings-close').click();
 results.full = await page.locator('#final').evaluate(el => el.getBoundingClientRect().width);
 check('full_at_least_1200', results.full >= 1200);
 await shot('08-reader-full');
 await page.locator('#read-modes [data-mode=compare]').click();
 results.compare = await page.locator('#comparison article').evaluateAll(els => els.map(el => el.getBoundingClientRect().width));
 check('both_columns_at_least_560', results.compare.every(w => w >= 560));
 const highlights = await page.locator('#final mark').allTextContents();
 check('sentence_level_highlight', highlights.length === 1 && highlights[0] === '林舟推开吱呀作响的木门。');
 check('unchanged_sentences_unmarked', await page.locator('#final p').first().innerText() === '暮色压过渡口。林舟推开吱呀作响的木门。江晚没有回头。');
 results.highlights = highlights;
 await shot('09-reader-compare');
 await page.locator('#aa').click();
 await page.locator('[data-pref=bg][data-value=night]').click();
 await page.locator('[data-pref=size][data-value="22"]').click();
 await page.locator('[data-pref=line][data-value="2.4"]').click();
 await page.locator('#settings-close').click();
 await page.locator('#read-modes [data-mode=final]').click();
 await shot('10-reader-night');
 await page.reload();
 await page.locator('#chapter-title').filter({hasText:'渡口的'}).waitFor();
 check('preferences_survive_reload', await page.locator('#reader').getAttribute('data-background') === 'night');
 results.full_after_reload = await page.locator('#final').evaluate(el => el.getBoundingClientRect().width);
 check('full_survives_reload', results.full_after_reload >= 1200);
 await page.keyboard.press('ArrowRight');
 await page.waitForURL(/\/2$/);
 await page.locator('#chapter-title').filter({hasText:'第 2 章'}).waitFor();
 await page.goBack();
 await page.locator('#chapter-title').filter({hasText:'第 1 章'}).waitFor();
 await page.goForward();
 await page.locator('#chapter-title').filter({hasText:'第 2 章'}).waitFor();
 await page.reload();
 await page.locator('#chapter-title').filter({hasText:'第 2 章'}).waitFor();
 check('back_forward_reload_reader', true);
 await page.keyboard.press('ArrowLeft');
 await page.locator('#chapter-title').filter({hasText:'第 1 章'}).waitFor();
 // Hold a navigation fetch and trigger the same refresh event used by actions.
 // A polling response must not cancel the pending chapter navigation.
 let releaseNavigation;
 const gate = new Promise(resolve => { releaseNavigation = resolve; });
 let held = false;
 const bookPattern = '**/api/book?id=webui%2Fsample';
 await page.route(bookPattern, async route => {
  if (!held) { held = true; await gate; }
  await route.continue();
 });
 const pendingNavigation = page.waitForRequest(request => request.url().endsWith('/api/book?id=webui%2Fsample'));
 await page.evaluate(() => { location.hash = '#/read/webui%2Fsample/2'; });
 await pendingNavigation;
 await page.evaluate(() => window.dispatchEvent(new Event('book-changed')));
 releaseNavigation();
 await page.locator('#chapter-title').filter({hasText:'第 2 章'}).waitFor();
 await page.unroute(bookPattern);
 check('refresh_does_not_cancel_navigation', true);
 await page.keyboard.press('t');
 check('T_toggles_toc', await page.locator('#side-toggle').getAttribute('aria-expanded') === 'true');
 await page.keyboard.press('Escape');
 await page.locator('#book-hero h1').waitFor();
 check('Esc_returns_detail', page.url().includes('#/book/'));
 for (const hash of ['library', 'new', 'book/webui%2Fsample', read]) {
  await visit(hash);
  await page.reload();
  check('direct_reload_' + hash, page.url().endsWith('#/' + hash));
 }
 // Mobile: inspect all four views, including the open drawer and stacked compare.
 await page.setViewportSize({width:375, height:812});
 results.mobile = {};
 for (const hash of ['library', 'new', 'book/webui%2Fsample', read]) {
  await visit(hash);
  const width = await page.evaluate(() => ({viewport:innerWidth, doc:document.documentElement.scrollWidth,
   reader:document.querySelector('#reader').scrollWidth}));
  results.mobile[hash] = width;
  check('mobile_no_horizontal_' + hash, width.doc === 375 && (!hash.startsWith('read') || width.reader === 375));
 }
 await shot('11-mobile-reader');
 await page.locator('#side-toggle').click();
 results.drawer = await page.locator('#toc-side').evaluate(el => ({position:getComputedStyle(el).position,
  width:el.getBoundingClientRect().width, visible:getComputedStyle(el).visibility}));
 check('mobile_drawer', results.drawer.position === 'fixed' && results.drawer.width < 375 && results.drawer.visible === 'visible');
 await shot('12-mobile-drawer');
 await page.locator('#drawer-mask').click();
 await page.locator('#read-modes [data-mode=compare]').click();
 const mobileColumns = await page.locator('#comparison article').evaluateAll(els => els.map(el => ({x:el.offsetLeft,y:el.offsetTop})));
 check('mobile_compare_stacked', mobileColumns[0].x === mobileColumns[1].x && mobileColumns[1].y > mobileColumns[0].y);
 await shot('13-mobile-compare');
 console.log('BROWSER_RESULTS=' + JSON.stringify(results));
 return results;
}
