// Hourly heatmap per room (recorder hourly mean) and heating hours per day for a week (climate history).
// Config: rooms: [{name, temperature, humidity}], heating: [{name, entity, color}], outdoor: sensor with daily mean statistics.
const DAYS = ['Нд', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const SCALES = {
  temperature: { unit: '°', lo: 21, hi: 26, digits: 1,
    stops: [[21, [58, 110, 200]], [22.5, [90, 160, 180]], [24, [255, 214, 120]], [26, [255, 140, 10]]] },
  humidity: { unit: ' %', lo: 30, hi: 75, digits: 0,
    stops: [[30, [196, 164, 104]], [45, [90, 88, 102]], [60, [58, 150, 210]], [75, [100, 210, 255]]] },
};

function color(scale, v) {
  const st = scale.stops;
  if (v <= st[0][0]) return `rgb(${st[0][1].join(',')})`;
  for (let i = 0; i < st.length - 1; i++) {
    const [v0, c0] = st[i], [v1, c1] = st[i + 1];
    if (v <= v1) {
      const k = (v - v0) / (v1 - v0);
      return `rgb(${c0.map((x, j) => Math.round(x + (c1[j] - x) * k)).join(',')})`;
    }
  }
  return `rgb(${st[st.length - 1][1].join(',')})`;
}
const fmt = (v, d) => v.toFixed(d).replace('.', ',');
const ts = (t) => (typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t));

class ClimateStatsCard extends HTMLElement {
  setConfig(config) {
    if (!config.rooms) throw new Error('rooms required');
    this._c = config;
    this._mode = this._mode || 'temperature';
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
  }
  set hass(hass) {
    this._hass = hass;
    const now = Date.now();
    if (!this._loadedAt || now - this._loadedAt > 10 * 60 * 1000) {
      this._loadedAt = now;
      this._load();
    }
  }
  getCardSize() { return 7; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }

  async _load() {
    this._err = null;
    try {
      await Promise.all([this._loadHours(), this._loadWeek()]);
    } catch (e) {
      this._err = (e && e.message) || String(e);
    }
    this._render();
  }

  async _loadHours() {
    const end = new Date();
    const start = new Date(end);
    start.setMinutes(0, 0, 0);
    start.setHours(start.getHours() - 23);
    const ids = [];
    for (const r of this._c.rooms) {
      if (r.temperature) ids.push(r.temperature);
      if (r.humidity) ids.push(r.humidity);
    }
    const res = await this._hass.callWS({
      type: 'recorder/statistics_during_period',
      start_time: start.toISOString(), end_time: end.toISOString(),
      statistic_ids: ids, period: 'hour', types: ['mean'],
    });
    const hours = {};
    for (const id of ids) {
      const cells = new Array(24).fill(null);
      for (const row of res[id] || []) {
        const i = Math.round((ts(row.start) - start.getTime()) / 3600000);
        if (i >= 0 && i < 24 && row.mean != null) cells[i] = row.mean;
      }
      hours[id] = cells;
    }
    this._hours = hours;
    this._hStart = start;
  }

