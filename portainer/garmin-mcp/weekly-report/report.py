"""Weekly Garmin report to Telegram, for training analysis in ChatGPT.

Every Sunday at REPORT_TIME (Europe/Kyiv) it pulls Monday to now from the garmin-mcp
server plus each activity's FIT file, then sends a short Ukrainian digest to Telegram
and the full English Markdown report as a .md file. The first run covers 28 days.

  python report.py           run forever on the weekly schedule
  python report.py --now     send this week's report once and exit
  python report.py --print   print the Markdown report and the digest, no Telegram
  python report.py --initial force the 28-day history on a one-off run
"""

import datetime as dt
import glob
import hashlib
import html
import json
import os
import sys
import time
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

from garmin_fit_sdk import Decoder, Stream

MCP_URL = os.environ.get("MCP_URL", "http://garmin-mcp:8491/mcp")
# the server only accepts localhost Host headers (DNS-rebinding check)
MCP_HOST = os.environ.get("MCP_HOST", "localhost:8491")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REPORT_DAY = int(os.environ.get("REPORT_WEEKDAY", "6"))  # Monday is 0
REPORT_TIME = os.environ.get("REPORT_TIME", "19:00")
# garmin-mcp saves downloads under its own home; this container mounts the same volume here
FIT_DIR = os.environ.get("FIT_DIR", "/fit")
MCP_FIT_DIR = "/home/garmin/Downloads/garmin"
STATE_FILE = os.environ.get("STATE_FILE", "/state/state.json")
TZ = ZoneInfo("Europe/Kyiv")
NA = "N/A"

UNAVAILABLE = [
    ("Training Readiness (morning score and timestamp)", "not exposed by the Garmin Personal API"),
    ("Recovery Time (remaining hours and timestamp)", "not exposed by the Garmin Personal API"),
    ("Acute Load, Chronic Load, Load Ratio, optimal Acute Load range", "not exposed by the Garmin Personal API"),
    ("Training Status", "not exposed by the Garmin Personal API"),
    ("Load Focus (low aerobic, high aerobic, anaerobic) and targets", "not exposed by the Garmin Personal API"),
    ("Confirmed rest day", "Garmin has no rest-day record in the Personal API"),
    ("Structured workout name", "only when an activity followed a Garmin workout; none of these did unless shown"),
]
UNAVAILABLE_NOTE = ("The Personal API's metrics service answers HTTP 404 for these, checked 2026-11-02. "
                    "They exist only in the Garmin Connect app.")

FEEL = {0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong"}
FEEL_UK = {0: "дуже слабко", 25: "слабко", 50: "нормально", 75: "добре", 100: "дуже добре"}
DAYS_UK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
DAY_ROW = "{:<5} {:>6} {:>5} {:>2} {:>3} {:>3}"
TYPES_UK = {"lap_swimming": "Басейн", "treadmill_running": "Доріжка", "running": "Біг",
            "elliptical": "Еліпс", "strength_training": "Силове", "cycling": "Вело", "walking": "Ходьба"}

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Mcp:
    def __init__(self):
        self.session = None
        self.next_id = 1
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "garmin-weekly-report", "version": "2"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, body):
        headers = {
            "Host": MCP_HOST,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["mcp-session-id"] = self.session
        req = urllib.request.Request(MCP_URL, json.dumps(body).encode(), headers)
        with opener.open(req, timeout=180) as resp:
            self.session = resp.headers.get("mcp-session-id") or self.session
            text = resp.read().decode()
        events = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if events:
            return json.loads(events[-1])
        return json.loads(text) if text.strip() else None

    def _rpc(self, method, params):
        self.next_id += 1
        reply = self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply["result"]

    def tool(self, name, **args):
        result = self._rpc("tools/call", {"name": name, "arguments": args})
        text = result["content"][0]["text"]
        if result.get("isError"):
            raise RuntimeError(f"{name}: {text}")
        return json.loads(text)


# formatting

def hms(seconds):
    if seconds is None:
        return NA
    seconds = round(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def hm(seconds):
    if seconds is None:
        return "–"
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def val(value, unit="", fmt="{}"):
    if value is None:
        return NA
    return fmt.format(value) + (f" {unit}" if unit else "")


def thousands(n):
    return "–" if n is None else f"{n:,}".replace(",", " ")


def avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def local_from_gmt(text):
    """'2026-10-31 07:40:46' in UTC -> aware Kyiv datetime."""
    return dt.datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=dt.timezone.utc).astimezone(TZ)


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


# data

def read_fit(mcp, activity_id):
    saved = mcp.tool("garmin_download_activity", activity_id=activity_id)
    path = saved["path"].replace(MCP_FIT_DIR, FIT_DIR, 1)
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".fit"))
        data = zf.read(name)
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read(convert_datetimes_to_dates=False)
    if errors:
        print(f"FIT {activity_id}: {errors}", flush=True)
    return messages


