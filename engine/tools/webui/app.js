import { $, state, api, persist, stored, notify } from './shared.js';
import { loadConfig } from './wizard.js';
import { refreshBooks } from './library.js';
import { renderDetail } from './book.js';
import { renderChapter } from './reader.js';
let routeEpoch = 0, renderedHash = '';
async function route(refresh = false) {
 if (refresh && (!state.readerId || renderedHash !== location.hash)) return;
 const hash = location.hash;
 const epoch = ++routeEpoch;
 const match = location.hash.match(/^#\/(book|read)\/(.+?)(?:\?(.*))?$/);
 const reading = match?.[1] === 'read';
 const view = match ? (reading ? 'reader' : 'detail') : location.hash.startsWith('#/new') ? 'create' : 'library';
 for (const id of ['library', 'create', 'detail', 'reader']) $(id).hidden = id !== view;
 $('top').hidden = reading;
 document.body.classList.toggle('reading', reading);
 if (!match) {
  state.readerId = null;
  return;
 }
 try {
  const tail = match[2];
  const cut = reading ? tail.lastIndexOf('/') : -1;
  const id = decodeURIComponent(reading ? tail.slice(0, cut) : tail);
  state.readerId = id;
  const book = await api('/api/book?id=' + encodeURIComponent(id));
  if (epoch !== routeEpoch || hash !== location.hash) return;
  if (refresh && JSON.stringify(book) === JSON.stringify(state.book)) return;
  state.book = book;
  renderedHash = hash;
  if (reading) {
   state.chapter = Number(tail.slice(cut + 1));
   if (!refresh && matchMedia('(max-width:899px)').matches) $('reader').classList.add('noside');
   renderChapter();
   if (!refresh) $('read-scroll').scrollTop = 0;
  } else {
   renderDetail(new URLSearchParams(match[3]).get('tab') || '目录');
  }
 } catch (error) {
  notify(error.message);
 }
}
window.addEventListener('hashchange', () => route());
window.addEventListener('books-changed', refreshBooks);
window.addEventListener('book-changed', () => route(true));
if (!location.hash) history.replaceState(null, '', '#/library');
loadConfig();
refreshBooks();
route();
let refreshing = false;
setInterval(async () => {
 if (refreshing) return;
 refreshing = true;
 try {
  await refreshBooks();
  await route(true);
 } finally {
  refreshing = false;
 }
}, 3000);
$('theme').onclick = () => {
 document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? '' : 'dark';
 persist('bookweb-theme', document.documentElement.dataset.theme);
};
document.documentElement.dataset.theme = stored('bookweb-theme', '');
