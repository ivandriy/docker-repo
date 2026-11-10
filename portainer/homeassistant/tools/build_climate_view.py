import json, copy, sys

# Usage: python3 build_climate_view.py <dir with dash.json>; writes dash_new.json next to it.
SCR = sys.argv[1].rstrip('/') + '/' if len(sys.argv) > 1 else './'
SEL = 'input_select.climate_selected_room'
PARK = [{'condition': 'state', 'entity': 'binary_sensor.power_outage', 'state': 'classic_card_parked'}]

ROOMS = {
    'bedroom': {'name': 'Спальня', 't': 'sensor.bedroom_temperature_sensor_temperature', 'h': 'sensor.bedroom_temperature_sensor_humidity',
                'heat': 'climate.my_thermostat', 'ac': 'climate.bedroom_ac', 'hum': None, 'win': 'binary_sensor.0xa4c13858a269b68c_contact'},
    'childroom': {'name': 'Дитяча', 't': 'sensor.childroom_temperature_sensor_temperature', 'h': 'sensor.childroom_temperature_sensor_humidity',
                  'heat': 'climate.childroom', 'ac': 'climate.childroom_ac', 'hum': None, 'win': 'binary_sensor.childroom_window_sensor_contact'},
    'livingroom': {'name': 'Вітальня', 't': 'sensor.livingroom_temperature_sensor_temperature', 'h': 'sensor.livingroom_temperature_sensor_humidity',
                   'heat': None, 'ac': 'climate.livingroom_ac', 'hum': 'humidifier.livingroom_humidifier', 'win': None},
    'bathroom': {'name': 'Ванна', 't': 'sensor.bathroom_temperature_sensor_temperature_2', 'h': 'sensor.bathroom_temperature_sensor_humidity_2',
                 'heat': None, 'ac': None, 'hum': 'humidifier.dehumidifier_in_bathroom', 'win': None},
}

def ents(r):
    return [e for e in (r['t'], r['h'], r['heat'], r['ac'], r['hum'], r['win']) if e]

def single(area='b'):
    return [{'grid-template-areas': f'"{area}"'}, {'grid-template-columns': '1fr'}, {'grid-template-rows': '1fr'}]

FILL = [{'width': '100%'}, {'height': '100%'}, {'justify-self': 'stretch'}, {'align-self': 'stretch'}]

COMMON_JS = r"""
const S = (e) => e ? states[e] : undefined;
const num = (e) => { const s = S(e); const v = s ? parseFloat(s.state) : NaN; return isNaN(v) ? null : v; };
const f1 = (v) => v === null || v === undefined ? '—' : Number(v).toFixed(1).replace('.', ',');
const off = (s) => !s || ['off', 'unavailable', 'unknown'].includes(s.state);
const acLbl = {cool: 'охолодження', heat: 'обігрів', dry: 'сушіння', fan_only: 'вентилятор', heat_cool: 'авто'};
const presetLbl = {home: 'ніч', away: 'нема вдома', none: 'тепло', comfort: 'комфорт', eco: 'еко', sleep: 'сон', boost: 'буст', activity: 'активність'};
"""

# Night/day/evening boundaries for today, in minutes. Live values win: the night schedule's next_event
# (end of the morning block while on, start of the evening block while off), and the evening start from
# sensor.time_of_day / sun.sun (the night automation switches the bedroom to 25 on time_of_day == Вечір).
# NIGHT_END / NIGHT_START are only the fallback; keep them in step with schedule.night_schedule_thermostat.
DAYPLAN_JS = r"""
const NIGHT_END = ['07:00', '06:00', '06:00', '06:00', '06:00', '06:00', '07:00'];
const NIGHT_START = ['22:00', '22:30', '22:30', '22:30', '22:30', '22:30', '22:00'];
const now = new Date(), wk = now.getDay(), nowMin = now.getHours() * 60 + now.getMinutes();
const toMin = (t) => +t.slice(0, 2) * 60 + +t.slice(3, 5);
const dMin = (d) => d.getHours() * 60 + d.getMinutes();
const fmtMin = (m) => String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
const nsch = states['schedule.night_schedule_thermostat'];
let mEnd = toMin(NIGHT_END[wk]), nStart = toMin(NIGHT_START[wk]);
if (nsch && nsch.attributes.next_event) {
  const ne = new Date(nsch.attributes.next_event);
  if (ne.toDateString() === now.toDateString()) {
    if (nsch.state === 'on' && dMin(ne) < 720) mEnd = dMin(ne);
    if (nsch.state === 'off' && dMin(ne) >= 720) nStart = dMin(ne);
  }
}
const nightOn = nsch ? nsch.state === 'on' : (nowMin < mEnd || nowMin >= nStart);
const nightEndTxt = nowMin < 720 ? fmtMin(mEnd) : NIGHT_END[(wk + 1) % 7];
let eStart = 16 * 60 + 30;
const tod = states['sensor.time_of_day'], sun = states['sun.sun'];
const todAt = tod ? new Date(tod.last_changed) : null;
if (tod && tod.state === 'Вечір' && todAt.toDateString() === now.toDateString() && todAt.getHours() >= 12) eStart = dMin(todAt);
else if (sun && sun.attributes.next_setting) eStart = dMin(new Date(sun.attributes.next_setting));
const presetT = (e, p) => { const s = states[e]; if (!s) return null; try { const v = JSON.parse(s.attributes.bt_preset_heat_temperatures || '{}')[p]; return v === undefined ? null : v; } catch (x) { return null; } };
const deg = (v) => v === null || v === undefined ? '—' : String(v).replace('.', ',') + '°';
"""

def status_js(rjson):
    # returns JS that defines kind, icon, col, txt for a room R
    return COMMON_JS + "const R = " + rjson + ";\n" + r"""
const heat = S(R.heat), ac = S(R.ac), hum = S(R.hum), win = S(R.win);
const h = num(R.h);
const hs = h === null ? '' : Math.round(h) + ' %';
let kind = 'idle', icon = 'mdi:pause', txt = hs, col = 'var(--secondary-text-color)';
if (!off(ac)) {
  kind = 'ac'; icon = ac.state === 'heat' ? 'mdi:sun-thermometer' : 'mdi:snowflake'; col = '#64D2FF';
  txt = 'кондиц. ' + (acLbl[ac.state] || ac.state) + ' ' + ac.attributes.temperature + '°';
} else if (heat && heat.attributes.hvac_action === 'heating') {
  kind = 'heat'; icon = 'mdi:fire'; col = '#FFD9A0';
  txt = '→ ' + f1(heat.attributes.temperature) + '° · ' + hs;
} else if (hum && hum.state === 'on' && ['humidifying', 'drying'].includes(hum.attributes.action)) {
  kind = 'hum'; icon = 'mdi:water'; col = '#A9E6FF';
  txt = (hum.attributes.action === 'drying' ? 'осушує ' : 'зволожує ') + Math.round(hum.attributes.current_humidity) + ' → ' + hum.attributes.humidity + ' %';
} else if (heat && heat.state === 'heat') {
  icon = 'mdi:thermostat'; txt = 'тримає ' + f1(heat.attributes.temperature) + '° · ' + hs;
} else if (heat && heat.state === 'off') {
  icon = 'mdi:power'; txt = 'опалення вимк · ' + hs;
} else if (hum && hum.state === 'on') {
  icon = 'mdi:water-outline'; txt = hs + ' · ціль ' + hum.attributes.humidity + ' %';
} else {
  icon = 'mdi:water-percent';
}
if (heat && heat.attributes.preset_mode && ['home', 'away'].includes(heat.attributes.preset_mode)) txt += ' · ' + presetLbl[heat.attributes.preset_mode];
if (!R.heat && kind === 'idle') txt += ' · без обігріву';
if (win && win.state === 'on') { icon = 'mdi:window-open-variant'; col = '#FF9F0A'; txt = 'вікно відчинене · ' + txt; }
"""

def bg_js(rjson):
    return "[[[" + COMMON_JS + "const R = " + rjson + ";\n" + r"""
const heat = S(R.heat), ac = S(R.ac), hum = S(R.hum);
if (!off(ac)) return 'rgba(10,132,255,.26)';
if (heat && heat.attributes.hvac_action === 'heating') return 'rgba(255,159,10,.30)';
if (hum && hum.state === 'on' && ['humidifying', 'drying'].includes(hum.attributes.action)) return 'rgba(100,210,255,.16)';
return 'rgba(127,127,127,.14)';
]]]"""

def ring_js(name):
    return "[[[ return states['" + SEL + "'] && states['" + SEL + "'].state === '" + name + "' ? 'inset 0 0 0 2px var(--primary-text-color)' : 'none'; ]]]"