def fetch(start, end):
    mcp = Mcp()
    span = {"start": start.isoformat(), "end": end.isoformat()}
    data = {
        "daily": {d["calendarDate"]: d for d in mcp.tool("garmin_get_daily_summaries", **span).get("data", [])},
        "sleep": {s["calendarDate"]: s for s in mcp.tool("garmin_get_sleep_summaries", **span).get("results", [])},
        "hrv": {h["calendarDate"]: h for h in mcp.tool("garmin_get_hrv_summaries", **span).get("hrvSummaries", [])},
        "zones": mcp.tool("garmin_get_heart_rate_zones").get("results", []),
        "activities": [],
    }
    found = mcp.tool("garmin_search_activities", **span, limit=200, sort_order="asc").get("results", [])
    for a in sorted(found, key=lambda a: a.get("startTimeGMT", "")):
        try:
            a["_fit"] = read_fit(mcp, a["activityId"])
        except Exception as exc:  # report the activity anyway, without FIT detail
            a["_fit"] = None
            a["_fit_error"] = str(exc)
        data["activities"].append(a)
    return data


# Markdown report (English, for ChatGPT)

def day_section(data, start, end, partial_note):
    rows = []
    day = start
    while day <= end:
        key = day.isoformat()
        d, s, h = data["daily"].get(key), data["sleep"].get(key), data["hrv"].get(key)
        acts = [a for a in data["activities"] if local_from_gmt(a["startTimeGMT"]).date() == day]
        d = d or {}
        steps = d.get("movement", {}).get("steps", {})
        im = d.get("intensityMinutes", {})
        bb = d.get("bodyBattery", {})
        base = (h or {}).get("baseline") or {}
        sleep_scores = (s or {}).get("sleepScores", {})
        if not d.get("includesWellnessData"):
            status = "missing wellness data"
        elif day == end and partial_note:
            status = "partial day"
        else:
            status = "complete"
        rows.append([
            f"{day:%a %Y-%m-%d}",
            status,
            val(steps.get("value")),
            val(d.get("calories", {}).get("burnedActive"), "kcal"),
            f"{val(im.get('moderate'))} / {val(im.get('vigorous'))}" if im else NA,
            val(d.get("heartRate", {}).get("restingValue"), "bpm"),
            val(d.get("stress", {}).get("avgLevel")),
            f"{val(bb.get('minValue'))}–{val(bb.get('maxValue'))}" if bb else NA,
            hms(s.get("sleepTimeSeconds")) if s else NA,
            val(sleep_scores.get("overall", {}).get("value")),
            hms(s.get("deepSleepSeconds")) if s else NA,
            hms(s.get("remSleepSeconds")) if s else NA,
            val((h or {}).get("lastNightAvg"), "ms"),
            val((h or {}).get("weeklyAvg"), "ms"),
            f"{val(base.get('balancedLow'))}–{val(base.get('balancedUpper'))} ms" if base else NA,
            (h or {}).get("status") or NA,
            ", ".join(f"{local_from_gmt(a['startTimeGMT']):%H:%M} {a.get('activityName')}" for a in acts)
            or "no recorded activity",
        ])
        day += dt.timedelta(days=1)
    header = ["Date", "Data", "Steps", "Active calories", "Intensity min moderate / vigorous",
              "Resting HR", "Stress avg (0-100)", "Body Battery min–max", "Sleep", "Sleep score",
              "Deep sleep", "REM sleep", "HRV night avg", "HRV 7-day avg", "HRV balanced baseline",
              "HRV status", "Activities"]
    return md_table(header, rows)


