// Run after preparing chapter 3 at scrollTop=500 and appending to the temporary
// chapter-003 final, then adding chapter 6 (see REPORT.md for fixture mutation).
async page => {
 await page.locator('#final').filter({hasText:'实时刷新验收：新增的一句终稿。'}).waitFor();
 await page.locator('#toc-links a').filter({hasText:'第 6 章'}).waitFor({state:'attached'});
 const result = {url:page.url(),scroll:await page.locator('#read-scroll').evaluate(e=>e.scrollTop),
  title:await page.locator('#chapter-title').innerText(),
  progress:await page.locator('#reading-progress').getAttribute('max'),
  chapter_count:await page.locator('#toc-links a').count(),
  final_updated:(await page.locator('#final').innerText()).includes('实时刷新验收：新增的一句终稿。')};
 if (!result.url.endsWith('/3') || result.scroll !== 500 || result.chapter_count !== 6 || result.progress !== '6') {
  throw Error('live refresh did not preserve reader state: '+JSON.stringify(result));
 }
 await page.screenshot({path:'engine/reports/ui-redesign-20261104/shots/18-reader-live-refresh.png'});
 if (await page.locator('#side-toggle').getAttribute('aria-expanded') === 'false') await page.locator('#side-toggle').click();
 await page.locator('#read-scroll').evaluate(e => e.scrollTop = e.scrollHeight);
 await page.screenshot({path:'engine/reports/ui-redesign-20261104/shots/19-reader-live-updated-content.png'});
 return result;
}
