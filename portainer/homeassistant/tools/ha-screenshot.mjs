#!/usr/bin/env node
// Screenshot a Home Assistant page in a throwaway headless Chromium, without the Browser pane.
//
//   node ha-screenshot.mjs [--card <text>] [--click <text>] [--width 390] [--light] [--wait 6] <path> <out.png>
//
// <path> is a frontend path such as /lovelace/power. --card crops to the smallest card whose text
// contains <text>; without it the whole page is captured. --click clicks the smallest element with that
// text first, for popups and expanded panels. The page renders dark unless --light.
//
// Chrome on the work Mac refuses DevTools automation by admin policy, so the browser runs on the
// Beelink: this script sends itself over `ssh beelink` into a one-off Playwright container and gets
// the PNG back on stdout. URL, token and arguments travel inside the script text on stdin, never
// on a command line. The token is put into a temporary Chromium profile inside the container.

import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const IMAGE = 'mcr.microsoft.com/playwright:v1.52.0-noble';
const CHROME = '/ms-playwright/chromium-1169/chrome-linux/chrome';
const remote = globalThis.__HA_SHOT;

function creds() {
  let url = process.env.HOMEASSISTANT_URL, token = process.env.HOMEASSISTANT_TOKEN;
  if (!url || !token) {
    const cfg = JSON.parse(readFileSync(join(homedir(), 'Library/Application Support/Claude/claude_desktop_config.json'), 'utf8'));
    const env = cfg.mcpServers?.['home-assistant']?.env || {};
    url ||= env.HOMEASSISTANT_URL;
    token ||= env.HOMEASSISTANT_TOKEN;
  }
  if (!url || !token) throw new Error('no HA url/token');
  return { origin: new URL(url).origin, token };
}

const args = remote ? [...remote.argv] : process.argv.slice(2);
const opt = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args.splice(i, 2)[1];
};
const flag = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? false : (args.splice(i, 1), true);
};
const card = opt('--card');
const click = opt('--click');
const width = +(opt('--width') ?? 390);
const wait = +(opt('--wait') ?? 6);
const light = flag('--light');
const [path, out] = args;
if (!path || !out) {
  console.error('usage: [--card text] [--click text] [--width 390] [--light] [--wait 6] <path> <out.png>');
  process.exit(2);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const step = (s) => process.env.SHOT_DEBUG && console.error(s);

if (!remote) {
  // Local side: ship this file to the Beelink and save what comes back.
  const prelude = `globalThis.__HA_SHOT = ${JSON.stringify({ ...creds(), argv: process.argv.slice(2), debug: !!process.env.SHOT_DEBUG })};\n`;
  const src = readFileSync(fileURLToPath(import.meta.url), 'utf8').replace(/^#!.*\n/, '');
  const ssh = spawn('ssh', ['beelink', `docker run --rm -i --network host -e TZ=Europe/Kyiv ${IMAGE} node --input-type=module -`], { stdio: ['pipe', 'pipe', 'inherit'] });
  const chunks = [];
  ssh.stdout.on('data', (c) => chunks.push(c));
  ssh.stdin.end(prelude + src);
  const code = await new Promise((r) => ssh.on('close', r));
  if (code !== 0) process.exit(code);
  writeFileSync(out, Buffer.concat(chunks));
  console.log(`saved ${out}`);
  process.exit(0);
}

// Container side.
if (remote.debug) process.env.SHOT_DEBUG = '1';
const { origin, token } = remote;
const profile = mkdtempSync(join(tmpdir(), 'ha-shot-'));
// Without --disable-dev-shm-usage the renderer stalls on the container's 64 MB /dev/shm.
const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-pipe', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });

try {
  const [toChrome, fromChrome] = [chrome.stdio[3], chrome.stdio[4]];
  let id = 0, buf = '', session;
  const waiting = new Map();
  fromChrome.on('data', (chunk) => {
    buf += chunk.toString();
    let i;
    while ((i = buf.indexOf('\0')) >= 0) {
      const m = JSON.parse(buf.slice(0, i));
      buf = buf.slice(i + 1);
      if (m.id && waiting.has(m.id)) {
        const w = waiting.get(m.id);
        waiting.delete(m.id);
        m.error ? w.rej(new Error(m.error.message)) : w.res(m.result);
      }
    }
  });
  // A call that gets no answer fails after 20 s instead of hanging.
  const cdp = (method, params = {}) => new Promise((res, rej) => {
    const n = ++id;
    const t = setTimeout(() => { waiting.delete(n); rej(new Error(`${method} timed out`)); }, 20000);
    waiting.set(n, { res: (v) => { clearTimeout(t); res(v); }, rej: (e) => { clearTimeout(t); rej(e); } });
    toChrome.write(JSON.stringify({ id: n, method, params, ...(session ? { sessionId: session } : {}) }) + '\0');
  });
  const { targetInfos } = await cdp('Target.getTargets');
  const page = targetInfos.find((t) => t.type === 'page');
  if (!page) throw new Error('chrome has no page');
  session = (await cdp('Target.attachToTarget', { targetId: page.targetId, flatten: true })).sessionId;
  const evaluate = async (expression) => (await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.value;

  step('cdp open');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width, height: 2400, deviceScaleFactor: 2, mobile: true });
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: light ? 'light' : 'dark' }] });
  const tokens = { access_token: token, token_type: 'Bearer', expires_in: 1800, refresh_token: '', hassUrl: origin, clientId: origin + '/', expires: Date.now() + 365 * 864e5 };
  await cdp('Page.addScriptToEvaluateOnNewDocument', {
    source: `if (location.origin === ${JSON.stringify(origin)}) localStorage.setItem('hassTokens', ${JSON.stringify(JSON.stringify(tokens))});`,
  });
  await cdp('Page.navigate', { url: origin + path });
  step('navigated');
  await sleep(wait * 1000);
  if (process.env.SHOT_DEBUG) step('page ' + await evaluate('location.href + " | " + document.title'));

  if (click) {
    const ok = await evaluate(`(() => {
      const hits = [];
      const walk = (root) => { for (const el of root.querySelectorAll('*')) {
        if (el.textContent.includes(${JSON.stringify(click)})) hits.push(el);
        if (el.shadowRoot) walk(el.shadowRoot); } };
      walk(document);
      if (!hits.length) return false;
      hits.sort((a, b) => a.textContent.length - b.textContent.length);
      hits[0].click();
      return true;
    })()`);
    if (!ok) throw new Error(`nothing to click with text: ${click}`);
    await sleep(1000);
  }

  let clip;
  if (card) {
    clip = await evaluate(`(() => {
      const hits = [];
      const walk = (root) => { for (const el of root.querySelectorAll('*')) {
        if (el.tagName === 'HA-CARD' && el.innerText.includes(${JSON.stringify(card)})) hits.push(el);
        if (el.shadowRoot) walk(el.shadowRoot); } };
      walk(document);
      if (!hits.length) return null;
      hits.sort((a, b) => a.innerText.length - b.innerText.length);
      const r = hits[0].getBoundingClientRect();
      return { x: Math.max(r.x - 8, 0), y: r.y + scrollY - 8, width: r.width + 16, height: r.height + 16, scale: 1 };
    })()`);
    if (!clip) throw new Error(`no card with text: ${card}`);
  }
  step('clip ' + JSON.stringify(clip));
  const shot = await cdp('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, ...(clip ? { clip } : {}) });
  process.stdout.write(Buffer.from(shot.data, 'base64'));
} finally {
  chrome.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