def week_section(data, start, end):
    days = []
    day = start
    while day <= end:
        days.append(day.isoformat())
        day += dt.timedelta(days=1)
    daily = [data["daily"].get(k, {}) for k in days]
    sleep = [data["sleep"].get(k, {}) for k in days]
    hrv = [data["hrv"].get(k, {}) for k in days]
    steps = [d.get("movement", {}).get("steps", {}).get("value") for d in daily]
    moderate = sum(d.get("intensityMinutes", {}).get("moderate") or 0 for d in daily)
    vigorous = sum(d.get("intensityMinutes", {}).get("vigorous") or 0 for d in daily)
    loads = [a.get("activityTrainingLoad") for a in data["activities"]
             if start <= local_from_gmt(a["startTimeGMT"]).date() <= end]
    last_hrv = next((h for h in reversed(hrv) if h), {})
    base = last_hrv.get("baseline") or {}
    return [
        "Calculated by this exporter from the daily Garmin values above:",
        "",
        f"- Steps: {thousands(sum(v for v in steps if v))} total, {thousands(round(avg(steps) or 0))} per day",
        f"- Intensity minutes: {moderate} moderate + {vigorous} vigorous = "
        f"{moderate + 2 * vigorous} (moderate + 2 × vigorous, Garmin's weekly-goal formula)",
        f"- Resting HR avg: {val(avg([d.get('heartRate', {}).get('restingValue') for d in daily]), 'bpm')}",
        f"- Stress avg: {val(avg([d.get('stress', {}).get('avgLevel') for d in daily]))}",
        f"- Sleep avg: {hms(avg([s.get('sleepTimeSeconds') for s in sleep]))}, "
        f"score avg {val(avg([s.get('sleepScores', {}).get('overall', {}).get('value') for s in sleep]))}",
        f"- HRV night avg: {val(avg([h.get('lastNightAvg') for h in hrv]), 'ms')}",
        f"- Sum of activity exercise loads: {val(round(sum(l for l in loads if l)) if loads else None)} "
        f"({len(loads)} activities). This is not Garmin's Acute Load.",
        "",
        f"Garmin-provided, as of {last_hrv.get('calendarDate', NA)}:",
        "",
        f"- HRV 7-day avg {val(last_hrv.get('weeklyAvg'), 'ms')}, status {last_hrv.get('status') or NA}, "
        f"balanced baseline {val(base.get('balancedLow'))}–{val(base.get('balancedUpper'), 'ms')}",
    ]


def zones_section(zones, title):
    lines = [f"## {title}", "", f"Retrieved {dt.datetime.now(TZ):%Y-%m-%dT%H:%M%z} from Garmin Connect.", ""]
    rows = []
    for z in zones:
        floors = [z.get(f"zone{i}Floor") for i in range(1, 6)]
        ranges = [f"{floors[i]}–{(floors[i + 1] - 1) if i < 4 else z.get('maxHeartRateUsed')}" for i in range(5)]
        rows.append([z.get("sport", NA), z.get("trainingMethod", NA), val(z.get("maxHeartRateUsed"), "bpm"),
                     val(z.get("restingHeartRateUsed"), "bpm"), val(z.get("lactateThresholdHeartRateUsed"), "bpm")]
                    + [f"{r} bpm" for r in ranges])
    lines += md_table(["Sport", "Method", "Max HR", "Resting HR used", "LTHR used",
                       "Z1", "Z2", "Z3", "Z4", "Z5"], rows)
    lines += ["", "Methods: HR_RESERVE = % of heart-rate reserve, MAX_HR = % of max HR, "
              "LACTATE_THRESHOLD = % of LTHR. Each activity below lists the zones its device used."]
    return lines


