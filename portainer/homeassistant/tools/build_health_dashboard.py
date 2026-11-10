#!/usr/bin/env python3
"""Build the "Здоров'я HA" dashboard (layout A: hero + checklist + charts).

    python3 build_health_dashboard.py out.json

Writes a full Lovelace config. Save it with:
    node ha-lovelace.mjs save --dash dashboard-health --base live.json out.json
"""
import json
import sys

SCORE = 'sensor.system_ha_global_health_score'
CPU = 'sensor.system_monitor_processor_use'
RAM = 'sensor.system_monitor_memory_usage'
TEMP = 'sensor.system_monitor_processor_temperature'
DISK = 'sensor.system_monitor_disk_usage_config'
UPTIME = 'sensor.system_monitor_uptime'
DISK_FREE = 'sensor.system_monitor_disk_free_config'
LOAD = 'sensor.system_monitor_load_5_min'
SWAP = 'sensor.system_monitor_swap_usage'
NET_IN = 'sensor.system_monitor_network_throughput_in_enp1s0'
NET_OUT = 'sensor.system_monitor_network_throughput_out_enp1s0'
PSI_CPU = 'sensor.system_monitor_cpu_pressure_some_60s_average'
PSI_RAM = 'sensor.system_monitor_memory_pressure_some_60s_average'
PSI_IO = 'sensor.system_monitor_io_pressure_some_60s_average'

CARD_BG = 'var(--ha-card-background, var(--card-background-color))'
FULL = [{'width': '100%'}, {'justify-self': 'stretch'}]

# Shared JS: builds the list of problems from the HAGHS attributes.
# Each item: {c: colour, i: mdi icon, t: title, d: detail line}.
PROBLEMS_JS = r"""
const s = states['%(score)s'];
const a = s ? s.attributes : {};
const R = '#FF453A', O = '#FF9F0A';
const nm = (x) => typeof x === 'string' ? x : (x && (x.name || x.title || x.entity_id || x.issue_id)) || String(x);
const ls = (arr, n = 4) => { const v = (arr || []).map(nm); return v.slice(0, n).join(' · ') + (v.length > n ? ` · ще ${v.length - n}` : ''); };
const P = [];
const add = (flag, c, i, t, d) => { if (a[flag]) P.push({c, i, t, d}); };
add('rec_integration_health', R, 'mdi:puzzle-remove-outline', `${a.integration_unhealthy_count} інтеграції не запустилися`, ls(a.integration_unhealthy_entries));
add('rec_update_failed', R, 'mdi:alert-octagon-outline', 'Останній цикл HAGHS завершився з помилкою', 'перевір журнал HA');
add('rec_repairs', R, 'mdi:wrench-outline', `Repairs: ${a.repair_count}`, ls(a.repairs));
const top = (o, n = 4) => Object.entries(o || {}).sort((x, y) => y[1] - x[1]).slice(0, n).map(([k, v]) => `${k} ${v}`).join(' · ');
const named = (arr, total) => { const c = {}; let anon = 0; const v = (arr || []).map(nm);
  v.forEach(x => { const t = x.trim(); if (/^[0-9a-f]{32}$/.test(t)) anon++; else c[t] = (c[t] || 0) + 1; });
  const rest = total > v.length ? [`ще ${total - v.length} поза списком HAGHS`] : [];
  return [...Object.entries(c).sort((x, y) => y[1] - x[1]).slice(0, 3).map(([k, n]) => n > 1 ? `${k} ×${n}` : k), ...(anon ? [`${anon} без назви`] : []), ...rest].join(' · '); };
add('rec_dead_devices', R, 'mdi:skull-outline', `${a.dead_device_count} пристроїв не на зв'язку`, named(a.dead_devices, a.dead_device_count));
add('rec_disk_low', R, 'mdi:harddisk', 'Мало місця на диску', '');
add('rec_backup_stale', R, 'mdi:backup-restore', 'Резервна копія застаріла', '');
add('rec_core_lag', R, 'mdi:timer-sand', 'HA відповідає повільно', '');
add('rec_cpu_load', O, 'mdi:cpu-64-bit', 'Високе навантаження процесора', '');
add('rec_ram_pressure', O, 'mdi:memory', "Тиск на пам'ять", '');
add('rec_io_pressure', O, 'mdi:harddisk', 'Диск перевантажений (IO)', '');
add('rec_power_unstable', O, 'mdi:flash-alert-outline', 'Нестабільне живлення', '');
add('rec_db_over_limit', O, 'mdi:database-alert-outline', `База даних завелика: ${(a.db_size_mb / 1024).toFixed(1)} ГБ`, '');
add('rec_config_audit', O, 'mdi:database-cog-outline', 'Recorder без фільтра сутностей', `зберігає ${a.recorder_keep_days} дн, фільтр ${a.recorder_filter_active ? 'є' : 'немає'} · −${a.config_audit_bonus} бонусних балів`);
add('rec_zombie', O, 'mdi:ghost-outline', `${a.zombie_count} зомбі-сутностей`, top(a.zombie_count_per_integration));
add('rec_stale', O, 'mdi:clock-alert-outline', `${a.stale_candidate_count} сутностей давно не оновлювались`, ls(a.stale_candidates));
add('rec_empty_devices', O, 'mdi:package-variant', `${a.empty_device_count} порожніх пристроїв`, ls(a.empty_devices));
add('rec_updates_pending', O, 'mdi:download-outline', `${(a.pending_updates || []).length} оновлень чекають`, '');
add('rec_pillar_fallback', O, 'mdi:help-circle-outline', 'Частину показників не вдалося виміряти', ls(a.pillar_fallbacks));
add('rec_cpu_ram_unmeasured', O, 'mdi:help-circle-outline', 'CPU і RAM не вимірюються', '');
""" % {'score': SCORE}