  async _loadWeek() {
    const list = this._c.heating || [];
    if (!list.length) { this._week = null; return; }
    const end = new Date();
    const start = new Date(end);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - 6);
    const out = this._c.outdoor;
    const [res, stats] = await Promise.all([
      this._hass.callWS({
        type: 'history/history_during_period',
        start_time: start.toISOString(), end_time: end.toISOString(),
        entity_ids: list.map((x) => x.entity),
        include_start_time_state: true, significant_changes_only: false,
        minimal_response: false, no_attributes: false,
      }),
      out ? this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: start.toISOString(), end_time: end.toISOString(),
        statistic_ids: [out], period: 'day', types: ['mean'],
      }) : Promise.resolve({}),
    ]);
    const days = [];
    for (let d = 0; d < 7; d++) {
      const s = new Date(start); s.setDate(start.getDate() + d);
      const e = new Date(s); e.setDate(s.getDate() + 1);
      days.push({ s: s.getTime(), e: Math.min(e.getTime(), end.getTime()), dow: s.getDay(), v: {}, on: false, out: null });
    }
    for (const row of (out && stats[out]) || []) {
      const day = days.find((x) => Math.abs(ts(row.start) - x.s) < 2 * 3600000);
      if (day && row.mean != null) day.out = row.mean;
    }
    for (const h of list) {
      const rows = res[h.entity] || [];
      let attrs = {};
      const segs = [];
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i];
        if (r.a) attrs = r.a;
        const t0 = Math.max(ts(r.lu != null ? r.lu : r.lc), start.getTime());
        const t1 = i + 1 < rows.length ? ts(rows[i + 1].lu != null ? rows[i + 1].lu : rows[i + 1].lc) : end.getTime();
        if (attrs.hvac_action === 'heating' && t1 > t0) segs.push([t0, t1]);
        if (r.s === 'heat' && t1 > t0) for (const day of days) if (Math.min(t1, day.e) > Math.max(t0, day.s)) day.on = true;
      }
      for (const day of days) {
        let ms = 0;
        for (const [a, b] of segs) {
          const x = Math.max(a, day.s), y = Math.min(b, day.e);
          if (y > x) ms += y - x;
        }
        day.v[h.entity] = ms / 3600000;
      }
    }
    this._week = days;
  }

  _more(entityId) {
    this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId }, bubbles: true, composed: true }));
  }

  _render() {
    const root = this.shadowRoot;
    if (!root) return;
    const sc = SCALES[this._mode];
    const key = this._mode === 'temperature' ? 'temperature' : 'humidity';
    let rowsHtml = '';
    const hStart = this._hStart ? this._hStart.getTime() : Date.now();
    for (const r of this._c.rooms) {
      const id = r[key];
      if (!id) continue;
      const cells = (this._hours && this._hours[id]) ? this._hours[id].slice() : new Array(24).fill(null);
      const st = this._hass.states[id];
      const live = st ? parseFloat(st.state) : NaN;
      if (!isNaN(live)) cells[23] = live;
      const vals = cells.filter((v) => v != null);
      const mn = vals.length ? Math.min(...vals) : null, mx = vals.length ? Math.max(...vals) : null;
      const cellHtml = cells.map((v, i) => {
        const hh = new Date(hStart + i * 3600000).getHours().toString().padStart(2, '0');
        if (v == null) return `<i class="c na" title="${r.name} ${hh}:00 · немає даних"></i>`;
        return `<i class="c${i === 23 ? ' nw' : ''}" style="background:${color(sc, v)}" title="${r.name} ${hh}:00 · ${fmt(v, sc.digits)}${sc.unit}"></i>`;
      }).join('');
      const range = mn == null ? '' : `${fmt(mn, sc.digits)}–${fmt(mx, sc.digits)}`;
      rowsHtml += `<div class="row" data-id="${id}"><span class="rn">${r.name}</span><div class="cells">${cellHtml}</div><span class="rr">${range}</span></div>`;
    }
    const ax = [0, 6, 12, 18].map((i) => new Date(hStart + i * 3600000).getHours().toString().padStart(2, '0') + ':00');

    let weekHtml = '';
    if (this._week) {
      // Heating hours per room per day as grouped bars (all 7 days, off days dashed) with the daily mean
      // outdoor temperature as a line over them. Column centres are (i + 0.5) / 7, so the grid has no gap.
      const list = this._c.heating, wk = this._week;
      const BAR_H = 70, LINE_TOP = 16, LINE_H = 40;
      const max = Math.max(4, ...wk.flatMap((d) => list.map((h) => d.v[h.entity] || 0)));
      const today = new Date().getDay();
      const cols = wk.map((d) => {
        const now = d.dow === today;
        const sum = list.reduce((a, h) => a + (d.v[h.entity] || 0), 0);
        const bars = d.on
          ? list.map((h) => {
            const v = d.v[h.entity] || 0;
            return `<b style="height:${Math.max(Math.round(v / max * BAR_H), 2)}px;background:${v > 0 ? h.color : 'rgba(127,127,127,.35)'}" title="${h.name}: ${fmt(v, 1)} год"></b>`;
          }).join('')
          : '<div class="offb" title="опалення було вимкнене"></div>';
        const sub = d.on ? (sum > 0 ? fmt(sum, sum < 10 ? 1 : 0) : '0') + ' год' : 'вимк.';
        return `<div class="wc"><div class="wb">${bars}</div><span class="wl${now ? ' now' : ''}">${DAYS[d.dow]}</span><span class="ws${d.on ? '' : ' off'}">${sub}</span></div>`;
      }).join('');
      const pts = wk.map((d, i) => ({ x: (i + 0.5) / 7 * 100, t: d.out, part: i === wk.length - 1 })).filter((p) => p.t != null);
      let line = '';
      if (pts.length) {
        const lo = Math.min(...pts.map((p) => p.t)) - 1, hi = Math.max(...pts.map((p) => p.t)) + 1;
        const y = (t) => LINE_TOP + (1 - (t - lo) / (hi - lo)) * LINE_H;
        line = `<svg class="ol" viewBox="0 0 100 ${LINE_TOP + LINE_H + 8}" preserveAspectRatio="none"><polyline points="${pts.map((p) => p.x.toFixed(2) + ',' + y(p.t).toFixed(1)).join(' ')}" fill="none" stroke="#64D2FF" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`
          + pts.map((p) => `<i class="od${p.part ? ' part' : ''}" style="left:${p.x}%;top:${y(p.t)}px" title="надворі ${fmt(p.t, 1)}°${p.part ? ', поки що' : ''}"></i><em class="ot" style="left:${p.x}%;top:${y(p.t) - 17}px">${Math.round(p.t)}°</em>`).join('');
      }
      const tot = list.map((h) => {
        const s = wk.reduce((a, d) => a + (d.v[h.entity] || 0), 0);
        return `<span><i style="background:${h.color}"></i>${h.name} ${fmt(s, 0)} год</span>`;
      }).join('') + (pts.length ? '<span><i style="background:#64D2FF;height:3px;border-radius:2px"></i>надворі, середня за день</span>' : '');
      weekHtml = `<div class="sec"><div class="hd"><span class="t">Опалення і погода</span><span class="s">годин на день · °C надворі</span></div>
        <div class="wk"><div class="wcols">${cols}</div>${line}</div><div class="leg">${tot}</div></div>`;
    }

    root.innerHTML = `<style>
      ha-card{padding:12px 14px;display:flex;flex-direction:column;gap:14px}
      .hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
      .t{font-size:15px;font-weight:500;color:var(--primary-text-color)}
      .s{font-size:12px;color:var(--secondary-text-color)}
      .tg{display:flex;background:rgba(127,127,127,.15);border-radius:9px;padding:2px}
      .tg button{border:0;background:transparent;color:var(--secondary-text-color);font:inherit;font-size:12px;padding:5px 10px;border-radius:7px;cursor:pointer}
      .tg button.on{background:rgba(127,127,127,.35);color:var(--primary-text-color);font-weight:500}
      .row{display:flex;align-items:center;gap:6px;margin:3px 0;cursor:pointer}
      .rn{width:62px;flex:none;font-size:12px;color:var(--secondary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .rr{width:62px;flex:none;font-size:12px;color:#A1A1A6;text-align:right}
      .cells{flex:1;display:grid;grid-template-columns:repeat(24,minmax(0,1fr));gap:2px}
      .c{display:block;height:18px;border-radius:3px}
      .c.na{background:rgba(127,127,127,.12)}
      .c.nw{box-shadow:0 0 0 1.5px var(--primary-text-color)}
      .ax{display:flex;justify-content:space-between;font-size:12px;color:#A1A1A6;margin:4px 68px 0 68px}
      .grad{display:flex;align-items:center;gap:6px;font-size:12px;color:#A1A1A6;margin:6px 68px 0 68px}
      .grad div{flex:1;height:6px;border-radius:3px}
      .wk{position:relative}
      .wcols{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
      .wc{display:flex;flex-direction:column;align-items:center;gap:2px;min-width:0}
      .wb{height:132px;width:100%;display:flex;align-items:flex-end;justify-content:center;gap:2px;border-bottom:1px solid rgba(127,127,127,.25)}
      .wb b{display:block;width:12px;border-radius:3px 3px 0 0}
      .offb{width:70%;border-bottom:2px dashed rgba(127,127,127,.45)}
      .wl{font-size:12px;color:#A1A1A6;margin-top:4px}
      .wl.now{color:var(--primary-text-color);font-weight:600}
      .ws{font-size:11px;color:var(--primary-text-color);white-space:nowrap}
      .ws.off{color:#A1A1A6;opacity:.7}
      .ol{position:absolute;left:0;top:0;width:100%;height:64px;overflow:visible;pointer-events:none}
      .od{position:absolute;width:7px;height:7px;margin:-3.5px 0 0 -3.5px;border-radius:50%;background:#64D2FF;box-shadow:0 0 0 2px var(--ha-card-background,var(--card-background-color,#1c1c1e))}
      .od.part{background:var(--ha-card-background,var(--card-background-color,#1c1c1e));box-shadow:inset 0 0 0 1.5px #64D2FF}
      .ot{position:absolute;transform:translateX(-50%);font-style:normal;font-size:11px;font-weight:500;color:#64D2FF;white-space:nowrap}
      .leg{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--secondary-text-color);margin-top:8px}
      .leg span{display:inline-flex;align-items:center;gap:5px}
      .leg i{width:9px;height:9px;border-radius:2px;display:inline-block}
      .err{font-size:12px;color:#FF9F0A}
    </style>
    <ha-card>
      <div class="sec">
        <div class="hd"><span class="t">${this._mode === 'temperature' ? 'Температура' : 'Вологість'} по годинах</span>
          <div class="tg"><button data-m="temperature" class="${this._mode === 'temperature' ? 'on' : ''}">Темп.</button><button data-m="humidity" class="${this._mode === 'humidity' ? 'on' : ''}">Волог.</button></div></div>
        ${rowsHtml}
        <div class="ax">${ax.map((a) => `<span>${a}</span>`).join('')}<span>зараз</span></div>
        <div class="grad"><span>${sc.lo}${sc.unit}</span><div style="background:linear-gradient(90deg,${sc.stops.map((s) => color(sc, s[0])).join(',')})"></div><span>${sc.hi}${sc.unit}</span></div>
      </div>
      ${weekHtml}
      ${this._err ? `<div class="err">Помилка даних: ${this._err}</div>` : ''}
    </ha-card>`;
    root.querySelectorAll('.tg button').forEach((b) => b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this._mode = b.dataset.m;
      this._render();
    }));
    root.querySelectorAll('.row').forEach((r) => r.addEventListener('click', () => this._more(r.dataset.id)));
  }
}

if (!customElements.get('climate-stats-card')) customElements.define('climate-stats-card', ClimateStatsCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'climate-stats-card', name: 'Climate stats card', description: 'Hourly heatmap and weekly heating hours' });