def activity_section(a):
    fit = a.get("_fit") or {}
    session = (fit.get("session_mesgs") or [{}])[0]
    start = local_from_gmt(a["startTimeGMT"])
    type_key = a.get("activityType", {}).get("typeKey", NA)
    lines = [f"### {start:%a %Y-%m-%d %H:%M} · {a.get('activityName') or type_key}", ""]
    if a.get("_fit_error"):
        lines += [f"FIT file unavailable: {a['_fit_error']}", ""]
    feel = session.get("workout_feel")
    rpe = session.get("workout_rpe")
    distance = a.get("distance")
    lines += [
        f"- Activity ID: {a['activityId']}",
        f"- Type: {type_key}",
        f"- Start: {start.isoformat()}",
        f"- Elapsed time: {hms(a.get('elapsedDuration'))}",
        f"- Timer time: {hms(session.get('total_timer_time') or a.get('duration'))}",
        f"- Moving time: {hms(a.get('movingDuration')) if a.get('movingDuration') else NA}",
        f"- Distance: {val(round(distance, 2) if distance else None, 'm')}",
        f"- HR avg / max: {val(a.get('averageHR'), 'bpm', '{:.0f}')} / {val(a.get('maxHR'), 'bpm', '{:.0f}')}",
        f"- Calories: {val(a.get('calories'), 'kcal', '{:.0f}')}",
        f"- Training Effect aerobic / anaerobic: {val(a.get('aerobicTrainingEffect'), fmt='{:.1f}')} / "
        f"{val(a.get('anaerobicTrainingEffect'), fmt='{:.1f}')} ({a.get('trainingEffectLabel') or NA})",
        f"- Exercise load: {val(a.get('activityTrainingLoad'), fmt='{:.1f}')}",
        f"- Self evaluation: feel {f'{FEEL.get(feel, feel)} ({feel}/100)' if feel is not None else 'not recorded'}, "
        f"perceived effort {f'{rpe / 10:g}/10 (RPE)' if rpe is not None else 'not recorded'}",
    ]
    if a.get("description"):
        lines.append(f"- Notes: {a['description']}")
    lines.append("")
    lines += hr_zones(fit)
    if type_key == "lap_swimming" and fit:
        lines += swim_section(fit, session)
    return lines


def hr_zones(fit):
    session_zone = next((z for z in fit.get("time_in_zone_mesgs", []) if z.get("reference_mesg") == "session"), None)
    if not session_zone or not session_zone.get("time_in_hr_zone"):
        return ["Time in HR zones: not in the FIT file.", ""]
    times = session_zone["time_in_hr_zone"]
    high = session_zone.get("hr_zone_high_boundary") or []
    total = sum(t for t in times if t)
    rows = []
    names = ["Below Z1"] + [f"Z{i}" for i in range(1, 6)] + ["Above Z5"]
    for i, seconds in enumerate(times):
        if i == 0:
            bounds = f"< {high[0] + 1}" if high else NA
        elif i < len(high):
            bounds = f"{high[i - 1] + 1}–{high[i]}"
        else:
            bounds = f"> {high[-1]}" if high else NA
        if i in (0, len(times) - 1) and not seconds:
            continue
        rows.append([names[i] if i < len(names) else f"#{i}", f"{bounds} bpm", hms(seconds),
                     f"{seconds / total * 100:.1f} %" if total else NA])
    method = session_zone.get("hr_calc_type", NA)
    return ([f"Time in HR zones (device zones: {method}, max {val(session_zone.get('max_heart_rate'), 'bpm')}, "
             f"resting {val(session_zone.get('resting_heart_rate'), 'bpm')}, "
             f"LTHR {val(session_zone.get('threshold_heart_rate'), 'bpm')}; % calculated by exporter):", ""]
            + md_table(["Zone", "Range", "Time", "Share"], rows) + [""])