def hero():
    js = PROBLEMS_JS + r"""
if (!s) return '<div style="color:var(--secondary-text-color)">HAGHS недоступний</div>';
const v = Number(s.state);
const col = (x) => x >= 90 ? '#32D74B' : x >= 70 ? '#FF9F0A' : '#FF453A';
const word = v >= 90 ? 'Добре' : v >= 70 ? 'Є що виправити' : 'Погано';
const C = 2 * Math.PI * 32, dash = (C * Math.max(0, Math.min(100, v)) / 100).toFixed(1);
const n = P.length;
const sub = n ? `${n} ${n === 1 ? 'проблема' : n < 5 ? 'проблеми' : 'проблем'}` : 'без проблем';
const bar = (label, x) => `<div class="hr"><span class="hl">${label}</span><div class="hb"><i style="width:${x}%;background:${col(x)}"></i></div><span class="hv">${x}</span></div>`;
const facts = `БД ${(a.db_size_mb / 1024).toFixed(1)} ГБ · зберігає ${a.recorder_keep_days} дн · CPU і RAM для оцінки: ${a.cpu_ram_source === 'psi' ? 'PSI ядра' : 'сенсори'}`;
return `
<div class="hw">
  <svg width="84" height="84" viewBox="0 0 84 84" aria-hidden="true">
    <circle cx="42" cy="42" r="32" fill="none" stroke="rgba(127,127,127,.25)" stroke-width="8"/>
    <circle cx="42" cy="42" r="32" fill="none" stroke="${col(v)}" stroke-width="8" stroke-linecap="round"
      stroke-dasharray="${dash} ${C.toFixed(1)}" transform="rotate(-90 42 42)"/>
    <text x="42" y="49" text-anchor="middle" fill="var(--primary-text-color)" font-size="22" font-weight="600">${v}</text>
  </svg>
  <div class="hi">
    <div class="ht">${word} <span class="hs">· ${sub}</span></div>
    ${bar('Залізо', a.hardware_score)}
    ${bar('Програми', a.application_score)}
  </div>
</div>
<div class="hf">${facts}</div>`;
"""
    return {
        'type': 'custom:button-card',
        'entity': SCORE,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [SCORE],
        'tap_action': {'action': 'more-info'},
        'custom_fields': {'hero': '[[[' + js + ']]]'},
        'extra_styles': (
            '#hero{text-align:left}'
            '.hw{display:flex;gap:16px;align-items:center}'
            '.hi{flex:1;min-width:0}'
            '.ht{font-size:18px;font-weight:600;margin-bottom:6px}'
            '.hs{font-size:13px;font-weight:400;color:var(--secondary-text-color)}'
            '.hr{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px}'
            '.hl{width:72px;color:var(--secondary-text-color)}'
            '.hb{flex:1;height:6px;border-radius:3px;background:rgba(127,127,127,.25);overflow:hidden}'
            '.hb i{display:block;height:100%;border-radius:3px}'
            '.hv{width:28px;text-align:right}'
            '.hf{margin-top:12px;font-size:12px;color:var(--secondary-text-color)}'
            '.hf+.hf{margin-top:4px}'
        ),
        'styles': {
            'card': [{'padding': '16px'}, {'text-align': 'left'}],
            'grid': [{'grid-template-areas': '"hero"'}, {'grid-template-columns': '1fr'}],
            'custom_fields': {'hero': FULL},
        },
    }