def room_tile(key, radius, height, compact=False):
    r = ROOMS[key]
    rj = json.dumps(r, ensure_ascii=False)
    if compact:
        body = "[[[" + status_js(rj) + r"""
return `<div style="display:flex;flex-direction:column;justify-content:space-between;height:100%;box-sizing:border-box;padding:8px 10px;text-align:left">
<div style="font-size:12px;color:var(--secondary-text-color);display:flex;align-items:center;gap:4px">${R.name}${kind === 'hum' ? '<ha-icon icon="mdi:water" style="--mdc-icon-size:13px;color:#A9E6FF"></ha-icon>' : ''}</div>
<div style="font-size:18px;font-weight:600;line-height:1">${f1(num(R.t))}° <span style="font-size:11px;font-weight:400;color:var(--secondary-text-color)">${hs}</span></div></div>`;
]]]"""
    else:
        body = "[[[" + status_js(rj) + r"""
return `<div style="display:flex;flex-direction:column;justify-content:space-between;height:100%;box-sizing:border-box;padding:10px;text-align:left">
<div style="font-size:12px;color:${kind === 'heat' ? '#FFD9A0' : (kind === 'hum' ? '#A9E6FF' : 'var(--secondary-text-color)')}">${R.name}</div>
<div style="font-size:28px;font-weight:600;line-height:1">${f1(num(R.t))}°</div>
<div style="display:flex;align-items:flex-start;gap:4px;font-size:12px;line-height:1.3;color:${col};white-space:normal;text-align:left"><ha-icon icon="${icon}" style="--mdc-icon-size:14px;flex:none"></ha-icon><span style="flex:1;text-align:left">${txt}</span></div></div>`;
]]]"""
    return {
        'type': 'custom:button-card',
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ents(r) + [SEL],
        'tap_action': {'action': 'perform-action', 'perform_action': 'input_select.select_option',
                       'target': {'entity_id': SEL}, 'data': {'option': r['name']}},
        'custom_fields': {'b': body},
        'styles': {
            'card': [{'padding': '0'}, {'height': height}, {'border-radius': radius}, {'background': bg_js(rj)},
                     {'box-shadow': ring_js(r['name'])}, {'border': 'none'}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }

FLOOR = 'climate.corridor_warm_floor_thermostat'
FLOOR_SCHED = 'schedule.corridor_warm_floor'

def corridor_tile():
    body = "[[[" + COMMON_JS + r"""
const fl = S('""" + FLOOR + r"""'), sc = S('""" + FLOOR_SCHED + r"""');
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}) : '';
let l = 'Коридор · тепла підлога вимк', r = '';
if (fl && fl.state === 'unavailable') { l = 'Коридор · тепла підлога недоступна'; }
else if (fl && fl.state === 'heat' && fl.attributes.temperature >= 25) { l = 'Коридор · тепла підлога гріє'; r = Math.round(fl.attributes.current_temperature) + ' → ' + fl.attributes.temperature + '°' + (sc && sc.state === 'on' && sc.attributes.next_event ? ' до ' + hhmm(sc.attributes.next_event) : ''); }
else if (fl && fl.state === 'heat') { l = 'Коридор · тепла підлога чекає'; r = sc && sc.attributes.next_event ? 'з ' + hhmm(sc.attributes.next_event) : ''; }
const on = r && l.includes('гріє');
return `<div style="display:flex;align-items:center;justify-content:space-between;height:100%;padding:0 10px;font-size:11px;color:${on ? '#FFD9A0' : 'var(--secondary-text-color)'}"><span>${l}</span><span>${r}</span></div>`;
]]]"""
    return {
        'type': 'custom:button-card', 'entity': FLOOR,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [FLOOR, FLOOR_SCHED],
        'tap_action': {'action': 'more-info'},
        'custom_fields': {'b': body},
        'styles': {
            'card': [{'padding': '0'}, {'height': '30px'}, {'border-radius': '4px'}, {'border': 'none'},
                     {'background': "[[[ const f = states['" + FLOOR + "']; return f && f.state === 'heat' && f.attributes.temperature >= 25 ? 'rgba(255,159,10,.18)' : 'rgba(127,127,127,.10)'; ]]]"}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }

HDR_JS = "[[[" + COMMON_JS + r"""
const w = S('weather.home');
const out = w && w.attributes.temperature !== undefined ? f1(w.attributes.temperature) + '°' : '—';
const season = S('input_boolean.heating_season') && S('input_boolean.heating_season').state === 'on';
const ns = S('schedule.night_schedule_thermostat');
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}) : '';
const hr = new Date().getHours();
let phase, next = '';
if (!season) { phase = 'опалення поза сезоном'; }
else if (ns && ns.state === 'on') { phase = 'ніч 20°'; next = 'до ' + hhmm(ns.attributes.next_event); }
else if (hr >= 18) { phase = 'вечір до ' + hhmm(ns && ns.attributes.next_event); next = 'далі ніч 20°'; }
else { phase = 'день 20°'; next = 'вечір з 18:00'; }
return `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;color:var(--secondary-text-color);padding:0 2px 6px;text-align:left">
<span style="display:inline-flex;align-items:center;gap:4px;color:var(--primary-text-color);font-weight:600"><ha-icon icon="mdi:weather-partly-cloudy" style="--mdc-icon-size:17px"></ha-icon>${out}</span>
<span>${phase}</span>${next ? `<span style="display:inline-flex;align-items:center;gap:4px"><ha-icon icon="mdi:weather-night" style="--mdc-icon-size:15px"></ha-icon>${next}</span>` : ''}</div>`;
]]]"""

WARN_JS = "[[[" + COMMON_JS + r"""
const ROOMS = [{"name": "Спальня", "heat": "climate.my_thermostat", "ac": "climate.bedroom_ac", "win": "binary_sensor.0xa4c13858a269b68c_contact"}, {"name": "Дитяча", "heat": "climate.childroom", "ac": "climate.childroom_ac", "win": "binary_sensor.childroom_window_sensor_contact"}, {"name": "Вітальня", "heat": null, "ac": "climate.livingroom_ac", "win": null}];
const warn = [];
for (const r of ROOMS) {
  const h = S(r.heat), a = S(r.ac);
  if (h && h.state === 'heat' && a && ['cool', 'heat_cool', 'dry'].includes(a.state)) warn.push(r.name + ': кондиціонер у режимі ' + (acLbl[a.state] || a.state) + ', поки працює опалення');
  if (r.win && S(r.win) && S(r.win).state === 'on' && ((h && h.attributes.hvac_action === 'heating') || !off(a))) warn.push(r.name + ': вікно відчинене');
}
const dead = [["climate.corridor_warm_floor_thermostat", "тепла підлога"], ["switch.towel_warmer_plug", "рушникосушарка"], ["climate.my_thermostat", "опалення спальні"], ["climate.childroom", "опалення дитячої"], ["binary_sensor.bathroom_presence_sensor_occupancy", "датчик присутності ванни"]].filter(x => S(x[0]) && S(x[0]).state === 'unavailable').map(x => x[1]);
if (dead.length) warn.push('Недоступно: ' + dead.join(', '));
if (!warn.length) return '';
return `<div style="display:flex;flex-direction:column;gap:4px;margin-top:6px;background:rgba(255,159,10,.14);color:#FF9F0A;border-radius:10px;padding:7px 10px;font-size:12px;text-align:left">${warn.map(x => `<div style="display:flex;align-items:center;gap:6px;text-align:left"><ha-icon icon="mdi:alert-outline" style="--mdc-icon-size:15px;flex:none"></ha-icon><span style="flex:1;text-align:left;white-space:normal">${x}</span></div>`).join('')}</div>`;
]]]"""

LEG_HTML = """<div style="display:flex;gap:12px;font-size:11px;color:var(--secondary-text-color);padding-top:6px;text-align:left;align-items:center">
<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:3px;background:rgba(255,159,10,.5)"></span>гріє</span>
<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:3px;background:rgba(100,210,255,.35)"></span>вологість</span>
<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:3px;background:rgba(10,132,255,.45)"></span>кондиціонер</span>
<span style="margin-left:auto">натисни кімнату</span></div>"""

KIT_HTML = """<div style="display:flex;flex-direction:column;justify-content:space-between;height:100%;box-sizing:border-box;padding:8px 10px;border-radius:4px;background:rgba(127,127,127,.08);text-align:left">
<span style="font-size:12px;color:var(--secondary-text-color)">Кухня</span><span style="font-size:11px;color:var(--secondary-text-color);opacity:.7">без датчика</span></div>"""

ALL_ENTS = sorted(set(sum([ents(r) for r in ROOMS.values()], [])) | {
    'weather.home', 'input_boolean.heating_season', 'schedule.night_schedule_thermostat', FLOOR, FLOOR_SCHED,
    'switch.towel_warmer_plug', 'binary_sensor.bathroom_presence_sensor_occupancy', SEL})

def plan_card():
    areas = ' '.join([
        '"hdr hdr hdr hdr hdr hdr"', '"bed bed bed ch ch ch"', '"bed bed bed ch ch ch"', '"cor cor cor cor cor cor"',
        '"liv liv liv liv kit kit"', '"liv liv liv liv bath bath"', '"wr wr wr wr wr wr"', '"leg leg leg leg leg leg"'])
    return {
        'type': 'custom:button-card',
        'variables': {'climate_plan': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ALL_ENTS,
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'custom_fields': {
            'hdr': HDR_JS,
            'bed': {'card': room_tile('bedroom', '12px 4px 4px 4px', '152px')},
            'ch': {'card': room_tile('childroom', '4px 12px 4px 4px', '152px')},
            'cor': {'card': corridor_tile()},
            'liv': {'card': room_tile('livingroom', '4px 4px 4px 12px', '132px')},
            'kit': KIT_HTML,
            'bath': {'card': room_tile('bathroom', '4px 4px 12px 4px', '64px', compact=True)},
            'wr': WARN_JS,
            'leg': LEG_HTML,
        },
        'styles': {
            'card': [{'padding': '12px'}, {'border-radius': '16px'}],
            'grid': [{'grid-template-areas': areas}, {'grid-template-columns': 'repeat(6, minmax(0, 1fr))'},
                     {'grid-template-rows': 'auto 74px 74px 30px 64px 64px auto auto'}, {'gap': '4px'}],
            'custom_fields': {k: FILL for k in ['hdr', 'bed', 'ch', 'cor', 'liv', 'kit', 'bath', 'wr', 'leg']},
        },
    }

# ---------- control section ----------

def small_btn(label_html, tap, bg_js_or_val, extra_ents, height='44px', color='var(--primary-text-color)', entity=None, radius='10px', align='center'):
    c = {
        'type': 'custom:button-card',
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': extra_ents,
        'tap_action': tap,
        'custom_fields': {'b': label_html},
        'styles': {
            'card': [{'padding': '0'}, {'height': height}, {'border-radius': radius}, {'background': bg_js_or_val},
                     {'border': 'none'}, {'box-shadow': 'none'}, {'color': color}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }
    if entity:
        c['entity'] = entity
    return c

def tab_btn(name):
    sel_bg = "[[[ return states['" + SEL + "'] && states['" + SEL + "'].state === '" + name + "' ? 'rgba(127,127,127,.35)' : 'transparent'; ]]]"
    sel_col = "[[[ return states['" + SEL + "'] && states['" + SEL + "'].state === '" + name + "' ? 'var(--primary-text-color)' : 'var(--secondary-text-color)'; ]]]"
    b = small_btn(f'<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:13px;font-weight:600">{name}</div>',
                  {'action': 'perform-action', 'perform_action': 'input_select.select_option', 'target': {'entity_id': SEL}, 'data': {'option': name}},
                  sel_bg, [SEL], height='36px', radius='9px')
    b['styles']['card'].append({'color': sel_col})
    return b

def tabs_card():
    names = [r['name'] for r in ROOMS.values()]
    return {
        'type': 'custom:button-card',
        'variables': {'climate_room_tabs': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [SEL],
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'custom_fields': {f't{i}': {'card': tab_btn(n)} for i, n in enumerate(names)},
        'styles': {
            'card': [{'padding': '4px'}, {'border-radius': '12px'}],
            'grid': [{'grid-template-areas': '"t0 t1 t2 t3"'}, {'grid-template-columns': 'repeat(4, minmax(0, 1fr))'}, {'gap': '4px'}],
            'custom_fields': {f't{i}': FILL for i in range(4)},
        },
    }

def dial_js(main, kind, cur_entity):
    # kind heat: climate target; hum: humidifier target
    return "[[[" + COMMON_JS + r"""
const m = S('""" + main + r"""');
const kind = '""" + kind + r"""';
const cur = num('""" + cur_entity + r"""');
const tgt = m ? (kind === 'heat' ? m.attributes.temperature : m.attributes.humidity) : null;
const lo = kind === 'heat' ? 15 : 30, hi = kind === 'heat' ? 30 : 80;
const fr = (v) => v === null || v === undefined ? 0 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
const C = 2 * Math.PI * 58, A = C * 0.75;
let col = '#8E8E93';
if (kind === 'heat' && m && m.attributes.hvac_action === 'heating') col = '#FF9F0A';
else if (kind === 'heat' && m && m.state === 'heat') col = '#FFB340';
else if (kind === 'hum' && m && m.state === 'on' && ['humidifying', 'drying'].includes(m.attributes.action)) col = '#64D2FF';
else if (kind === 'hum' && m && m.state === 'on') col = '#5AA9C8';
const ang = (135 + fr(cur) * 270) * Math.PI / 180;
const dx = 75 + 58 * Math.cos(ang), dy = 75 + 58 * Math.sin(ang);
const lbl = kind === 'heat' ? 'ціль' : (m && m.attributes.device_class === 'dehumidifier' ? 'осушення до' : 'ціль');
const tt = tgt === null || tgt === undefined ? '—' : (kind === 'heat' ? f1(tgt) + '°' : Math.round(tgt) + ' %');
const ct = cur === null ? '—' : (kind === 'heat' ? f1(cur) + '°' : Math.round(cur) + ' %');
const isOff = !m || m.state === 'off';
return `<div style="position:relative;width:150px;height:140px">
<svg width="150" height="140" viewBox="0 0 150 140"><circle cx="75" cy="75" r="58" fill="none" stroke="rgba(127,127,127,.22)" stroke-width="10" stroke-linecap="round" stroke-dasharray="${A.toFixed(1)} ${C.toFixed(1)}" transform="rotate(135 75 75)"/>
${isOff ? '' : `<circle cx="75" cy="75" r="58" fill="none" stroke="${col}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${(A * fr(tgt)).toFixed(1)} ${C.toFixed(1)}" transform="rotate(135 75 75)"/>`}
${cur === null ? '' : `<circle cx="${dx.toFixed(1)}" cy="${dy.toFixed(1)}" r="5" fill="var(--primary-text-color)" stroke="var(--ha-card-background, #1c1c1e)" stroke-width="2"/>`}</svg>
<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding-top:8px">
<div style="font-size:11px;color:var(--secondary-text-color)">${lbl}</div>
<div style="font-size:32px;font-weight:600;line-height:1.05;${isOff ? 'opacity:.4' : ''}">${tt}</div>
<div style="font-size:12px;color:var(--secondary-text-color)">зараз ${ct}</div></div></div>`;
]]]"""

def info_js(key):
    r = ROOMS[key]
    main = r['heat'] or r['hum']
    kind = 'heat' if r['heat'] else 'hum'
    return "[[[" + COMMON_JS + r"""
const m = S('""" + main + r"""');
const kind = '""" + kind + r"""';
const cur = num('""" + (r['t'] if kind == 'heat' else r['h']) + r"""');
const ac = S('""" + (r['ac'] or '') + r"""');
let st = '', col = 'var(--secondary-text-color)', sub = '';
if (!m || m.state === 'unavailable') { st = 'Недоступно'; col = '#FF453A'; }
else if (kind === 'heat') {
  const t = m.attributes.temperature;
  if (m.state === 'off') st = 'Опалення вимкнене';
  else if (m.attributes.hvac_action === 'heating') { st = 'Гріє' + (cur !== null && t > cur ? ', ще ' + f1(t - cur) + '° до цілі' : ''); col = '#FF9F0A'; }
  else st = 'Тримає ціль';
  const hv = num('""" + r['h'] + r"""');
  sub = hv === null ? '' : 'вологість ' + Math.round(hv) + ' %';
} else {
  const a = m.attributes.action;
  if (m.state === 'off') st = 'Вимкнено';
  else if (a === 'humidifying') { st = 'Зволожує'; col = '#64D2FF'; }
  else if (a === 'drying') { st = 'Осушує'; col = '#64D2FF'; }
  else st = 'Чекає';
  sub = m.attributes.device_class === 'dehumidifier' ? 'вмикає витяжку вище цілі' : 'тримає вологість';
}
if (ac && !off(ac)) sub = 'кондиціонер: ' + (acLbl[ac.state] || ac.state) + ' ' + ac.attributes.temperature + '°';
return `<div style="display:flex;flex-direction:column;gap:4px;text-align:left;padding-top:10px">
<div style="font-size:17px;font-weight:600">""" + r['name'] + r"""</div>
<div style="font-size:13px;color:${col};white-space:normal">${st}</div>
<div style="font-size:12px;color:var(--secondary-text-color);white-space:normal">${sub}</div></div>`;
]]]"""

BTN_BG = 'rgba(127,127,127,.18)'

def step_btn(main, kind, delta):
    label = '−' if delta < 0 else '+'
    if kind == 'heat':
        tap = {'action': 'perform-action', 'perform_action': 'climate.set_temperature', 'target': {'entity_id': main},
               'data': {'temperature': "[[[ const t = states['" + main + "'].attributes.temperature; return Math.min(30, Math.max(5, t " + ('-' if delta < 0 else '+') + " 0.5)); ]]]"}}
    else:
        tap = {'action': 'perform-action', 'perform_action': 'humidifier.set_humidity', 'target': {'entity_id': main},
               'data': {'humidity': "[[[ const t = states['" + main + "'].attributes.humidity; return Math.min(80, Math.max(30, t " + ('-' if delta < 0 else '+') + " 5)); ]]]"}}
    return small_btn(f'<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:24px;font-weight:400">{label}</div>',
                     tap, BTN_BG, [main], height='44px', radius='12px')

def preset_btn(label, tap, active_js, ents_):
    bg = "[[[ return (" + active_js + ") ? '#FF9F0A' : 'rgba(127,127,127,.18)'; ]]]"
    col = "[[[ return (" + active_js + ") ? '#1C1300' : 'var(--primary-text-color)'; ]]]"
    b = small_btn(f'<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:13px;font-weight:600">{label}</div>',
                  tap, bg, ents_, height='44px')
    b['styles']['card'].append({'color': col})
    return b

def presets_card(key):
    r = ROOMS[key]
    if r['heat']:
        e = r['heat']
        pm = "states['" + e + "'] && states['" + e + "'].attributes.preset_mode === '%s'"
        def tap(p):
            return {'action': 'perform-action', 'perform_action': 'climate.set_preset_mode', 'target': {'entity_id': e}, 'data': {'preset_mode': p}}
        cards = [preset_btn('Ніч 20°', tap('home'), pm % 'home', [e]),
                 preset_btn('Тепло', tap('none'), pm % 'none', [e]),
                 preset_btn('Нема вдома', tap('away'), pm % 'away', [e])]
    else:
        e = r['hum']
        on = "states['" + e + "'] && states['" + e + "'].state === 'on'"
        cards = [preset_btn('Вимкнути', {'action': 'perform-action', 'perform_action': 'humidifier.turn_off', 'target': {'entity_id': e}}, '!(' + on + ')', [e]),
                 preset_btn('Увімкнути', {'action': 'perform-action', 'perform_action': 'humidifier.turn_on', 'target': {'entity_id': e}}, on, [e])]
    return {'type': 'grid', 'columns': len(cards), 'square': False, 'cards': cards}

def device_row(name, entity, state_js, tap, action_label):
    body = "[[[" + COMMON_JS + "const s = S('" + entity + "');\n" + state_js + r"""
return `<div style="display:flex;align-items:center;gap:8px;height:100%;padding:0 2px;text-align:left">
<span style="flex:1;font-size:13px;text-align:left">""" + name + r"""</span>
<span style="font-size:12px;color:var(--secondary-text-color)">${v}</span>
<span style="font-size:12px;font-weight:600;color:#0A84FF;padding-left:6px">""" + action_label + r"""</span></div>`;
]]]"""
    c = small_btn(body, tap, 'transparent', [entity], height='40px', radius='0')
    c['entity'] = entity
    c['styles']['card'].append({'border-top': '1px solid rgba(127,127,127,.18)'})
    return c

AC_STATE = "const v = !s ? '—' : (s.state === 'off' ? 'вимкнено' : (acLbl[s.state] || s.state) + ' ' + s.attributes.temperature + '°');"
WIN_STATE = "const v = !s ? '—' : (s.state === 'on' ? 'відчинене' : 'зачинене');"
ONOFF_STATE = "const v = !s ? '—' : (s.state === 'on' ? 'увімкнено' : (s.state === 'off' ? 'вимкнено' : 'недоступно'));"
PERSON_STATE = "const v = !s ? '—' : (s.state === 'home' ? 'вдома' : 'не вдома');"
TEMP_STATE = "const v = !s ? '—' : f1(parseFloat(s.state)) + '°';"
FLOOR_STATE = "const v = !s ? '—' : (s.state === 'unavailable' ? 'недоступна' : s.state === 'off' ? 'вимкнено' : (s.attributes.temperature >= 25 ? 'гріє до ' + s.attributes.temperature + '°' : 'чекає, ' + s.attributes.temperature + '°'));"
MORE = {'action': 'more-info'}
TOGGLE = {'action': 'toggle'}

DEVICES = {
    'bedroom': [('Кондиціонер', 'climate.bedroom_ac', AC_STATE, MORE, 'Режим ›'),
                ('Вікно', 'binary_sensor.0xa4c13858a269b68c_contact', WIN_STATE, MORE, 'Історія ›')],
    'childroom': [('Кондиціонер', 'climate.childroom_ac', AC_STATE, MORE, 'Режим ›'),
                  ('Вікно', 'binary_sensor.childroom_window_sensor_contact', WIN_STATE, MORE, 'Історія ›'),
                  ('Маша', 'person.mariia', PERSON_STATE, MORE, 'Де ›')],
    'livingroom': [('Кондиціонер', 'climate.livingroom_ac', AC_STATE, MORE, 'Режим ›')],
    'bathroom': [('Витяжка', 'switch.bathroom_exhaust_fan', ONOFF_STATE, TOGGLE, 'Перемкнути'),
                 ('Рушникосушарка', 'switch.towel_warmer_plug', ONOFF_STATE, TOGGLE, 'Перемкнути'),
                 ('Тепла підлога', FLOOR, FLOOR_STATE, MORE, 'Розклад ›')],
}

def panel_card(key):
    r = ROOMS[key]
    main = r['heat'] or r['hum']
    kind = 'heat' if r['heat'] else 'hum'
    cur = r['t'] if kind == 'heat' else r['h']
    devs = DEVICES[key]
    trig = sorted(set(ents(r) + [d[1] for d in devs] + [main]))
    return {
        'type': 'custom:button-card',
        'variables': {'climate_room_panel': key},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': trig,
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'visibility': [{'condition': 'state', 'entity': SEL, 'state': r['name']}],
        'custom_fields': {
            'dial': dial_js(main, kind, cur),
            'info': info_js(key),
            'mn': {'card': step_btn(main, kind, -1)},
            'pl': {'card': step_btn(main, kind, +1)},
            'pre': {'card': presets_card(key)},
            'dev': {'card': {'type': 'grid', 'columns': 2, 'square': False, 'cards': [device_row(*d) for d in devs]}},
        },
        'styles': {
            'card': [{'padding': '12px 14px'}, {'border-radius': '16px'}],
            'grid': [{'grid-template-areas': '"dial info info" "dial mn pl" "pre pre pre" "dev dev dev"'},
                     {'grid-template-columns': '150px minmax(0, 1fr) minmax(0, 1fr)'},
                     {'grid-template-rows': 'auto auto auto auto'}, {'column-gap': '10px'}, {'row-gap': '10px'}],
            'custom_fields': {'dial': [{'justify-self': 'start'}, {'align-self': 'center'}],
                              'info': FILL, 'mn': FILL + [{'align-self': 'end'}], 'pl': FILL + [{'align-self': 'end'}],
                              'pre': FILL, 'dev': FILL},
        },
    }

STATS = {
    'type': 'custom:climate-stats-card',
    'variables': {'climate_stats': True},
    'grid_options': {'columns': 12},
    'rooms': [{'name': r['name'], 'temperature': r['t'], 'humidity': r['h']} for r in ROOMS.values()],
    'heating': [{'name': 'спальня', 'entity': 'climate.my_thermostat', 'color': '#FF9F0A'},
                {'name': 'дитяча', 'entity': 'climate.childroom', 'color': '#FFD08A'}],
    'outdoor': 'sensor.lun_misto_air_kyiv_104_temperature',
}
STATS.pop('variables')  # custom element ignores it; marker kept via type

def main():
    base = json.load(open(SCR + 'dash.json'))
    cfg = base.get('config', base)
    new = copy.deepcopy(base)
    ncfg = new.get('config', new)
    v = [x for x in ncfg['views'] if x.get('path') == 'climate'][0]
    secs = v['sections']
    for s in [s for s in secs if s['cards'] and s['cards'][0].get('heading') == 'Розклад' and not s.get('visibility')]:
        sec1 = next(x for x in secs if x['cards'] and x['cards'][0].get('heading') == 'Керування' and not x.get('visibility'))
        sec1['cards'] += s['cards']
        secs.remove(s)
    s0, s1, s2 = secs[0], secs[1], secs[2]
    assert s0['cards'][0].get('heading') == 'Стан' and s1['cards'][0].get('heading') == 'Керування' and s2['cards'][0].get('heading') == 'Статистика'
    isnew = lambda c: any(k in (c.get('variables') or {}) for k in ('climate_plan', 'climate_scale', 'climate_stan_heading', 'climate_room_tabs', 'climate_room_panel', 'climate_room_heading')) or c.get('type') == 'custom:climate-stats-card'
    for sec in (s0, s1, s2):
        sec['cards'] = [c for c in sec['cards'] if not isnew(c)]
    for c in s1['cards']:
        if c.get('entity') == 'input_boolean.heating_season': c['name'] = 'Сезон опалення'
        if c.get('entity') == 'schedule.night_schedule_thermostat': c['name'] = 'Нічний розклад'
    # Стан: park summary + room tiles, add plan
    for c in s0['cards'][1:]:
        assert (c.get('variables') or {}).get('climate_summary') or (c.get('variables') or {}).get('climate_room_tile')
        c['visibility'] = PARK
    s0['cards'].insert(1, plan_card())
    # Керування: park room subheadings + tiles, keep the Розклад block
    idx_sched = next(i for i, c in enumerate(s1['cards']) if c.get('heading') == 'Розклад')
    for c in s1['cards'][1:idx_sched]:
        c['visibility'] = PARK
    newc = [dict(tabs_card(), visibility=PARK)] + [panel_card(k) for k in ROOMS]
    s1['cards'][1:1] = newc
    s1['cards'][0]['visibility'] = PARK
    s1['cards'].insert(1, room_heading())
    # Статистика: park old charts, add stats card
    for c in s2['cards'][1:]:
        c['visibility'] = PARK
    s2['cards'].insert(1, STATS)
    json.dump(new, open(SCR + 'dash_new.json', 'w'), ensure_ascii=False, indent=1)
    print('ok', len(json.dumps(v)))

# Lights-tab look for the climate cards: rounded rows, icon circles, outlined pill chips, section backgrounds.
CARD_BG = 'var(--ha-card-background, var(--card-background-color))'
ICONS = {'bedroom': 'mdi:bed-king-outline', 'childroom': 'mdi:teddy-bear', 'livingroom': 'mdi:sofa-outline', 'bathroom': 'mdi:shower'}

def tile_bg_js(rjson):
    return "[[[" + COMMON_JS + "const R = " + rjson + ";\n" + r"""
const heat = S(R.heat), ac = S(R.ac), hum = S(R.hum);
const cur = num(R.t), hh = num(R.h);
const bg = '""" + CARD_BG + r"""';
const bar = (rgb, p) => 'linear-gradient(90deg, rgba(' + rgb + ',0.35) 0%, rgba(' + rgb + ',0.35) ' + p + '%, transparent ' + p + '%), ' + bg;
if (!off(ac)) return bar('10,132,255', 100);
if (heat && heat.attributes.hvac_action === 'heating') return 'linear-gradient(rgba(255,159,10,0.14), rgba(255,159,10,0.14)), ' + bg;
if (hum && hum.state === 'on' && ['humidifying', 'drying'].includes(hum.attributes.action)) return bar('100,210,255', hh === null ? 50 : Math.round(hh));
return bg;
]]]"""

def icon_circle_js(rjson, icon, size):
    return r"""
const active = kind === 'heat' ? ['#FF9F0A', '#1c1c1e'] : (kind === 'ac' ? ['#0A84FF', '#fff'] : (kind === 'hum' ? ['#64D2FF', '#1c1c1e'] : ['rgba(127,127,127,0.18)', '#8E8E93']));
const ic = `<div style="width:""" + str(size) + "px;height:" + str(size) + r"""px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:${active[0]}"><ha-icon icon='""" + icon + r"""' style="--mdc-icon-size:""" + str(int(size * 0.55)) + r"""px;color:${active[1]}"></ha-icon></div>`;
"""

def room_tile(key, radius, height, compact=False):
    r = ROOMS[key]
    rj = json.dumps(r, ensure_ascii=False)
    icon = ICONS[key]
    if compact:
        body = "[[[" + status_js(rj) + icon_circle_js(rj, icon, 30) + r"""
return `<div style="display:flex;align-items:center;gap:10px;height:100%;box-sizing:border-box;padding:0 12px;text-align:left">${ic}
<div style="display:flex;flex-direction:column;min-width:0;text-align:left"><span style="font-size:13px;font-weight:500;color:var(--primary-text-color)">${R.name}</span>
<span style="font-size:12px;color:var(--secondary-text-color)">${f1(num(R.t))}° · ${hs}</span></div></div>`;
]]]"""
    else:
        body = "[[[" + status_js(rj) + icon_circle_js(rj, icon, 36) + r"""
const selected = states['""" + SEL + r"""'] && states['""" + SEL + r"""'].state === R.name;
const cur0 = num(R.t);
let pct = null, bc = '#8E8E93', delta = '', dc = 'var(--secondary-text-color)';
if (heat && heat.state === 'heat' && heat.attributes.temperature && cur0 !== null) {
  const d = cur0 - heat.attributes.temperature;
  if (heat.attributes.hvac_action === 'heating') {
    pct = Math.round(Math.max(4, Math.min(100, (cur0 - 18) / (heat.attributes.temperature - 18) * 100)));
    bc = '#FF9F0A';
  }
  if (Math.abs(d) < 0.3) delta = 'у цілі';
  else if (d > 0) delta = '+' + f1(d) + '°';
  else { delta = '−' + f1(-d) + '°'; dc = '#FF9F0A'; }
} else if (hum && hum.state === 'on' && hum.attributes.humidity && h !== null && ['humidifying', 'drying'].includes(hum.attributes.action)) {
  pct = Math.round(Math.max(4, Math.min(100, h / hum.attributes.humidity * 100)));
  bc = '#64D2FF';
}
const bar = pct === null ? '<div style="height:4px"></div>' : `<div style="height:4px;border-radius:2px;background:rgba(127,127,127,.22);overflow:hidden"><div style="height:100%;width:${pct}%;background:${bc};border-radius:2px"></div></div>`;
const dot = selected ? '<span style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.85)"></span>' : '';
return `<div style="display:flex;flex-direction:column;justify-content:space-between;height:100%;box-sizing:border-box;padding:12px;text-align:left">
<div style="display:flex;align-items:center;gap:10px">${ic}<span style="font-size:14px;font-weight:500;color:var(--primary-text-color)">${R.name}</span>${dot}</div>
<div style="display:flex;align-items:baseline;gap:8px"><span style="font-size:28px;font-weight:500;line-height:1;color:var(--primary-text-color)">${f1(num(R.t))}°</span>${delta ? `<span style="font-size:12px;color:${dc}">${delta}</span>` : ''}</div>
<div style="font-size:12px;line-height:1.3;color:var(--secondary-text-color);white-space:normal;text-align:left">${txt}</div>${bar}</div>`;
]]]"""
    return {
        'type': 'custom:button-card',
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ents(r) + [SEL],
        'tap_action': {'action': 'perform-action', 'perform_action': 'input_select.select_option',
                       'target': {'entity_id': SEL}, 'data': {'option': r['name']}, 'haptic': 'light'},
        'hold_action': {'action': 'more-info', 'entity': r['heat'] or r['hum'] or r['t']},
        'custom_fields': {'b': body},
        'styles': {
            'card': [{'padding': '0'}, {'height': height}, {'border-radius': '16px'}, {'background': tile_bg_js(rj)},
                     {'box-shadow': ring_js(r['name'])}, {'border': 'none'}, {'overflow': 'hidden'}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }

def ring_js(name):
    return "[[[ return states['" + SEL + "'] && states['" + SEL + "'].state === '" + name + "' ? 'inset 0 0 0 1.5px rgba(255,255,255,0.55)' : 'none'; ]]]"

def corridor_tile():
    body = "[[[" + COMMON_JS + r"""
const fl = S('""" + FLOOR + r"""'), sc = S('""" + FLOOR_SCHED + r"""');
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}) : '';
let st = 'вимкнено', on = false;
if (fl && fl.state === 'unavailable') st = '<span style="color:#FF9F0A">недоступна · немає живлення</span>';
else if (fl && fl.state === 'heat' && fl.attributes.temperature >= 25) { on = true; st = Math.round(fl.attributes.current_temperature) + ' → ' + fl.attributes.temperature + '°' + (sc && sc.state === 'on' && sc.attributes.next_event ? ' · до ' + hhmm(sc.attributes.next_event) : ''); }
else if (fl && fl.state === 'heat') st = 'чекає' + (sc && sc.attributes.next_event ? ' · з ' + hhmm(sc.attributes.next_event) : '');
const ic = `<div style="width:30px;height:30px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:${on ? '#FF9F0A' : 'rgba(127,127,127,0.18)'}"><ha-icon icon="mdi:heating-coil" style="--mdc-icon-size:17px;color:${on ? '#1c1c1e' : '#8E8E93'}"></ha-icon></div>`;
return `<div style="display:flex;align-items:center;gap:10px;height:100%;padding:0 12px;text-align:left">${ic}<span style="font-size:13px;font-weight:500;color:var(--primary-text-color)">Тепла підлога</span><span style="margin-left:auto;font-size:12px;color:var(--secondary-text-color)">${st}</span></div>`;
]]]"""
    return {
        'type': 'custom:button-card', 'entity': FLOOR,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [FLOOR, FLOOR_SCHED],
        'tap_action': {'action': 'more-info'},
        'custom_fields': {'b': body},
        'styles': {
            'card': [{'padding': '0'}, {'height': '44px'}, {'border-radius': '16px'}, {'border': 'none'}, {'box-shadow': 'none'},
                     {'background': "[[[ const f = states['" + FLOOR + "']; const bg = '" + CARD_BG + "'; return f && f.state === 'heat' && f.attributes.temperature >= 25 ? 'linear-gradient(90deg, rgba(255,159,10,0.30), rgba(255,159,10,0.08)), ' + bg : bg; ]]]"}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }

CHIP = 'display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 11px;border-radius:15px;font-size:13px;box-sizing:border-box;white-space:nowrap'

HDR_JS = "[[[" + COMMON_JS + r"""
const w = S('weather.home');
const out = w && w.attributes.temperature !== undefined ? f1(w.attributes.temperature) + '°' : '—';
const season = S('input_boolean.heating_season') && S('input_boolean.heating_season').state === 'on';
const ns = S('schedule.night_schedule_thermostat');
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}) : '';
const hr = new Date().getHours();
const C = '""" + CHIP + r"""';
const chip = (icon, text, col) => `<span style="${C};border:1px solid ${col};color:${col}"><ha-icon icon="${icon}" style="--mdc-icon-size:16px"></ha-icon>${text}</span>`;
""" + DAYPLAN_JS + r"""
const tn = deg(presetT('climate.my_thermostat', 'home'));
let phase;
if (!season) phase = chip('mdi:radiator-off', 'поза сезоном', '#8E8E93');
else if (nightOn) phase = chip('mdi:weather-night', 'ніч ' + tn + ' до ' + nightEndTxt, '#8B7FD4');
else if (nowMin >= eStart) phase = chip('mdi:weather-sunset-down', 'вечір до ' + fmtMin(nStart), '#FF9F0A') + chip('mdi:weather-night', 'далі ' + tn, '#8B7FD4');
else phase = chip('mdi:white-balance-sunny', 'день', '#FFD60A') + chip('mdi:weather-sunset-down', 'вечір з ' + fmtMin(eStart), '#FF9F0A');
return `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding-bottom:4px;text-align:left">
<span style="${C};background:rgba(127,127,127,0.18);color:var(--primary-text-color);font-weight:500"><ha-icon icon="mdi:weather-partly-cloudy" style="--mdc-icon-size:16px"></ha-icon>${out}</span>${phase}</div>`;
]]]"""

KIT_HTML = """<div style="display:flex;align-items:center;gap:10px;height:100%;box-sizing:border-box;padding:0 12px;border-radius:16px;background:""" + CARD_BG + """;opacity:.5;text-align:left">
<div style="width:30px;height:30px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:rgba(127,127,127,0.18)"><ha-icon icon="mdi:countertop-outline" style="--mdc-icon-size:17px;color:#8E8E93"></ha-icon></div>
<div style="display:flex;flex-direction:column;text-align:left"><span style="font-size:13px;font-weight:500;color:var(--primary-text-color)">Кухня</span><span style="font-size:12px;color:var(--secondary-text-color)">без датчика</span></div></div>"""

SEASON = 'input_boolean.heating_season'
NIGHT_SCHED = 'schedule.night_schedule_thermostat'

def season_chip():
    on = "states['" + SEASON + "'] && states['" + SEASON + "'].state === 'on'"
    label = "[[[ return " + on + " ? 'Сезон: увімкнено' : 'Сезон: вимкнено'; ]]]"
    tap = {'action': 'perform-action', 'perform_action': 'input_boolean.toggle', 'target': {'entity_id': SEASON},
           'confirmation': {'text': 'Змінити сезон опалення?'}}
    return chip_btn(label, 'mdi:radiator', '#FF9F0A', on, tap, [SEASON], height='30px')

def night_chip():
    on = "states['" + NIGHT_SCHED + "'] && states['" + NIGHT_SCHED + "'].state === 'on'"
    tap = {'action': 'more-info', 'entity': NIGHT_SCHED}
    return chip_btn('Нічний розклад', 'mdi:calendar-clock', '#8B7FD4', on, tap, [NIGHT_SCHED], height='30px')

def corridor_hidden_when_dead():
    c = corridor_tile()
    c['styles']['card'].append({'display': "[[[ const f = states['" + FLOOR + "']; return f && f.state === 'unavailable' ? 'none' : 'block'; ]]]"})
    return c

def plan_card():
    areas = ' '.join([
        '"hdr hdr hdr hdr hdr hdr"', '"sea sea sea nsc nsc nsc"', '"bed bed bed ch ch ch"', '"cor cor cor cor cor cor"',
        '"liv liv liv bath bath bath"', '"wr wr wr wr wr wr"'])
    return {
        'type': 'custom:button-card',
        'variables': {'climate_plan': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ALL_ENTS,
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'custom_fields': {
            'hdr': HDR_JS,
            'sea': {'card': season_chip()},
            'nsc': {'card': night_chip()},
            'bed': {'card': room_tile('bedroom', '', '136px')},
            'ch': {'card': room_tile('childroom', '', '136px')},
            'cor': {'card': corridor_hidden_when_dead()},
            'liv': {'card': room_tile('livingroom', '', '136px')},
            'bath': {'card': room_tile('bathroom', '', '136px')},
            'wr': WARN_JS,
        },
        'styles': {
            'card': [{'padding': '0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}],
            'grid': [{'grid-template-areas': areas}, {'grid-template-columns': 'repeat(6, minmax(0, 1fr))'},
                     {'grid-template-rows': 'auto 30px 136px auto 136px auto'}, {'gap': '8px'}],
            'custom_fields': {k: FILL for k in ['hdr', 'sea', 'nsc', 'bed', 'ch', 'cor', 'liv', 'bath', 'wr']},
        },
    }

TAB_ICONS = {'Спальня': 'mdi:bed-king-outline', 'Дитяча': 'mdi:teddy-bear', 'Вітальня': 'mdi:sofa-outline', 'Ванна': 'mdi:shower'}

def chip_btn(label, icon, color, active_js, tap, ents_, height='34px', dark_text=True):
    a = "(" + active_js + ")"
    fg_on = '#1c1c1e' if dark_text else '#fff'
    return {
        'type': 'custom:button-card', 'name': label, 'icon': icon, 'layout': 'icon_name', 'show_state': False, 'show_icon': bool(icon),
        'triggers_update': ents_, 'tap_action': dict(tap, haptic='light'), 'hold_action': {'action': 'none'},
        'styles': {
            'card': [{'height': height}, {'padding': '0 10px'}, {'border-radius': '17px'}, {'box-shadow': 'none'},
                     {'border': '1px solid ' + color}, {'background': "[[[ return " + a + " ? '" + color + "' : 'transparent'; ]]]"}],
            'grid': [{'column-gap': '6px'}, {'grid-template-columns': 'auto auto'}, {'justify-content': 'center'}],
            'img_cell': [{'width': '16px'}, {'height': '16px'}],
            'icon': [{'width': '16px'}, {'color': "[[[ return " + a + " ? '" + fg_on + "' : '" + color + "'; ]]]"}],
            'name': [{'font-size': '13px'}, {'font-weight': '500'}, {'color': "[[[ return " + a + " ? '" + fg_on + "' : '" + color + "'; ]]]"}],
        },
    }

def tab_btn(name):
    act = "states['" + SEL + "'] && states['" + SEL + "'].state === '" + name + "'"
    return chip_btn(name, None, '#64D2FF', act,
                    {'action': 'perform-action', 'perform_action': 'input_select.select_option', 'target': {'entity_id': SEL}, 'data': {'option': name}}, [SEL])

def tabs_card():
    names = [r['name'] for r in ROOMS.values()]
    return {
        'type': 'custom:button-card',
        'variables': {'climate_room_tabs': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [SEL],
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'custom_fields': {f't{i}': {'card': tab_btn(n)} for i, n in enumerate(names)},
        'styles': {
            'card': [{'padding': '0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}],
            'grid': [{'grid-template-areas': '"t0 t1 t2 t3"'}, {'grid-template-columns': 'repeat(4, minmax(0, 1fr))'}, {'gap': '6px'}],
            'custom_fields': {f't{i}': FILL for i in range(4)},
        },
    }

def step_btn(main, kind, delta):
    icon = 'mdi:minus' if delta < 0 else 'mdi:plus'
    if kind == 'heat':
        tap = {'action': 'perform-action', 'perform_action': 'climate.set_temperature', 'target': {'entity_id': main},
               'data': {'temperature': "[[[ const t = states['" + main + "'].attributes.temperature; return Math.min(30, Math.max(5, t " + ('-' if delta < 0 else '+') + " 0.5)); ]]]"}}
    else:
        tap = {'action': 'perform-action', 'perform_action': 'humidifier.set_humidity', 'target': {'entity_id': main},
               'data': {'humidity': "[[[ const t = states['" + main + "'].attributes.humidity; return Math.min(80, Math.max(30, t " + ('-' if delta < 0 else '+') + " 5)); ]]]"}}
    return {
        'type': 'custom:button-card', 'icon': icon, 'show_name': False, 'show_state': False,
        'triggers_update': [main], 'tap_action': dict(tap, haptic='light'),
        'styles': {'card': [{'width': '48px'}, {'height': '48px'}, {'border-radius': '50%'}, {'padding': '0'}, {'box-shadow': 'none'},
                            {'background': 'rgba(127,127,127,0.18)'}],
                   'icon': [{'width': '22px'}, {'color': 'var(--primary-text-color)'}]},
    }

def presets_card(key):
    r = ROOMS[key]
    if r['heat']:
        e = r['heat']
        pm = "states['" + e + "'] && states['" + e + "'].attributes.preset_mode === '%s'"
        def tap(p):
            return {'action': 'perform-action', 'perform_action': 'climate.set_preset_mode', 'target': {'entity_id': e}, 'data': {'preset_mode': p}}
        def lbl(text, p):
            return ("[[[ const s = states['" + e + "']; let v; try { v = JSON.parse((s && s.attributes.bt_preset_heat_temperatures) || '{}')['" + p +
                    "']; } catch (x) {} return '" + text + "' + (v === undefined ? '' : ' ' + String(v).replace('.', ',') + '°'); ]]]")
        cards = [chip_btn(lbl('Ніч', 'home'), 'mdi:weather-night', '#8B7FD4', pm % 'home', tap('home'), [e]),
                 chip_btn(lbl('Тепло', 'none'), 'mdi:fire', '#FF9F0A', pm % 'none', tap('none'), [e]),
                 chip_btn(lbl('Не вдома', 'away'), 'mdi:home-export-outline', '#8E8E93', pm % 'away', tap('away'), [e])]
    else:
        e = r['hum']
        on = "states['" + e + "'] && states['" + e + "'].state === 'on'"
        label = "[[[ return " + on + " ? 'Вимкнути' : 'Увімкнути'; ]]]"
        cards = [chip_btn(label, 'mdi:power', '#64D2FF', on, {'action': 'perform-action', 'perform_action': 'humidifier.toggle', 'target': {'entity_id': e}}, [e])]
    n = len(cards)
    return {
        'type': 'custom:button-card', 'show_name': False, 'show_icon': False, 'show_state': False, 'tap_action': {'action': 'none'},
        'custom_fields': {f'p{i}': {'card': c} for i, c in enumerate(cards)},
        'styles': {'card': [{'padding': '0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}],
                   'grid': [{'grid-template-areas': '"' + ' '.join(f'p{i}' for i in range(n)) + '"'},
                            {'grid-template-columns': f'repeat({n}, minmax(0, 1fr))'}, {'gap': '6px'}],
                   'custom_fields': {f'p{i}': FILL for i in range(n)}},
    }

DEV_ICON = {
    'climate.bedroom_ac': ('mdi:air-conditioner', "s && !off(s)", '#0A84FF', '#fff'),
    'climate.childroom_ac': ('mdi:air-conditioner', "s && !off(s)", '#0A84FF', '#fff'),
    'climate.livingroom_ac': ('mdi:air-conditioner', "s && !off(s)", '#0A84FF', '#fff'),
    'binary_sensor.0xa4c13858a269b68c_contact': ("${s && s.state === 'on' ? 'mdi:window-open-variant' : 'mdi:window-closed-variant'}", "s && s.state === 'on'", '#FF9F0A', '#1c1c1e'),
    'binary_sensor.childroom_window_sensor_contact': ("${s && s.state === 'on' ? 'mdi:window-open-variant' : 'mdi:window-closed-variant'}", "s && s.state === 'on'", '#FF9F0A', '#1c1c1e'),
    'person.mariia': ('mdi:account', "s && s.state === 'home'", '#32D74B', '#1c1c1e'),
    'sensor.livingroom_temperature_sensor_temperature': ('mdi:thermometer', 'false', '', ''),
    'sensor.bathroom_temperature_sensor_temperature_2': ('mdi:thermometer', 'false', '', ''),
    'switch.bathroom_exhaust_fan': ('mdi:fan', "s && s.state === 'on'", '#64D2FF', '#1c1c1e'),
    'switch.towel_warmer_plug': ('mdi:radiator', "s && s.state === 'on'", '#FF9F0A', '#1c1c1e'),
    FLOOR: ('mdi:heating-coil', "s && s.state === 'heat' && s.attributes.temperature >= 25", '#FF9F0A', '#1c1c1e'),
}

def device_row(name, entity, state_js, tap, action_label):
    icon, act, c_on, fg_on = DEV_ICON[entity]
    body = "[[[" + COMMON_JS + "const s = S('" + entity + "');\n" + state_js + "\nconst on = " + act + ";\n" + r"""
return `<div style="display:flex;align-items:center;gap:10px;height:100%;text-align:left">
<div style="width:32px;height:32px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:${on ? '""" + c_on + r"""' : 'rgba(127,127,127,0.18)'}"><ha-icon icon="__ICON__" style="--mdc-icon-size:18px;color:${on ? '""" + fg_on + r"""' : '#8E8E93'}"></ha-icon></div>
<div style="display:flex;flex-direction:column;flex:1;min-width:0;text-align:left"><span style="font-size:13px;font-weight:500;color:var(--primary-text-color)">""" + name + r"""</span><span style="font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${v}</span></div></div>`;
]]]"""
    body = body.replace('__ICON__', icon)
    card = {
        'type': 'custom:button-card', 'entity': entity,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [entity],
        'tap_action': dict(tap, haptic='light'), 'hold_action': {'action': 'more-info'},
        'custom_fields': {'b': body},
        'styles': {'card': [{'padding': '8px 10px'}, {'height': '52px'}, {'border-radius': '16px'}, {'box-shadow': 'none'}, {'border': 'none'},
                            {'background': CARD_BG}],
                   'grid': single(), 'custom_fields': {'b': FILL}},
    }
    if entity.endswith('_contact'):
        card['visibility'] = [{'condition': 'state', 'entity': entity, 'state': 'on'}]
    elif entity in (FLOOR, 'switch.towel_warmer_plug'):
        card['visibility'] = [{'condition': 'state', 'entity': entity, 'state_not': 'unavailable'}]
    return card

def room_heading():
    body = ("[[[ const s = states['" + SEL + "']; const r = s ? s.state : ''; "
            "return `<div style=\"display:block;width:100%;padding:0 4px;box-sizing:border-box;text-align:left;white-space:nowrap\">"
            "<ha-icon icon=\"mdi:tune-variant\" style=\"--mdc-icon-size:20px;color:var(--secondary-text-color);vertical-align:middle\"></ha-icon>"
            "<span style=\"margin-left:8px;vertical-align:middle;font-size:16px;font-weight:500;color:var(--primary-text-color)\">Керування${r ? ' · ' + r : ''}</span></div>`; ]]]")
    return {
        'type': 'custom:button-card', 'variables': {'climate_room_heading': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [SEL], 'tap_action': {'action': 'none'},
        'custom_fields': {'b': body},
        'styles': {'card': [{'padding': '0'}, {'height': '28px'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}],
                   'grid': single(), 'custom_fields': {'b': FILL}},
    }

PLAN_ENTS = ['schedule.night_schedule_thermostat', 'sensor.time_of_day', 'sun.sun', 'person.mariia']

def strip_js(key):
    # Today's heating plan for one room as a 24 h bar: night / day / evening with target temperatures, now marker.
    e = ROOMS[key]['heat']
    return "[[[" + COMMON_JS + DAYPLAN_JS + r"""
const e = '""" + e + r"""', child = """ + ('true' if key == 'childroom' else 'false') + r""";
const tN = presetT(e, 'home'), tW = presetT(e, 'none'), tA = presetT(e, 'away');
const masha = states['person.mariia'], away = child && masha && masha.state !== 'home';
const NIGHT = ['rgba(139,127,212,', '#C4BCEE'], DAY = ['rgba(255,214,10,', '#FFE58A'], EVE = ['rgba(255,159,10,', '#FFC266'];
const segs = child
  ? [[0, mEnd, NIGHT, 'ніч', tN], [mEnd, nStart, away ? DAY : EVE, away ? 'нема вдома' : 'день', away ? tA : tW], [nStart, 1440, NIGHT, 'ніч', tN]]
  : [[0, mEnd, NIGHT, 'ніч', tN], [mEnd, eStart, DAY, 'день', tN], [eStart, nStart, EVE, 'вечір', tW], [nStart, 1440, NIGHT, 'ніч', tN]];
const bar = segs.map(([a, b, c, name, t]) => {
  const w = (b - a) / 14.4, cur = nowMin >= a && nowMin < b;
  const txt = w >= 22 ? name + ' ' + deg(t) : (w >= 10 ? deg(t) : '');
  return `<div style="flex:${b - a} 1 0;min-width:0;display:flex;align-items:center;justify-content:center;background:${c[0]}${cur ? '.55' : '.22'});color:${cur ? 'var(--primary-text-color)' : c[1]};font-size:12px;font-weight:${cur ? 600 : 500};white-space:nowrap;overflow:hidden">${txt}</div>`;
}).join('');
const ticks = segs.slice(1).filter(([a], i, arr) => i === 0 || (a - arr[i - 1][0]) / 14.4 >= 9).map(([a]) => `<span style="position:absolute;left:${(a / 14.4).toFixed(2)}%;transform:translateX(-50%)">${fmtMin(a)}</span>`).join('');
const note = child ? (away ? 'Марії немає вдома, тому вдень ' + deg(tA) : 'вдень ' + deg(tW) + ', коли Марії немає ' + deg(tA)) : 'вечір починається із заходом сонця';
return `<div style="display:flex;flex-direction:column;gap:6px;text-align:left">
<div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--secondary-text-color)"><span style="font-weight:500;color:var(--primary-text-color)">Сьогодні</span><span style="text-align:right">${note}</span></div>
<div style="position:relative;display:flex;gap:2px;height:30px;border-radius:10px;overflow:hidden">${bar}
<div style="position:absolute;left:${(nowMin / 14.4).toFixed(2)}%;top:0;bottom:0;width:2px;margin-left:-1px;background:var(--primary-text-color);box-shadow:0 0 0 1px rgba(0,0,0,.35)"></div></div>
<div style="position:relative;height:16px;font-size:12px;color:var(--secondary-text-color)">${ticks}</div></div>`;
]]]"""

NIGHT_TILE_JS = "[[[" + COMMON_JS + DAYPLAN_JS + r"""
const season = states['input_boolean.heating_season'] && states['input_boolean.heating_season'].state === 'on';
const tn = deg(presetT('climate.my_thermostat', 'home'));
let v;
if (!nsch) v = '—';
else if (nightOn) v = 'ніч ' + tn + ' до ' + nightEndTxt;
else v = 'з ' + fmtMin(nStart) + ' до ' + NIGHT_END[(wk + 1) % 7];
if (!season) v = 'поза сезоном';
const on = season && nightOn;
return `<div style="display:flex;align-items:center;gap:12px;height:100%;text-align:left">
<div style="width:38px;height:38px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:${on ? '#8B7FD4' : 'rgba(127,127,127,0.18)'}"><ha-icon icon="mdi:weather-night" style="--mdc-icon-size:21px;color:${on ? '#1c1c1e' : '#8B7FD4'}"></ha-icon></div>
<div style="display:flex;flex-direction:column;flex:1;min-width:0;text-align:left"><span style="font-size:14px;font-weight:500;color:var(--primary-text-color)">Нічний розклад</span><span style="font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${v}</span></div></div>`;
]]]"""

def night_tile():
    return {
        'type': 'custom:button-card', 'entity': 'schedule.night_schedule_thermostat',
        'variables': {'climate_night_tile': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': PLAN_ENTS + ['input_boolean.heating_season', 'climate.my_thermostat'],
        'tap_action': {'action': 'more-info'},
        'grid_options': {'columns': 6, 'rows': 1},
        'custom_fields': {'b': NIGHT_TILE_JS},
        'styles': {'card': [{'padding': '8px 12px'}, {'height': '56px'}, {'border-radius': '16px'}, {'box-shadow': 'none'}, {'border': 'none'},
                            {'background': CARD_BG}],
                   'grid': single(), 'custom_fields': {'b': FILL}},
    }

def panel_card(key):
    r = ROOMS[key]
    main = r['heat'] or r['hum']
    kind = 'heat' if r['heat'] else 'hum'
    cur = r['t'] if kind == 'heat' else r['h']
    devs = DEVICES[key]
    trig = sorted(set(ents(r) + [d[1] for d in devs] + [main] + (PLAN_ENTS if kind == 'heat' else [])))
    card = {
        'type': 'custom:button-card',
        'variables': {'climate_room_panel': key},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': trig,
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'visibility': [{'condition': 'state', 'entity': SEL, 'state': r['name']}],
        'custom_fields': {
            'dial': dial_js(main, kind, cur),
            'info': info_js(key),
            'mn': {'card': step_btn(main, kind, -1)},
            'pl': {'card': step_btn(main, kind, +1)},
            'pre': {'card': presets_card(key)},
            'dev': {'card': {'type': 'grid', 'columns': 2, 'square': False, 'cards': [device_row(*d) for d in devs]}},
        },
        'styles': {
            'card': [{'padding': '0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}],
            'grid': [{'grid-template-areas': '"dial info info" "dial mn pl" "pre pre pre" "dev dev dev"'},
                     {'grid-template-columns': '150px 48px 1fr'},
                     {'grid-template-rows': 'auto auto auto auto'}, {'column-gap': '12px'}, {'row-gap': '12px'}],
            'custom_fields': {'dial': [{'justify-self': 'start'}, {'align-self': 'center'}],
                              'info': FILL, 'mn': [{'justify-self': 'start'}, {'align-self': 'end'}],
                              'pl': [{'justify-self': 'start'}, {'align-self': 'end'}],
                              'pre': FILL, 'dev': FILL},
        },
    }
    if kind == 'heat':
        card['custom_fields']['strip'] = strip_js(key)
        card['styles']['grid'][0] = {'grid-template-areas': '"dial info info" "dial mn pl" "strip strip strip" "pre pre pre" "dev dev dev"'}
        card['styles']['grid'][2] = {'grid-template-rows': 'auto auto auto auto auto'}
        card['styles']['custom_fields']['strip'] = FILL
    return card

_orig_main = main
def main():
    _orig_main()
    new = json.load(open(SCR + 'dash_new.json'))
    v = [x for x in new.get('config', new)['views'] if x.get('path') == 'climate'][0]
    for s in v['sections'][:3]:
        s['background'] = True
    s1 = v['sections'][1]
    s1['cards'] = [c for c in s1['cards'] if not (c.get('variables') or {}).get('climate_night_tile')]
    i = next(i for i, c in enumerate(s1['cards']) if c.get('type') == 'tile' and c.get('entity') == 'schedule.night_schedule_thermostat')
    s1['cards'][i]['visibility'] = PARK
    s1['cards'].insert(i, night_tile())
    idx = next(i for i, c in enumerate(s1['cards']) if c.get('heading') == 'Розклад')
    for c in s1['cards'][idx:]:
        c['visibility'] = PARK
    json.dump(new, open(SCR + 'dash_new.json', 'w'), ensure_ascii=False, indent=1)
    print('styled')

# Стан as a room scale (2026-11-07): one sentence on top, then one row per room on a shared 18-28° scale
# (white tick = target, dot = current, bar between them, amber while heating). The plan card is parked.
LO, HI = 18, 28
DEAD = [[FLOOR, 'Тепла підлога'], ['switch.towel_warmer_plug', 'Рушникосушарка'], ['climate.my_thermostat', 'Опалення спальні'],
        ['climate.childroom', 'Опалення дитячої'], ['binary_sensor.bathroom_presence_sensor_occupancy', 'Датчик присутності ванни']]
LOC = {'Спальня': 'Спальні', 'Дитяча': 'Дитячій', 'Вітальня': 'Вітальні', 'Ванна': 'Ванній'}
SCALE_JS = r"""
const LO = """ + str(LO) + ", HI = " + str(HI) + r""";
const P = (v) => Math.max(0, Math.min(100, (v - LO) / (HI - LO) * 100));
const STOPS = [[21, [58, 110, 200]], [22.5, [90, 160, 180]], [24, [255, 214, 120]], [26, [255, 140, 10]]];
const tcol = (v) => {
  if (v <= STOPS[0][0]) return 'rgb(' + STOPS[0][1] + ')';
  for (let i = 1; i < STOPS.length; i++) if (v <= STOPS[i][0]) {
    const [a, ca] = STOPS[i - 1], [b, cb] = STOPS[i], k = (v - a) / (b - a);
    return 'rgb(' + ca.map((c, j) => Math.round(c + (cb[j] - c) * k)) + ')';
  }
  return 'rgb(' + STOPS[STOPS.length - 1][1] + ')';
};
const TRACK = 'linear-gradient(90deg,' + STOPS.map((s) => 'rgb(' + s[1] + ') ' + P(s[0]).toFixed(0) + '%').join(',') + ')';
const dg = (v) => String(v).replace('.', ',') + '°';
"""
ROW_COLS = 'grid-template-columns:118px minmax(0,1fr) 52px;column-gap:10px;padding:0 10px'

def scale_row(key):
    r = ROOMS[key]
    rj = json.dumps(r, ensure_ascii=False)
    body = "[[[" + COMMON_JS + SCALE_JS + "const R = " + rj + ";\n" + r"""
const heat = S(R.heat), ac = S(R.ac), hum = S(R.hum), win = S(R.win);
const cur = num(R.t), h = num(R.h);
const hs = h === null ? '' : Math.round(h) + ' %';
const tgt = heat && heat.state === 'heat' && heat.attributes.temperature ? +heat.attributes.temperature : null;
const heating = !!heat && heat.attributes.hvac_action === 'heating';
let sub = '', sc = 'var(--secondary-text-color)', calm = false;
if (win && win.state === 'on') { sub = 'вікно відчинене'; sc = '#FF9F0A'; }
else if (!off(ac)) { sub = 'кондиц. ' + (acLbl[ac.state] || ac.state) + ' ' + dg(ac.attributes.temperature); sc = '#64D2FF'; }
else if (heating) { sub = 'гріє до ' + dg(tgt); sc = '#FF9F0A'; }
else if (hum && hum.state === 'on' && ['humidifying', 'drying'].includes(hum.attributes.action)) { sub = (hum.attributes.action === 'drying' ? 'осушує до ' : 'зволожує до ') + hum.attributes.humidity + ' %'; sc = '#64D2FF'; }
else if (heat && heat.state === 'unavailable') { sub = 'термостат недоступний'; sc = '#FF9F0A'; }
else if (tgt !== null) { sub = 'ціль ' + dg(tgt); calm = true; }
else if (heat && heat.state === 'off') { sub = 'опалення вимк.'; calm = true; }
else if (hum && hum.state === 'on') { sub = (R.hum.includes('dehumid') ? 'осушення до ' : 'зволоження до ') + hum.attributes.humidity + ' %'; calm = true; }
else { sub = 'без обігріву'; calm = true; }
if (calm && hs) sub += ' · ' + hs;
let marks = '';
if (cur !== null && tgt !== null) {
  const a = Math.min(P(cur), P(tgt)), b = Math.max(P(cur), P(tgt));
  marks += `<div style="position:absolute;top:9px;height:4px;left:${a}%;width:${b - a}%;border-radius:2px;background:${heating ? '#FF9F0A' : 'rgba(255,255,255,0.35)'}"></div>`;
  marks += `<div style="position:absolute;top:3px;left:${P(tgt)}%;width:2px;height:16px;margin-left:-1px;border-radius:1px;background:var(--primary-text-color)"></div>`;
}
if (cur !== null) marks += `<div style="position:absolute;top:4px;left:${P(cur)}%;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:${tcol(cur)};box-shadow:0 0 0 2px #1c1c1e"></div>`;
return `<div style="display:grid;""" + ROW_COLS + r""";align-items:center;height:100%;box-sizing:border-box;text-align:left">
<div style="display:flex;flex-direction:column;min-width:0;text-align:left"><span style="font-size:14px;font-weight:500;color:var(--primary-text-color)">${R.name}</span>
<span style="font-size:12px;color:${sc};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${sub}</span></div>
<div style="padding:0 7px"><div style="position:relative;height:22px"><div style="position:absolute;left:0;right:0;top:9px;height:4px;border-radius:2px;background:${TRACK};opacity:0.3"></div>${marks}</div></div>
<div style="text-align:right;font-size:18px;font-weight:500;font-variant-numeric:tabular-nums;color:var(--primary-text-color)">${f1(cur)}°</div></div>`;
]]]"""
    # The Керування section is parked, so a row opens its room's main device instead of selecting the room.
    return {
        'type': 'custom:button-card',
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ents(r),
        'tap_action': {'action': 'more-info', 'entity': r['heat'] or r['hum'] or r['t'], 'haptic': 'light'},
        'hold_action': {'action': 'more-info', 'entity': r['t']},
        'custom_fields': {'b': body},
        'styles': {
            'card': [{'padding': '0'}, {'height': '48px'}, {'border-radius': '12px'}, {'border': 'none'},
                     {'background': 'transparent'}, {'box-shadow': 'none'}],
            'grid': single(), 'custom_fields': {'b': FILL},
        },
    }

SUM_JS = "[[[" + COMMON_JS + DAYPLAN_JS + r"""
const RM = """ + json.dumps([dict(ROOMS[k], loc=LOC[ROOMS[k]['name']]) for k in ROOMS], ensure_ascii=False) + r""";
const dg = (v) => String(v).replace('.', ',') + '°';
const season = S('input_boolean.heating_season') && S('input_boolean.heating_season').state === 'on';
const warn = [], info = [], calm = [];
for (const r of RM) {
  const h = S(r.heat), a = S(r.ac), w = S(r.win), cur = num(r.t);
  if (season && w && w.state === 'on') warn.push('у ' + r.loc + ' відчинене вікно');
  if (h && h.state === 'heat' && a && ['cool', 'heat_cool', 'dry'].includes(a.state)) warn.push('у ' + r.loc + ' кондиціонер працює разом з опаленням');
  if (!season || !h || h.state !== 'heat' || !h.attributes.temperature || cur === null) continue;
  const tg = +h.attributes.temperature, d = cur - tg;
  if (h.attributes.hvac_action === 'heating') info.push(r.name + ' гріє до ' + dg(tg));
  else if (d <= -1) warn.push('у ' + r.loc + ' холодніше за ціль на ' + f1(-d) + '°');
  else calm.push([r.name, d > 0.5 ? 'up' : 'in', d]);
}
// Status badge instead of a sentence (user's pick 2026-11-07); the room rows say what exactly happened.
// [icon, word, word colour, circle background, icon colour, ring]
let st;
if (warn.length) st = ['mdi:alert-outline', 'Увага · ' + warn.length, '#FF9F0A', 'rgba(255,159,10,0.18)', '#FF9F0A', 'inset 0 0 0 1.5px rgba(255,159,10,0.7)'];
else if (info.length) st = ['mdi:fire', 'Гріє', 'var(--primary-text-color)', '#FF9F0A', '#1c1c1e', 'none'];
else if (!season) st = ['mdi:radiator-off', 'Поза сезоном', 'var(--primary-text-color)', 'rgba(127,127,127,0.18)', '#8E8E93', 'none'];
else if (!calm.length) st = ['mdi:help', 'Немає даних', 'var(--primary-text-color)', 'rgba(127,127,127,0.18)', '#8E8E93', 'none'];
else st = ['mdi:check', 'У нормі', 'var(--primary-text-color)', 'rgba(50,215,75,0.16)', '#32D74B', 'inset 0 0 0 1.5px rgba(50,215,75,0.6)'];
const w0 = S('weather.home');
const out = w0 && w0.attributes.temperature !== undefined ? f1(w0.attributes.temperature) + '°' : '—';
const tn = deg(presetT('climate.my_thermostat', 'home'));
let phase;
if (!season) phase = '';
else if (nightOn) phase = `<span style="color:#C4BCEE">ніч ${tn} до ${nightEndTxt}</span>`;
else if (nowMin >= eStart) phase = `<span style="color:#FFC266">вечір до ${fmtMin(nStart)}</span>`;
else phase = `день, вечір з ${fmtMin(eStart)}`;
return `<div style="display:flex;align-items:center;gap:12px;padding:0 10px 8px;text-align:left">
<div style="width:44px;height:44px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;background:${st[3]};box-shadow:${st[5]}"><ha-icon icon="${st[0]}" style="--mdc-icon-size:24px;color:${st[4]}"></ha-icon></div>
<div style="display:flex;flex-direction:column;gap:2px;min-width:0;text-align:left"><span style="font-size:16px;font-weight:600;color:${st[2]}">${st[1]}</span>
<span style="font-size:13px;color:var(--secondary-text-color)">надворі ${out}${phase ? ' · ' + phase : ''}</span></div></div>`;
]]]"""

AXIS_HTML = ('<div style="display:grid;' + ROW_COLS + ';text-align:left"><span></span><div style="padding:0 7px"><div style="position:relative;height:14px;font-size:11px;color:var(--secondary-text-color);font-variant-numeric:tabular-nums">'
             + ''.join(f'<span style="position:absolute;left:{(v - LO) / (HI - LO) * 100:.0f}%;transform:translateX(-50%)">{v}°</span>' for v in range(LO, HI + 1, 2))
             + '</div></div><span></span></div>')

def soft_chip(label, icon, color, tint, active_js, tap, ents_):
    a = "(" + active_js + ")"
    return {
        'type': 'custom:button-card', 'name': label, 'icon': icon, 'layout': 'icon_name', 'show_state': False, 'show_icon': True,
        'triggers_update': ents_, 'tap_action': dict(tap, haptic='light'), 'hold_action': {'action': 'none'},
        'styles': {
            'card': [{'height': '30px'}, {'padding': '0 11px'}, {'border-radius': '15px'}, {'box-shadow': 'none'},
                     {'border': "[[[ return '1px solid ' + (" + a + " ? '" + color + "' : 'rgba(127,127,127,0.45)'); ]]]"},
                     {'background': "[[[ return " + a + " ? '" + tint + "' : 'transparent'; ]]]"}],
            'grid': [{'column-gap': '6px'}, {'grid-template-columns': 'auto auto'}, {'justify-content': 'center'}],
            'img_cell': [{'width': '16px'}, {'height': '16px'}],
            'icon': [{'width': '16px'}, {'color': "[[[ return " + a + " ? '" + color + "' : 'var(--secondary-text-color)'; ]]]"}],
            'name': [{'font-size': '13px'}, {'font-weight': '500'}, {'color': "[[[ return " + a + " ? '" + color + "' : 'var(--secondary-text-color)'; ]]]"}],
        },
    }

def chips_row():
    season_on = "states['" + SEASON + "'] && states['" + SEASON + "'].state === 'on'"
    night_on = "states['" + NIGHT_SCHED + "'] && states['" + NIGHT_SCHED + "'].state === 'on'"
    dead_js = "const D = " + json.dumps([d[0] for d in DEAD]) + "; const n = D.filter((e) => states[e] && states[e].state === 'unavailable').length;"
    season = soft_chip("[[[ return " + season_on + " ? 'Сезон опалення' : 'Поза сезоном'; ]]]", 'mdi:radiator', '#FF9F0A', 'rgba(255,159,10,0.14)', season_on,
                       {'action': 'perform-action', 'perform_action': 'input_boolean.toggle', 'target': {'entity_id': SEASON},
                        'confirmation': {'text': 'Змінити сезон опалення?'}}, [SEASON])
    night = soft_chip('Нічний розклад', 'mdi:weather-night', '#C4BCEE', 'rgba(139,127,212,0.18)', night_on,
                      {'action': 'more-info', 'entity': NIGHT_SCHED}, [NIGHT_SCHED])
    md = ("{% set d = " + json.dumps(DEAD, ensure_ascii=False) + " %}"
          "{% for e, n in d if states(e) == 'unavailable' %}- **{{ n }}**: недоступно з {{ as_local(states[e].last_changed).strftime('%d.%m %H:%M') }}\n{% endfor %}")
    dead = soft_chip("[[[ " + dead_js + " return n + ' недоступні'; ]]]", 'mdi:alert-outline', '#FF9F0A', 'rgba(255,159,10,0.14)', 'true',
                     {'action': 'fire-dom-event', 'browser_mod': {'service': 'browser_mod.popup', 'data': {
                         'title': 'Недоступні пристрої', 'content': {'type': 'markdown', 'content': md}}}}, [d[0] for d in DEAD])
    dead['styles']['card'].append({'display': "[[[ " + dead_js + " return n ? 'block' : 'none'; ]]]"})
    return {
        'type': 'custom:button-card', 'show_name': False, 'show_icon': False, 'show_state': False,
        'tap_action': {'action': 'none'},
        'custom_fields': {'sea': {'card': season}, 'nsc': {'card': night}, 'dead': {'card': dead}},
        'styles': {
            'card': [{'padding': '4px 10px 0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}, {'overflow': 'visible'}],
            'grid': [{'display': 'flex'}, {'flex-wrap': 'wrap'}, {'justify-content': 'flex-start'}, {'gap': '6px'}],
            'custom_fields': {k: [{'flex': 'none'}, {'width': 'auto'}] for k in ('sea', 'nsc', 'dead')},
        },
    }

def scale_card():
    areas = '"sum" "bed" "ch" "liv" "bath" "axis" "cor"'
    return {
        'type': 'custom:button-card',
        'variables': {'climate_scale': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': ALL_ENTS + ['sun.sun', 'sensor.time_of_day'],
        'tap_action': {'action': 'none'},
        'grid_options': {'columns': 12},
        'custom_fields': {
            'sum': SUM_JS,
            'bed': {'card': scale_row('bedroom')}, 'ch': {'card': scale_row('childroom')},
            'liv': {'card': scale_row('livingroom')}, 'bath': {'card': scale_row('bathroom')},
            'axis': AXIS_HTML,
            'cor': {'card': corridor_hidden_when_dead()},
        },
        'styles': {
            'card': [{'padding': '0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}, {'overflow': 'visible'}],
            'grid': [{'grid-template-areas': areas}, {'grid-template-columns': 'minmax(0, 1fr)'}, {'row-gap': '4px'}],
            'custom_fields': {k: FILL for k in ['sum', 'bed', 'ch', 'liv', 'bath', 'axis', 'cor']},
        },
    }

# Стан heading with three round buttons on the right (season, night schedule, unavailable count); the
# original heading card is parked. Replaces the chips row that sat at the bottom of the scale card.
def round_btn(color, tint, active_js, tap, ents_, icon=None, label=None):
    a = "(" + active_js + ")"
    card = {
        'type': 'custom:button-card', 'show_state': False, 'show_icon': bool(icon), 'show_name': bool(label),
        'triggers_update': ents_, 'tap_action': dict(tap, haptic='light'), 'hold_action': {'action': 'none'},
        'styles': {
            'card': [{'width': '34px'}, {'height': '34px'}, {'padding': '0'}, {'border-radius': '50%'}, {'box-shadow': 'none'},
                     {'border': "[[[ return '1px solid ' + (" + a + " ? '" + color + "' : 'rgba(127,127,127,0.45)'); ]]]"},
                     {'background': "[[[ return " + a + " ? '" + tint + "' : 'transparent'; ]]]"}],
            'grid': [{'grid-template-areas': '"i"' if icon else '"n"'}, {'grid-template-columns': '1fr'}, {'grid-template-rows': '1fr'}],
        },
    }
    if icon:
        card['icon'] = icon
        card['styles']['img_cell'] = [{'width': '18px'}, {'height': '18px'}, {'justify-self': 'center'}, {'align-self': 'center'}]
        card['styles']['icon'] = [{'width': '18px'}, {'color': "[[[ return " + a + " ? '" + color + "' : 'var(--secondary-text-color)'; ]]]"}]
    if label:
        card['name'] = label
        card['styles']['name'] = [{'font-size': '13px'}, {'font-weight': '600'}, {'justify-self': 'center'}, {'align-self': 'center'},
                                  {'color': "[[[ return " + a + " ? '" + color + "' : 'var(--secondary-text-color)'; ]]]"}]
    return card

def stan_heading():
    season_on = "states['" + SEASON + "'] && states['" + SEASON + "'].state === 'on'"
    night_on = "states['" + NIGHT_SCHED + "'] && states['" + NIGHT_SCHED + "'].state === 'on'"
    dead_js = "const D = " + json.dumps([d[0] for d in DEAD]) + "; const n = D.filter((e) => states[e] && states[e].state === 'unavailable').length;"
    season = round_btn('#FF9F0A', 'rgba(255,159,10,0.14)', season_on,
                       {'action': 'perform-action', 'perform_action': 'input_boolean.toggle', 'target': {'entity_id': SEASON},
                        'confirmation': {'text': "[[[ return " + season_on + " ? 'Вимкнути сезон опалення?' : 'Увімкнути сезон опалення?'; ]]]"}},
                       [SEASON], icon="[[[ return " + season_on + " ? 'mdi:radiator' : 'mdi:radiator-off'; ]]]")
    night = round_btn('#C4BCEE', 'rgba(139,127,212,0.18)', night_on, {'action': 'more-info', 'entity': NIGHT_SCHED}, [NIGHT_SCHED], icon='mdi:weather-night')
    md = ("{% set d = " + json.dumps(DEAD, ensure_ascii=False) + " %}"
          "{% for e, n in d if states(e) == 'unavailable' %}- **{{ n }}**: недоступно з {{ as_local(states[e].last_changed).strftime('%d.%m %H:%M') }}\n{% endfor %}")
    dead = round_btn('#FF9F0A', 'rgba(255,159,10,0.14)', 'true',
                     {'action': 'fire-dom-event', 'browser_mod': {'service': 'browser_mod.popup', 'data': {
                         'title': 'Недоступні пристрої', 'content': {'type': 'markdown', 'content': md}}}},
                     [d[0] for d in DEAD], label="[[[ " + dead_js + " return String(n); ]]]")
    dead['styles']['card'].append({'display': "[[[ " + dead_js + " return n ? 'block' : 'none'; ]]]"})
    title = ('<div style="display:block;width:100%;padding:0 4px;box-sizing:border-box;text-align:left;white-space:nowrap">'
             '<ha-icon icon="mdi:home-thermometer-outline" style="--mdc-icon-size:20px;color:var(--secondary-text-color);vertical-align:middle"></ha-icon>'
             '<span style="margin-left:8px;vertical-align:middle;font-size:16px;font-weight:400;color:var(--primary-text-color)">Стан</span></div>')
    return {
        'type': 'custom:button-card', 'variables': {'climate_stan_heading': True},
        'show_name': False, 'show_icon': False, 'show_state': False,
        'tap_action': {'action': 'none'}, 'grid_options': {'columns': 12},
        'custom_fields': {'t': title, 'sea': {'card': season}, 'nsc': {'card': night}, 'dead': {'card': dead}},
        'styles': {
            'card': [{'padding': '0 4px 0 0'}, {'background': 'none'}, {'box-shadow': 'none'}, {'border': 'none'}, {'overflow': 'visible'}],
            'grid': [{'grid-template-areas': '"t sea nsc dead"'}, {'grid-template-columns': 'minmax(0, 1fr) auto auto auto'},
                     {'column-gap': '6px'}, {'align-items': 'center'}],
            'custom_fields': {'t': [{'justify-self': 'stretch'}, {'width': '100%'}, {'text-align': 'left'}], 'sea': [{'width': '34px'}], 'nsc': [{'width': '34px'}],
                              'dead': [{'width': 'auto'}]},
        },
    }

_styled_main = main
def main():
    _styled_main()
    new = json.load(open(SCR + 'dash_new.json'))
    v = [x for x in new.get('config', new)['views'] if x.get('path') == 'climate'][0]
    s0 = v['sections'][0]
    s0['cards'] = [c for c in s0['cards'] if not (c.get('variables') or {}).get('climate_scale')]
    plan = next(c for c in s0['cards'] if (c.get('variables') or {}).get('climate_plan'))
    plan['visibility'] = PARK
    s0['cards'].insert(1, scale_card())
    assert s0['cards'][0].get('heading') == 'Стан'
    s0['cards'][0]['visibility'] = PARK
    s0['cards'].insert(1, stan_heading())
    assert v['sections'][1]['cards'][0].get('heading') == 'Керування'
    v['sections'][1]['visibility'] = PARK
    json.dump(new, open(SCR + 'dash_new.json', 'w'), ensure_ascii=False, indent=1)
    print('scale')

main()