def swim_section(fit, session):
    laps = fit.get("lap_mesgs", [])
    lengths = fit.get("length_mesgs", [])
    rest = sum(l.get("total_timer_time") or 0 for l in laps if not l.get("num_active_lengths"))
    lines = [
        "Pool swim:",
        "",
        f"- Pool length: {val(session.get('pool_length'))} {('m' if session.get('pool_length_unit') == 'metric' else session.get('pool_length_unit') or '')}",
        f"- Total distance: {val(session.get('total_distance'), 'm', '{:.0f}')}",
        f"- Swimming time (Garmin active time): {hms(session.get('active_time'))}",
        f"- Rest time: {hms(rest)} (sum of the rest intervals Garmin recorded, added up by exporter)",
        f"- Active lengths: {val(session.get('num_active_lengths'))}, avg strokes per length "
        f"{val(session.get('avg_strokes_per_length'))}, avg SWOLF {val(session.get('avg_swolf'))}",
        "- Stroke count: Garmin counts one stroke per arm cycle (each time the watch arm completes a stroke).",
        "",
        "Intervals (Garmin laps):",
        "",
    ]
    rows = []
    for i, lap in enumerate(laps, 1):
        active = lap.get("num_active_lengths") or 0
        stroke = lap.get("swim_stroke")
        kind = "rest" if not active else ("drill" if stroke == "drill" else "swim")
        rows.append([i, kind, val(lap.get("total_distance"), "m", "{:.0f}") if active else "0 m",
                     hms(lap.get("total_timer_time")), stroke or ("–" if kind == "rest" else NA),
                     val(lap.get("total_strokes")) if active else "–",
                     val(lap.get("avg_swolf")) if active else "–",
                     f"{val(lap.get('avg_heart_rate'))}/{val(lap.get('max_heart_rate'))} bpm"
                     if lap.get("avg_heart_rate") else NA])
    lines += md_table(["#", "Type", "Distance", "Duration", "Stroke", "Strokes", "SWOLF", "HR avg/max"], rows)
    lines += ["", "Pool lengths (Garmin length records; idle = rest):", ""]
    owner = {}
    for i, lap in enumerate(laps, 1):
        first, count = lap.get("first_length_index"), lap.get("num_lengths") or 0
        if first is not None:
            for j in range(first, first + count):
                owner[j] = i
    rows = []
    for j, length in enumerate(lengths):
        if length.get("length_type") != "active":
            continue
        rows.append([j + 1, owner.get(j, NA), length.get("swim_stroke") or NA, hms(length.get("total_timer_time")),
                     val(length.get("total_strokes")), val(length.get("avg_swimming_cadence"), "strokes/min")])
    lines += md_table(["Length", "Interval", "Stroke", "Time", "Strokes", "Cadence"], rows)
    return lines + [""]


def build_markdown(data, start, end, now, initial, zones_changed):
    year, week, _ = end.isocalendar()
    partial = now.date() == end and now.hour < 24
    lines = [
        f"# Garmin training report {year}-W{week:02d}",
        "",
        f"- Period: {start} to {end} ({(end - start).days + 1} days, Monday to Sunday weeks, Europe/Kyiv)",
        f"- Generated: {now.isoformat(timespec='minutes')}",
    ]
    if initial:
        lines.append("- First extended report: covers the last 28 days.")
    if partial:
        lines.append(f"- {end:%A} is incomplete: data up to {now:%H:%M}. Sleep for that date is the night before.")
    lines += [
        "- Missing values are N/A, never 0. Garmin-provided values and exporter calculations are labeled.",
        "- Units: durations hh:mm:ss, distance in meters, HR in bpm, HRV in ms.",
        "",
        "## Days",
        "",
    ]
    lines += day_section(data, start, end, partial)
    weeks = []
    s = start
    while s <= end:
        weeks.append((s, min(s + dt.timedelta(days=6), end)))
        s += dt.timedelta(days=7)
    for ws, we in weeks:
        y, w, _ = ws.isocalendar()
        lines += ["", f"## Week {y}-W{w:02d} ({ws} to {we})", ""] + week_section(data, ws, we)
    lines += ["", "## Training load and readiness", "",
              "Not available. Requested but not retrievable:", ""]
    lines += [f"- {name}: {reason}" for name, reason in UNAVAILABLE]
    lines += ["", UNAVAILABLE_NOTE, ""]
    if initial or zones_changed:
        lines += zones_section(data["zones"], "Heart-rate zone settings" + (" (changed)" if zones_changed else "")) + [""]
    lines += ["## Activities", ""]
    if not data["activities"]:
        lines += ["No recorded activity in this period.", ""]
    for a in data["activities"]:
        lines += activity_section(a)
    return f"garmin-{year}-W{week:02d}.md", "\n".join(lines).rstrip() + "\n"


