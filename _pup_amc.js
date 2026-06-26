// Headless smoke test for the Digital AMC dashboard: load it, click a desk tile (modal),
// click a kanban card, hover for same-stock highlight — assert zero console/page errors.
const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const file = 'file:///' + path.resolve(__dirname, 'output/_amc/site/index.html').replace(/\\/g, '/');
  const errors = [];
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto(file, { waitUntil: 'networkidle0' });

  const counts = await page.evaluate(() => ({
    fmTiles: document.querySelectorAll('.tile.fm').length,
    anTiles: document.querySelectorAll('.tile.an').length,
    kc: document.querySelectorAll('.kc').length,
    divs: document.querySelectorAll('.divh').length,
  }));

  // open an FM desk modal
  await page.click('.tile.fm');
  const modalOpen = await page.evaluate(() => document.getElementById('overlay').classList.contains('on')
    && document.getElementById('modalbody').innerHTML.length > 50);
  await page.keyboard.press('Escape');
  const modalClosed = await page.evaluate(() => !document.getElementById('overlay').classList.contains('on'));

  // hover a kanban card → same-stock highlight should add .hot somewhere
  const hotOk = await page.evaluate(() => {
    const c = document.querySelector('.kc[data-stock]');
    if (!c) return true;
    c.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    const n = document.querySelectorAll('.kc.hot').length;
    c.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));
    return n >= 1;
  });

  // flow search dims non-matching cards
  const searchOk = await page.evaluate(() => {
    const inp = document.getElementById('flowsearch');
    const sym = (document.querySelector('.kc[data-stock]') || {}).getAttribute
      ? document.querySelector('.kc[data-stock]').getAttribute('data-stock') : '';
    inp.value = sym; inp.dispatchEvent(new Event('input'));
    const dim = document.querySelectorAll('.kc.dim').length;
    return { dim, sym };
  });

  await browser.close();
  console.log('counts:', JSON.stringify(counts));
  console.log('modalOpen:', modalOpen, 'modalClosed:', modalClosed, 'hotOk:', hotOk, 'search:', JSON.stringify(searchOk));
  if (errors.length) { console.log('ERRORS:\n' + errors.join('\n')); console.log('FAIL'); process.exit(1); }
  if (!modalOpen || !modalClosed || !hotOk) { console.log('INTERACTION FAIL'); process.exit(1); }
  console.log('PASS');
})().catch(e => { console.error(e); process.exit(1); });
