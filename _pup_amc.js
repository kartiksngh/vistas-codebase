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

  // ── Firms & Schemes tab: firm selector switches firm blocks; a scheme row opens its panel ──
  const firms = await page.evaluate(() => {
    showTab('schemes');
    const pills = Array.from(document.querySelectorAll('.firmpill'));
    const blocks = Array.from(document.querySelectorAll('.firmblock'));
    const shown = () => document.querySelectorAll('.firmblock').filter
      ? null : Array.from(document.querySelectorAll('.firmblock')).filter(b => b.style.display !== 'none').map(b => b.id);
    const defaultShown = Array.from(document.querySelectorAll('.firmblock')).filter(b => b.style.display !== 'none').map(b => b.id);
    return { nPills: pills.length, nBlocks: blocks.length, defaultShown,
             firstPillKey: pills[0] ? pills[0].getAttribute('data-firm') : null,
             secondPillKey: pills[1] ? pills[1].getAttribute('data-firm') : null };
  });
  // exactly one firm block visible by default
  let switchOk = true, schemeOk = true;
  if (firms.secondPillKey) {
    switchOk = await page.evaluate((k) => {
      showFirm(k);
      const vis = Array.from(document.querySelectorAll('.firmblock')).filter(b => b.style.display !== 'none').map(b => b.id);
      return vis.length === 1 && vis[0] === 'firm-' + k;
    }, firms.secondPillKey);
  }
  // open the first scheme row in the (now default) firm and confirm a scheme panel appears
  schemeOk = await page.evaluate((k) => {
    if (k) showFirm(k);
    const row = document.querySelector('.firmblock[style*="block"] .bktbl tbody tr');
    if (!row) return true;                       // a freshly-seeded firm may have no replay rows yet
    row.click();
    const open = Array.from(document.querySelectorAll('.schpanel')).some(p => p.style.display === 'block');
    schemesHome();
    return open;
  }, firms.firstPillKey);

  await browser.close();
  console.log('counts:', JSON.stringify(counts));
  console.log('modalOpen:', modalOpen, 'modalClosed:', modalClosed, 'hotOk:', hotOk, 'search:', JSON.stringify(searchOk));
  console.log('firms:', JSON.stringify(firms), 'switchOk:', switchOk, 'schemeOk:', schemeOk);
  if (errors.length) { console.log('ERRORS:\n' + errors.join('\n')); console.log('FAIL'); process.exit(1); }
  if (!modalOpen || !modalClosed || !hotOk) { console.log('INTERACTION FAIL'); process.exit(1); }
  if (firms.nPills < 1 || firms.nBlocks < 1 || firms.defaultShown.length !== 1) { console.log('FIRMS FAIL'); process.exit(1); }
  if (!switchOk || !schemeOk) { console.log('FIRMS INTERACTION FAIL'); process.exit(1); }
  console.log('PASS');
})().catch(e => { console.error(e); process.exit(1); });