# Telegram digest (Ukrainian, HTML)

def build_digest(data, start, end, now, initial):
    year, week, _ = end.isocalendar()
    week_start = end - dt.timedelta(days=6)
    days = [week_start + dt.timedelta(days=i) for i in range(7)]
    daily = [data["daily"].get(d.isoformat(), {}) for d in days]
    sleep = [data["sleep"].get(d.isoformat(), {}) for d in days]
    hrv = [data["hrv"].get(d.isoformat(), {}) for d in days]
    acts = [a for a in data["activities"] if week_start <= local_from_gmt(a["startTimeGMT"]).date() <= end]
    steps = [d.get("movement", {}).get("steps", {}).get("value") for d in daily]
    moderate = sum(d.get("intensityMinutes", {}).get("moderate") or 0 for d in daily)
    vigorous = sum(d.get("intensityMinutes", {}).get("vigorous") or 0 for d in daily)
    last_hrv = next((h for h in reversed(hrv) if h), {})
    base = last_hrv.get("baseline") or {}
    load = sum(a.get("activityTrainingLoad") or 0 for a in acts)

    def num(v):
        return "–" if v is None else f"{v:.0f}"

    out = [f"<b>Garmin · тиждень {week}</b>  {week_start:%d.%m}–{end:%d.%m}"]
    if now.date() == end:
        out.append(f"<i>Неділя до {now:%H:%M}</i>")
    out += [
        "",
        f"👟 Кроки: <b>{thousands(sum(v for v in steps if v))}</b> ({thousands(round(avg(steps) or 0))}/день)",
        f"😴 Сон: <b>{hm(avg([s.get('sleepTimeSeconds') for s in sleep]))}</b>, "
        f"оцінка {num(avg([s.get('sleepScores', {}).get('overall', {}).get('value') for s in sleep]))}",
        f"💓 HRV: <b>{num(avg([h.get('lastNightAvg') for h in hrv]))} мс</b>, "
        f"база {num(base.get('balancedLow'))}–{num(base.get('balancedUpper'))}, {last_hrv.get('status', '–')}",
        f"❤️ Пульс спокою: <b>{num(avg([d.get('heartRate', {}).get('restingValue') for d in daily]))}</b>",
        f"🧠 Стрес: <b>{num(avg([d.get('stress', {}).get('avgLevel') for d in daily]))}</b>",
        f"🔥 Інтенсивність: <b>{moderate + 2 * vigorous} хв</b> · навантаження {load:.0f} ({len(acts)} трен.)",
        "",
        "<b>Дні</b>",
        "<pre>" + DAY_ROW.format("День", "Кроки", "Сон", "Оц", "HRV", "ЧСС"),
    ]
    for d, dd, s, h in zip(days, daily, sleep, hrv):
        if d > end:
            break
        out.append(DAY_ROW.format(
            f"{DAYS_UK[d.weekday()]} {d:%d}",
            thousands(dd.get("movement", {}).get("steps", {}).get("value")),
            hm(s.get("sleepTimeSeconds")) if s else "–",
            num(s.get("sleepScores", {}).get("overall", {}).get("value")) if s else "–",
            num(h.get("lastNightAvg")) if h else "–",
            num(dd.get("heartRate", {}).get("restingValue")),
        ))
    out[-1] += "</pre>"
    out += ["", "<b>Тренування</b>"]
    if not acts:
        out.append("Немає записаних тренувань.")
    for a in acts:
        start_local = local_from_gmt(a["startTimeGMT"])
        session = ((a.get("_fit") or {}).get("session_mesgs") or [{}])[0]
        type_key = a.get("activityType", {}).get("typeKey", "")
        parts = [f"{DAYS_UK[start_local.weekday()]} {start_local:%H:%M}",
                 f"<b>{html.escape(TYPES_UK.get(type_key, a.get('activityName') or type_key))}</b>"]
        if a.get("distance"):
            parts.append(f"{a['distance']:.0f} м")
        parts.append(hm(a.get("duration")))
        if a.get("averageHR"):
            parts.append(f"ЧСС {a['averageHR']:.0f}/{(a.get('maxHR') or 0):.0f}")
        if a.get("aerobicTrainingEffect") is not None:
            parts.append(f"TE {a['aerobicTrainingEffect']:.1f}/{(a.get('anaerobicTrainingEffect') or 0):.1f}")
        if a.get("activityTrainingLoad"):
            parts.append(f"навант. {a['activityTrainingLoad']:.0f}")
        if session.get("workout_feel") is not None:
            parts.append(FEEL_UK.get(session["workout_feel"], str(session["workout_feel"])))
        if session.get("workout_rpe") is not None:
            parts.append(f"RPE {session['workout_rpe'] / 10:g}")
        out.append("• " + " · ".join(parts))
    out += ["", "📎 Повний звіт для ChatGPT у файлі нижче" + (" (28 днів)." if initial else ".")]
    return "\n".join(out)


