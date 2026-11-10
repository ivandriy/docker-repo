#!/usr/bin/env node
// Read or save a Lovelace dashboard config over the HA websocket API, without a browser.
//
//   node ha-lovelace.mjs get  [--dash <url_path>] <out.json>
//   node ha-lovelace.mjs save [--dash <url_path>] --base <orig.json> <new.json>
//
// The default dashboard is used when --dash is omitted. `save` refuses to write when the live
// config no longer matches --base (someone changed the dashboard after `get`).
// URL and token come from HOMEASSISTANT_URL / HOMEASSISTANT_TOKEN, or from the ha-mcp entry in
// the Claude desktop config, so the token is never copied anywhere.

import { readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

function creds() {
  let url = process.env.HOMEASSISTANT_URL, token = process.env.HOMEASSISTANT_TOKEN;
  if (!url || !token) {
    const cfg = JSON.parse(readFileSync(join(homedir(), 'Library/Application Support/Claude/claude_desktop_config.json'), 'utf8'));
    const env = cfg.mcpServers?.['home-assistant']?.env || {};
    url ||= env.HOMEASSISTANT_URL;
    token ||= env.HOMEASSISTANT_TOKEN;
  }
  if (!url || !token) throw new Error('no HA url/token');
  return { url: url.replace(/^http/, 'ws').replace(/\/$/, '') + '/api/websocket', token };
}

function connect() {
  const { url, token } = creds();
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let id = 0;
    const waiting = new Map();
    const call = (msg) => new Promise((res, rej) => {
      const n = ++id;
      waiting.set(n, { res, rej });
      ws.send(JSON.stringify({ id: n, ...msg }));
    });
    ws.onerror = () => reject(new Error('websocket error'));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: token }));
      else if (m.type === 'auth_ok') resolve({ call, close: () => ws.close() });
      else if (m.type === 'auth_invalid') reject(new Error('auth invalid'));
      else if (m.type === 'result' && waiting.has(m.id)) {
        const w = waiting.get(m.id);
        waiting.delete(m.id);
        m.success ? w.res(m.result) : w.rej(new Error(JSON.stringify(m.error)));
      }
    };
  });
}

const args = process.argv.slice(2);
const cmd = args.shift();
const opt = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args.splice(i, 2)[1];
};
const dash = opt('--dash') ?? null;
const base = opt('--base');
const file = args[0];

if (!['get', 'save'].includes(cmd) || !file || (cmd === 'save' && !base)) {
  console.error('usage: get [--dash p] <out.json> | save [--dash p] --base <orig.json> <new.json>');
  process.exit(2);
}

const ha = await connect();
try {
  const live = await ha.call({ type: 'lovelace/config', url_path: dash });
  if (cmd === 'get') {
    writeFileSync(file, JSON.stringify(live, null, 2));
    console.log(`saved ${file}: ${live.views?.length ?? 0} views`);
  } else {
    const orig = JSON.parse(readFileSync(base, 'utf8'));
    if (JSON.stringify(orig) !== JSON.stringify(live)) {
      console.error('live dashboard differs from --base; run get again and reapply the change');
      process.exit(1);
    }
    const next = JSON.parse(readFileSync(file, 'utf8'));
    const changed = (next.views || []).map((v, i) => JSON.stringify(v) !== JSON.stringify(live.views?.[i]) ? i : null).filter((i) => i !== null);
    await ha.call({ type: 'lovelace/config/save', url_path: dash, config: next });
    console.log(`saved dashboard ${dash ?? 'default'}; views changed: ${changed.join(', ') || 'none'}`);
  }
} finally {
  ha.close();
}