def checklist():
    js = PROBLEMS_JS + r"""
if (!P.length) return '<div class="cr"><ha-icon icon="mdi:check-circle-outline" style="color:#32D74B"></ha-icon><div class="ct">Нічого не забирає бали</div></div>';
return P.map(p => `
<div class="cr">
  <ha-icon icon="${p.i}" style="color:${p.c}"></ha-icon>
  <div class="cx"><div class="ct">${p.t}</div>${p.d ? `<div class="cd">${p.d}</div>` : ''}</div>
</div>`).join('');
"""
    return {
        'type': 'custom:button-card',
        'entity': SCORE,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [SCORE],
        'tap_action': {'action': 'none'},
        'custom_fields': {'list': '[[[' + js + ']]]'},
        'extra_styles': (
            '#list{text-align:left}'
            '.cr{display:flex;gap:12px;align-items:flex-start;padding:8px 0}'
            '.cr+.cr{border-top:1px solid rgba(127,127,127,.18)}'
            '.cr ha-icon{--mdc-icon-size:22px;flex:none;margin-top:1px}'
            '.cx{flex:1;min-width:0}'
            '.ct{font-size:14px}'
            '.cd{font-size:12px;color:var(--secondary-text-color);margin-top:2px;white-space:normal;overflow-wrap:anywhere}'
        ),
        'styles': {
            'card': [{'padding': '8px 16px'}, {'text-align': 'left'}],
            'grid': [{'grid-template-areas': '"list"'}, {'grid-template-columns': '1fr'}],
            'custom_fields': {'list': FULL},
        },
    }


def updates():
    content = (
        "{% set u = state_attr('" + SCORE + "', 'pending_updates') or [] %}"
        "{% if u | length == 0 %}Усе оновлено{% else %}"
        "<details><summary>Показати список ({{ u | length }})</summary>\n\n"
        "{% for x in u | sort %}- {{ x | replace(' Update', '') }}\n{% endfor %}"
        "\n</details>{% endif %}"
    )
    return {'type': 'markdown', 'content': content, 'entity_id': [SCORE]}


def score_chart():
    return {
        'type': 'custom:apexcharts-card',
        'graph_span': '7d',
        'header': {'show': True, 'title': "Оцінка, 7 днів", 'show_states': True, 'colorize_states': True},
        'apex_config': {
            'chart': {'height': 180},
            'legend': {'show': False},
            'xaxis': {'tickAmount': 7, 'labels': {'datetimeUTC': False, 'formatter':
                "EVAL:function(v){return ['нд','пн','вт','ср','чт','пт','сб'][new Date(v).getDay()];}"}},
        },
        'yaxis': [{'min': 50, 'max': 100, 'decimals': 0, 'apex_config': {'tickAmount': 5}}],
        'series': [{
            'entity': SCORE, 'name': 'Оцінка', 'color': '#32D74B', 'stroke_width': 2,
            'type': 'line', 'curve': 'stepline', 'group_by': {'func': 'min', 'duration': '1h'},
            'show': {'in_header': 'raw'},
        }],
    }


def host_chart():
    return {
        'type': 'custom:apexcharts-card',
        'graph_span': '24h',
        'header': {'show': True, 'title': 'CPU, RAM і температура, 24 години', 'show_states': True, 'colorize_states': True},
        'apex_config': {
            'chart': {'height': 200},
            'legend': {'show': False},
            'xaxis': {'labels': {'datetimeUTC': False, 'format': 'HH:mm'}},
        },
        'yaxis': [
            {'id': 'pct', 'min': 0, 'max': 100, 'decimals': 0, 'apex_config': {'tickAmount': 4}},
            {'id': 'temp', 'opposite': True, 'min': 30, 'max': 90, 'decimals': 0, 'apex_config': {'tickAmount': 4}},
        ],
        'series': [
            {'entity': CPU, 'name': 'CPU', 'yaxis_id': 'pct', 'color': '#0A84FF', 'stroke_width': 2,
             'group_by': {'func': 'avg', 'duration': '10min'}},
            {'entity': RAM, 'name': 'RAM', 'yaxis_id': 'pct', 'color': '#BF5AF2', 'stroke_width': 2,
             'group_by': {'func': 'avg', 'duration': '10min'}},
            {'entity': TEMP, 'name': 'Температура', 'yaxis_id': 'temp', 'color': '#FF9F0A', 'stroke_width': 2,
             'group_by': {'func': 'max', 'duration': '10min'}},
        ],
    }