# delivery and schedule

def telegram(method, fields, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if files:
        boundary = "garminreport"
        body = b""
        for name, value in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, (filename, content) in files.items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: text/markdown\r\n\r\n").encode() + content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def run_once(print_only=False, force_initial=False):
    now = dt.datetime.now(TZ)
    end = now.date() if now.weekday() == 6 else now.date() - dt.timedelta(days=now.weekday() + 1)
    state = load_state()
    initial = force_initial or not state.get("initial_done")
    start = end - dt.timedelta(days=27 if initial else 6)
    data = fetch(start, end)
    zones_hash = hashlib.sha256(json.dumps(data["zones"], sort_keys=True).encode()).hexdigest()
    zones_changed = bool(state.get("zones_hash")) and state["zones_hash"] != zones_hash
    filename, markdown = build_markdown(data, start, end, now, initial, zones_changed)
    digest = build_digest(data, start, end, now, initial)
    if print_only:
        print(markdown)
        print("----- Telegram -----")
        print(digest)
        return
    telegram("sendMessage", {"chat_id": CHAT_ID, "text": digest, "parse_mode": "HTML"})
    telegram("sendDocument", {"chat_id": CHAT_ID}, {"document": (filename, markdown.encode())})
    state.update(initial_done=True, zones_hash=zones_hash, last_sent=now.isoformat())
    save_state(state)
    print(f"{now:%Y-%m-%d %H:%M} sent {filename} ({start} to {end})", flush=True)


def seconds_until_next_run():
    hour, minute = map(int, REPORT_TIME.split(":"))
    now = dt.datetime.now(TZ)
    target = (now + dt.timedelta(days=REPORT_DAY - now.weekday())).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=7)
    return (target - now).total_seconds(), target


def main():
    if "--print" in sys.argv:
        return run_once(print_only=True, force_initial="--initial" in sys.argv)
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    if "--now" in sys.argv or "--initial" in sys.argv:
        return run_once(force_initial="--initial" in sys.argv)
    while True:
        wait, target = seconds_until_next_run()
        print(f"next report {target:%Y-%m-%d %H:%M}", flush=True)
        time.sleep(wait)
        for attempt in range(3):
            try:
                run_once()
                break
            except Exception as exc:  # retry within the evening, then wait for next week
                print(f"report failed (attempt {attempt + 1}): {exc}", flush=True)
                time.sleep(1200)


if __name__ == "__main__":
    main()
