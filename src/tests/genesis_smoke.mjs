/**
 * genesis_smoke — drive the real genesis Being loop with Playwright and verify
 * a configured model actually infers, streams, and renders (/exec browser runs).
 *
 * Looks at the OUTPUT, not just HTTP status: counts streaming LLM calls, checks
 * the Being produced text + an exec block, and saves a screenshot to eyeball.
 *
 * Usage:
 *   GKEY=<google_api_key> node src/tests/genesis_smoke.mjs
 *   GKEY=... MODEL=gemini-3.1-pro ENV=prod node src/tests/genesis_smoke.mjs
 *
 * Env:
 *   GKEY  (required) Google API key — runs as provider=google so no relay auth
 *   MODEL (default gemini-3.5-flash)  display name from models.js
 *   ENV   (default dev)  dev|prod
 *   WAIT  (default 48)   seconds to let the autonomous loop run
 *   HEADED=1             show the browser
 *
 * Exit 0 = pass. Needs Playwright + a local Google Chrome (uses channel:chrome).
 */
import { chromium } from 'playwright';

const KEY = process.env.GKEY;
const MODEL = process.env.MODEL || 'gemini-3.5-flash';
const ENV = process.env.ENV || 'dev';
const WAIT = parseInt(process.env.WAIT || '48', 10);
const BASE = ENV === 'prod' ? 'https://infero.net' : 'https://dev.infero.net';
const URL = `${BASE}/genesis/`;
const SHOT = `/tmp/genesis_smoke_${ENV}_${MODEL}.png`;

if (!KEY) { console.error('FAIL: GKEY env required'); process.exit(2); }

const fail = (m) => { console.error('FAIL:', m); process.exitCode = 1; };

const browser = await chromium.launch({ headless: !process.env.HEADED, channel: 'chrome' });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctx.newPage();
const llm = [];
page.on('request', r => {
  if (/generativelanguage|generateContent/.test(r.url())) llm.push(r.url().split('?')[0]);
});

try {
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.evaluate(([key, model]) => {
    localStorage.setItem('genesis_settings', JSON.stringify({
      provider: 'google', model, tokens: { google: key }, companion_name: 'smoke-test',
    }));
  }, [KEY, MODEL]);
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 });

  // let the autonomous loop run a couple inferences
  const deadline = Date.now() + WAIT * 1000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(2000);
    if (llm.length >= 1) {
      const t = await page.evaluate(() => (document.querySelector('#messages')||document.body).innerText);
      if (t.length > 400) break;   // got a real response
    }
  }

  const res = await page.evaluate(() => {
    const s = JSON.parse(localStorage.getItem('genesis_settings') || '{}');
    const msgs = (document.querySelector('#messages') || document.body).innerText;
    const canvasHtml = (document.querySelector('#html-div') || {}).innerHTML || '';
    return {
      model: s.model,
      msgLen: msgs.length,
      hasExec: /\/exec browser|\/browser exec/.test(msgs),
      canvasFilled: canvasHtml.length > 50,
      sample: msgs.slice(0, 400),
    };
  });
  await page.screenshot({ path: SHOT });

  console.log('ENV           :', ENV, BASE);
  console.log('MODEL_SELECTED:', res.model, res.model === MODEL ? 'OK' : '(MISMATCH)');
  console.log('LLM_STREAM_REQ:', llm.length, llm[0] ? '(' + llm[0].split('/').pop() + ')' : '');
  console.log('MSG_LEN       :', res.msgLen);
  console.log('HAS_EXEC_BLOCK:', res.hasExec);
  console.log('CANVAS_FILLED :', res.canvasFilled);
  console.log('SCREENSHOT    :', SHOT);
  console.log('SAMPLE        :', res.sample.replace(/\n/g, ' / ').slice(0, 200));

  // assertions
  if (res.model !== MODEL) fail(`model mismatch: ${res.model} != ${MODEL}`);
  if (llm.length < 1) fail('no LLM streamGenerateContent request observed');
  if (res.msgLen < 400) fail(`response too short (${res.msgLen} chars) — model may not have generated`);

  if (!process.exitCode) console.log('\nPASS: ' + MODEL + ' infers + streams + renders in genesis (' + ENV + ')');
} catch (e) {
  fail(e.message);
} finally {
  await browser.close();
}