def spark(entity, name, color, decimals=0):
    """Half-width tile: current value plus a 24 h sparkline."""
    return {
        'type': 'custom:mini-graph-card',
        'entities': [{'entity': entity, 'color': color}],
        'name': name,
        'hours_to_show': 24,
        'points_per_hour': 4,
        'aggregate_func': 'avg',
        'line_width': 2,
        'height': 56,
        'decimals': decimals,
        'font_size': 70,
        'align_state': 'left',
        'show': {'icon': False, 'legend': False, 'labels': False, 'extrema': False, 'fill': 'fade'},
        'grid_options': {'columns': 6, 'rows': 'auto'},
    }


def disk_tile():
    js = r"""
const u = states['%(disk)s'], f = states['%(free)s'];
const p = u ? Number(u.state) : NaN, g = f ? Number(f.state) : NaN;
const col = p >= 90 ? '#FF453A' : p >= 75 ? '#FF9F0A' : '#32D74B';
return `<div class="dn">Диск</div>
<div class="dv">${isNaN(p) ? '—' : Math.round(p)}<span>%%</span><em>${isNaN(g) ? '' : Math.round(g) + ' ГБ вільно'}</em></div>
<div class="db"><i style="width:${isNaN(p) ? 0 : p}%%;background:${col}"></i></div>`;
""" % {'disk': DISK, 'free': DISK_FREE}
    return {
        'type': 'custom:button-card',
        'entity': DISK,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [DISK, DISK_FREE],
        'tap_action': {'action': 'more-info'},
        'custom_fields': {'d': '[[[' + js + ']]]'},
        'extra_styles': (
            '#d{text-align:left}'
            '.dn{font-size:18px;font-weight:500;letter-spacing:.1em;opacity:.65;white-space:nowrap}'
            '.dv{font-size:28px;line-height:1.6;margin-top:6px}.dv span{font-size:16px;margin-left:2px}'
            '.dv em{font-style:normal;font-size:12px;color:var(--secondary-text-color);margin-left:8px}'
            '.db{height:8px;border-radius:4px;background:rgba(127,127,127,.25);overflow:hidden;margin-top:6px}'
            '.db i{display:block;height:100%;border-radius:4px}'
        ),
        'styles': {
            'card': [{'padding': '16px'}, {'height': '100%'}],
            'grid': [{'grid-template-areas': '"d"'}, {'grid-template-columns': '1fr'}],
            'custom_fields': {'d': FULL},
        },
        'grid_options': {'columns': 6, 'rows': 'auto'},
    }


