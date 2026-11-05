import { $ } from './shared.js';
function closeOverlays(except = null) {
 for (const item of document.querySelectorAll('dialog[open], details[open], #read-settings:not([hidden])')) {
  if (item === except) continue;
  if (item.tagName === 'DIALOG') item.close();
  else if (item.tagName === 'DETAILS') item.open = false;
  else {
   item.hidden = true;
   $('aa').setAttribute('aria-expanded', 'false');
  }
 }
 if (except !== $('toc-side') && matchMedia('(max-width:899px)').matches) {
  $('reader').classList.add('noside');
  $('side-toggle').setAttribute('aria-expanded', 'false');
 }
}
function showSettings() {
 const opening = $('read-settings').hidden;
 closeOverlays();
 $('read-settings').hidden = !opening;
 $('aa').setAttribute('aria-expanded', String(opening));
}
document.addEventListener('click', (event) => {
 const dialog = event.target.closest('dialog');
 if (dialog) {
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  return;
 }
 const summary = event.target.closest('summary');
 if (summary) closeOverlays(summary.parentElement);
 else if (!event.target.closest('#read-settings, #aa, #toc-side, #side-toggle, #chapter-directory')) closeOverlays();
}, true);
document.addEventListener('keydown', (event) => {
 if (event.key !== 'Escape') return;
 const open = document.querySelector('dialog[open], details[open], #read-settings:not([hidden])');
 const drawer = !$('reader').hidden && matchMedia('(max-width:899px)').matches && !$('reader').classList.contains('noside');
 if (open || drawer) {
  event.preventDefault();
  event.stopImmediatePropagation();
  closeOverlays();
 }
}, true);
window.addEventListener('hashchange', () => closeOverlays());
export { closeOverlays, showSettings };