def pressure_panel():
    js = r"""
const n = (id) => { const e = states[id]; const x = e ? Number(e.state) : NaN; return isNaN(x) ? null : x; };
const col = (x) => x >= 20 ? '#FF453A' : x >= 5 ? '#FF9F0A' : '#32D74B';
const row = (label, id) => { const x = n(id); const w = x === null ? 0 : Math.min(100, Math.max(2, x * 5));
  return `<div class="pr"><span class="pl">${label}</span><div class="pb"><i style="width:${w}%%;background:${x === null ? '#8E8E93' : col(x)}"></i></div><span class="pv">${x === null ? '—' : x.toFixed(1) + '%%'}</span></div>`; };
const f = (id, d) => { const x = n(id); return x === null ? '—' : x.toFixed(d); };
const up = states['%(uptime)s'];
const days = up && !isNaN(Date.parse(up.state)) ? Math.floor((Date.now() - Date.parse(up.state)) / 864e5) : null;
return `<div class="ph">Тиск ресурсів, середнє за 1 хв</div>
${row('Процесор', '%(pc)s')}${row("Пам'ять", '%(pm)s')}${row('Диск IO', '%(pi)s')}
<div class="pf">Load ${f('%(load)s', 2)} · swap ${f('%(swap)s', 0)}%% · мережа ↓ ${f('%(nin)s', 1)} ↑ ${f('%(nout)s', 1)} Мбіт/с · працює ${days === null ? '—' : days + ' дн'}</div>`;
""" % {'uptime': UPTIME, 'pc': PSI_CPU, 'pm': PSI_RAM, 'pi': PSI_IO, 'load': LOAD, 'swap': SWAP,
       'nin': NET_IN, 'nout': NET_OUT}
    return {
        'type': 'custom:button-card',
        'entity': PSI_CPU,
        'show_name': False, 'show_icon': False, 'show_state': False,
        'triggers_update': [PSI_CPU, PSI_RAM, PSI_IO, LOAD, SWAP, NET_IN, NET_OUT, UPTIME],
        'tap_action': {'action': 'none'},
        'custom_fields': {'p': '[[[' + js + ']]]'},
        'extra_styles': (
            '#p{text-align:left}'
            '.ph{font-size:13px;color:var(--secondary-text-color);margin-bottom:6px}'
            '.pr{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:13px}'
            '.pl{width:72px;color:var(--secondary-text-color)}'
            '.pb{flex:1;height:6px;border-radius:3px;background:rgba(127,127,127,.25);overflow:hidden}'
            '.pb i{display:block;height:100%;border-radius:3px}'
            '.pv{width:44px;text-align:right}'
            '.pf{margin-top:10px;font-size:12px;color:var(--secondary-text-color);white-space:normal}'
        ),
        'styles': {
            'card': [{'padding': '16px'}],
            'grid': [{'grid-template-areas': '"p"'}, {'grid-template-columns': '1fr'}],
            'custom_fields': {'p': FULL},
        },
    }


def net_chart():
    return {
        'type': 'custom:apexcharts-card',
        'graph_span': '24h',
        'header': {'show': True, 'title': 'Мережа, 24 години', 'show_states': True, 'colorize_states': True},
        'apex_config': {
            'chart': {'height': 170},
            'legend': {'show': False},
            'xaxis': {'labels': {'datetimeUTC': False, 'format': 'HH:mm'}},
        },
        'yaxis': [{'min': 0, 'decimals': 0, 'apex_config': {'tickAmount': 4}}],
        'series': [
            {'entity': NET_IN, 'name': 'Вхід', 'color': '#32D74B', 'stroke_width': 2,
             'group_by': {'func': 'avg', 'duration': '10min'}},
            {'entity': NET_OUT, 'name': 'Вихід', 'color': '#64D2FF', 'stroke_width': 2,
             'group_by': {'func': 'avg', 'duration': '10min'}},
        ],
    }


def heading(text, icon, style='title', **kw):
    return {'type': 'heading', 'heading': text, 'heading_style': style, 'icon': icon, **kw}


def build():
    return {
        'title': "Здоров'я HA",
        'views': [{
            'title': "Здоров'я",
            'path': 'health',
            'icon': 'mdi:heart-pulse',
            'type': 'sections',
            'max_columns': 1,
            'theme': 'ios-dark-mode-blue-red',
            'sections': [
                {'type': 'grid', 'cards': [heading('Стан', 'mdi:heart-pulse'), hero()]},
                {'type': 'grid', 'cards': [
                    heading('Що забирає бали', 'mdi:format-list-checks'),
                    checklist(),
                    heading('Оновлення', 'mdi:download-outline', 'subtitle',
                            tap_action={'action': 'navigate', 'navigation_path': '/config/updates'}),
                    updates(),
                ]},
                {'type': 'grid', 'cards': [
                    heading('Beelink EQ', 'mdi:server'),
                    spark(CPU, 'Процесор', '#0A84FF'),
                    spark(RAM, "Пам'ять", '#BF5AF2'),
                    spark(TEMP, 'Температура', '#FF9F0A'),
                    disk_tile(),
                    pressure_panel(),
                    host_chart(),
                    net_chart(),
                ]},
                {'type': 'grid', 'cards': [
                    heading('Статистика', 'mdi:chart-line'),
                    score_chart(),
                ]},
            ],
        }],
    }


if __name__ == '__main__':
    with open(sys.argv[1], 'w', encoding='utf-8') as f:
        json.dump(build(), f, ensure_ascii=False, indent=1)
