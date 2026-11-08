"""Weekly Garmin report to Telegram, for training analysis in ChatGPT.

Every Sunday at REPORT_TIME (Europe/Kyiv) it pulls Monday to now from the garmin-mcp
server plus each activity's FIT file, then sends a short Ukrainian digest to Telegram
and the full English report as .md and .html files. The first run covers 28 days.

  python report.py           run forever on the weekly schedule
  python report.py --now     send this week's report once and exit
  python report.py --print   print the Markdown report and the digest, no Telegram
  python report.py --html F  also write the HTML report to file F (with --print)
  python report.py --initial force the 28-day history on a one-off run
"""

import datetime as dt
import hashlib
import html
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
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
# Uptime Kuma push monitor URL without query; empty disables the heartbeat
KUMA_PUSH_URL = os.environ.get("KUMA_PUSH_URL", "")
TZ = ZoneInfo("Europe/Kyiv")
NA = "N/A"
RHR_FLAG_BPM = 10  # flag a resting HR this far from the period median
FAST_LENGTH_S = 15  # flag 25 m lengths faster than this
LOW_STROKES = 3  # flag lengths with this many strokes or fewer
FIT_HALF_UNIT_S = 0.0005  # FIT times are whole milliseconds, so each value carries up to ±0.5 ms
PRECISION_NOTE = ("Calculations use original source precision before rounding. Displayed durations and activity "
                  "loads are rounded independently, so their sums may differ slightly from reported totals.")

# Probed by hand with this key; the exporter has no other path to these metrics.
UNAVAILABLE = [
    ("Training Readiness (morning score, timestamp)", "metrics-service /metrics/trainingreadiness/{date}", "404"),
    ("Recovery Time (remaining hours, timestamp)", "metrics-service /metrics/trainingreadiness/{date}", "404"),
    ("Acute Load, Chronic Load, Load Ratio, optimal range",
     "metrics-service /metrics/trainingstatus/aggregated/{date}", "404"),
    ("Training Status", "metrics-service /metrics/trainingstatus/aggregated/{date}", "404"),
    ("Load Focus and targets", "metrics-service /metrics/trainingstatus/aggregated/{date}", "404"),
    ("Confirmed rest day", "no endpoint known", "not tested"),
    ("User-confirmed stroke type", "not present in activity summary or FIT file", "n/a"),
]
UNAVAILABLE_CHECKED = "2026-11-02T11:05+02:00"

FEEL = {0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong"}
FEEL_UK = {0: "дуже слабко", 25: "слабко", 50: "нормально", 75: "добре", 100: "дуже добре"}
DAYS_UK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
TYPES_UK = {"lap_swimming": "Басейн", "treadmill_running": "Доріжка", "running": "Біг",
            "elliptical": "Еліпс", "strength_training": "Силове", "cycling": "Вело",
            "indoor_cycling": "Велотренажер", "walking": "Ходьба"}

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Mcp:
    def __init__(self):
        self.session = None
        self.next_id = 1
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "garmin-weekly-report", "version": "3"},
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
    sign = "-" if seconds < 0 else ""
    seconds = round(abs(seconds))
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


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


def dates(start, end):
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def activity_date(a):
    return local_from_gmt(a["startTimeGMT"]).date()


def load_sum(acts):
    loads = [a["activityTrainingLoad"] for a in acts if a.get("activityTrainingLoad") is not None]
    return (round(sum(loads), 1) if loads else None), len(loads)


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


def session_of(a):
    return ((a.get("_fit") or {}).get("session_mesgs") or [{}])[0]


def session_zone(a):
    return next((z for z in (a.get("_fit") or {}).get("time_in_zone_mesgs", [])
                 if z.get("reference_mesg") == "session"), None)


def swim_times(a):
    """Timer, swimming and rest/idle seconds (unrounded), the signed residual and its rounding tolerance."""
    s = session_of(a)
    laps = (a.get("_fit") or {}).get("lap_mesgs", [])
    rest_laps = [l.get("total_timer_time") or 0 for l in laps if not l.get("num_active_lengths")]
    rest = sum(rest_laps)
    timer, swim = s.get("total_timer_time"), s.get("active_time")
    # rounding to 3 decimals only drops float noise; + 0.0 drops -0.0
    diff = None if timer is None or swim is None else round(timer - swim - rest, 3) + 0.0
    tolerance = FIT_HALF_UNIT_S * (len(rest_laps) + 2)
    return timer, swim, rest, diff, tolerance


def residual(diff, tolerance):
    if diff is None:
        return NA, NA
    ok = abs(diff) <= tolerance + 1e-9
    return f"{diff:+.3f} s", f"{'reconciled' if ok else 'not reconciled'} (tolerance ±{tolerance:.4f} s)"


def rhr_flags(data, start, end):
    values = {d: data["daily"].get(d.isoformat(), {}).get("heartRate", {}).get("restingValue")
              for d in dates(start, end)}
    flags = {}
    for day, v in values.items():
        others = [x for d, x in values.items() if d != day and x is not None]
        if v is not None and len(others) >= 3 and abs(v - statistics.median(others)) >= RHR_FLAG_BPM:
            flags[day] = statistics.median(others)
    return flags


def swim_quality(a):
    fit = a.get("_fit") or {}
    session = session_of(a)
    pool = session.get("pool_length")
    active = [l for l in fit.get("length_mesgs", []) if l.get("length_type") == "active"]
    strokes = {}
    for l in active:
        strokes[l.get("swim_stroke") or NA] = strokes.get(l.get("swim_stroke") or NA, 0) + 1
    fast = [l for l in active if pool and pool <= 25 and (l.get("total_timer_time") or 99) < FAST_LENGTH_S]
    low = [l for l in active if l.get("total_strokes") is not None and l["total_strokes"] <= LOW_STROKES]
    drills = sum(1 for l in fit.get("lap_mesgs", []) if l.get("swim_stroke") == "drill")
    return active, strokes, fast, low, drills


# Markdown report (English, for ChatGPT)

def day_section(data, start, end, partial, flags):
    rows = []
    for day in dates(start, end):
        key = day.isoformat()
        d, s, h = data["daily"].get(key) or {}, data["sleep"].get(key), data["hrv"].get(key)
        acts = [a for a in data["activities"] if activity_date(a) == day]
        steps = d.get("movement", {}).get("steps", {})
        im = d.get("intensityMinutes", {})
        bb = d.get("bodyBattery", {})
        base = (h or {}).get("baseline") or {}
        rhr = d.get("heartRate", {}).get("restingValue")
        if not d.get("includesWellnessData"):
            status = "missing wellness data"
        elif day == end and partial:
            status = "partial day"
        else:
            status = "complete"
        rows.append([
            f"{day:%a %Y-%m-%d}",
            status,
            val(steps.get("value")),
            val(d.get("calories", {}).get("burnedActive"), "kcal"),
            f"{val(im.get('moderate'))} / {val(im.get('vigorous'))}" if im else NA,
            val(rhr, "bpm") + (" ⚠ flagged" if day in flags else ""),
            val(d.get("stress", {}).get("avgLevel")),
            f"{val(bb.get('minValue'))}–{val(bb.get('maxValue'))}" if bb else NA,
            hms(s.get("sleepTimeSeconds")) if s else NA,
            val((s or {}).get("sleepScores", {}).get("overall", {}).get("value")),
            hms(s.get("deepSleepSeconds")) if s else NA,
            hms(s.get("remSleepSeconds")) if s else NA,
            val((h or {}).get("lastNightAvg"), "ms"),
            val((h or {}).get("weeklyAvg"), "ms"),
            f"{val(base.get('balancedLow'))}–{val(base.get('balancedUpper'))} ms" if base else NA,
            (h or {}).get("status") or NA,
            ", ".join(f"{local_from_gmt(a['startTimeGMT']):%H:%M} {a.get('activityName')}" for a in acts)
            or "no recorded activity",
        ])
    header = ["Date", "Data", "Steps", "Active calories", "Intensity min moderate / vigorous",
              "Resting HR", "Stress avg (0-100)", "Body Battery min–max", "Sleep", "Sleep score",
              "Deep sleep", "REM sleep", "HRV night avg", "HRV 7-day avg", "HRV balanced baseline",
              "HRV status", "Activities"]
    return md_table(header, rows)


def week_section(data, start, end, flags):
    keys = [d.isoformat() for d in dates(start, end)]
    daily = [data["daily"].get(k, {}) for k in keys]
    sleep = [data["sleep"].get(k, {}) for k in keys]
    hrv = [data["hrv"].get(k, {}) for k in keys]
    steps = [d.get("movement", {}).get("steps", {}).get("value") for d in daily]
    moderate = sum(d.get("intensityMinutes", {}).get("moderate") or 0 for d in daily)
    vigorous = sum(d.get("intensityMinutes", {}).get("vigorous") or 0 for d in daily)
    rhr = {d: data["daily"].get(d.isoformat(), {}).get("heartRate", {}).get("restingValue") for d in dates(start, end)}
    acts = [a for a in data["activities"] if start <= activity_date(a) <= end]
    loads, n_loads = load_sum(acts)
    swims = [swim_times(a) for a in acts if a.get("activityType", {}).get("typeKey") == "lap_swimming" and a.get("_fit")]
    last_hrv = next((h for h in reversed(hrv) if h), {})
    base = last_hrv.get("baseline") or {}
    lines = [
        "Calculated by this exporter from the daily Garmin values above:",
        "",
        f"- Steps: {thousands(sum(v for v in steps if v))} total, {thousands(round(avg(steps) or 0))} per day",
        f"- Intensity minutes: {moderate} moderate + {vigorous} vigorous = "
        f"{moderate + 2 * vigorous} (moderate + 2 × vigorous, Garmin's weekly-goal formula)",
        f"- Resting HR mean, all reported values: {val(avg(rhr.values()), 'bpm')}",
    ]
    flagged = [d for d in rhr if d in flags]
    if flagged:
        kept = [v for d, v in rhr.items() if d not in flags]
        lines.append(f"- Resting HR mean without flagged {', '.join(f'{d:%m-%d}' for d in flagged)} "
                     f"(diagnostic sensitivity check, not a corrected Garmin value): {val(avg(kept), 'bpm')}")
    lines += [
        f"- Stress avg: {val(avg([d.get('stress', {}).get('avgLevel') for d in daily]))}",
        f"- Sleep avg: {hms(avg([s.get('sleepTimeSeconds') for s in sleep]))}, "
        f"score avg {val(avg([s.get('sleepScores', {}).get('overall', {}).get('value') for s in sleep]))}",
        f"- HRV night avg: {val(avg([h.get('lastNightAvg') for h in hrv]), 'ms')}",
        f"- Sum of activity exercise loads: {val(loads, fmt='{:.1f}')} ({n_loads} activities, summed before "
        f"rounding). This is not Garmin's Acute Load.",
    ]
    if swims:
        timer, swim, rest = (sum(x[i] or 0 for x in swims) for i in range(3))
        shown, status = residual(round(timer - swim - rest, 3) + 0.0, sum(x[4] for x in swims))
        lines.append(f"- Pool swims ({len(swims)}): timer {hms(timer)}, Garmin-detected swimming time {hms(swim)}, "
                     f"Garmin-recorded rest/idle {hms(rest)}, residual {shown}, {status}; "
                     f"totals summed from unrounded values")
    lines += [
        "",
        f"Garmin-provided, as of {last_hrv.get('calendarDate', NA)}:",
        "",
        f"- HRV 7-day avg {val(last_hrv.get('weeklyAvg'), 'ms')}, status {last_hrv.get('status') or NA}, "
        f"balanced baseline {val(base.get('balancedLow'))}–{val(base.get('balancedUpper'), 'ms')}",
    ]
    return lines


def zone_configs(data):
    """Distinct zone settings recorded in the activities' FIT files."""
    configs = {}
    for a in data["activities"]:
        z = session_zone(a)
        if not z:
            continue
        key = (tuple(z.get("hr_zone_high_boundary") or ()), z.get("hr_calc_type"), z.get("max_heart_rate"),
               z.get("resting_heart_rate"), z.get("threshold_heart_rate"))
        configs.setdefault(key, []).append(a)
    return configs


def quality_section(data, start, end, flags):
    lines = ["## Data quality", ""]

    lines += ["### Heart-rate zones", ""]
    profile = next((z for z in data["zones"] if z.get("sport") == "DEFAULT"), (data["zones"] or [{}])[0])
    floors = [profile.get(f"zone{i}Floor") for i in range(1, 6)]
    lines += [
        f"Current profile (Garmin Connect heart-rate zone settings, retrieved {dt.datetime.now(TZ):%Y-%m-%dT%H:%M%z}): "
        f"trainingMethod={profile.get('trainingMethod')}, maxHeartRateUsed={profile.get('maxHeartRateUsed')}, "
        f"restingHeartRateUsed={profile.get('restingHeartRateUsed')}, "
        f"lactateThresholdHeartRateUsed={profile.get('lactateThresholdHeartRateUsed')}, "
        f"zone1Floor..zone5Floor={floors}. These are current settings, not proof of what older activities used.",
        "",
        "Recorded in the activity FIT files (message time_in_zone, reference_mesg=session):",
        "",
    ]
    for (bounds, method, max_hr, rest_hr, lthr), acts in zone_configs(data).items():
        lines.append(f"- {len(acts)} activities ({activity_date(acts[0])} to {activity_date(acts[-1])}): "
                     f"hr_calc_type={method}, max_heart_rate={max_hr}, resting_heart_rate={rest_hr}, "
                     f"threshold_heart_rate={lthr}, hr_zone_high_boundary={list(bounds)}")
        if list(bounds[:5]) == floors and max_hr == profile.get("maxHeartRateUsed"):
            lines.append("  - The first five raw values equal the profile's zone1Floor..zone5Floor and the last equals "
                         "max HR, so each value reads as the lower bound of the next zone. The FIT profile does not "
                         "state which end is inclusive; tables below show the raw values without any +1 adjustment.")
        if method == "percent_hrr" and max_hr and rest_hr:
            pct = [f"{(b - rest_hr) / (max_hr - rest_hr) * 100:.1f}" for b in bounds[:5]]
            lines.append(f"  - Diagnostic: as % of heart-rate reserve these floors are {pct}. They are not round "
                         "percentages, so the zones may be custom or rounded by Garmin. Not recalculated.")
    lines += ["- Each activity's zone table uses only that activity's own FIT values.", ""]

    lines += ["### Resting HR", ""]
    if not flags:
        lines.append(f"No daily resting HR differs by {RHR_FLAG_BPM} bpm or more from the period median.")
    for day, median in flags.items():
        hr = data["daily"].get(day.isoformat(), {}).get("heartRate", {})
        lines.append(f"- {day}: heartRate.restingValue={hr.get('restingValue')} bpm (daily summary field for "
                     f"resting HR, not the minimum), heartRate.minValue={hr.get('minValue')}, "
                     f"heartRate.maxValue={hr.get('maxValue')}. Median of the other days {median:g} bpm. "
                     "Kept as reported; verify in Garmin Connect (possible sensor artifact).")
    lines.append("")

    swims = [a for a in data["activities"] if a.get("activityType", {}).get("typeKey") == "lap_swimming" and a.get("_fit")]
    if swims:
        lines += ["### Pool swims", "",
                  "Stroke types are Garmin-detected; no user-confirmed stroke type exists in the source. "
                  "Rest/idle records are what Garmin did not classify as swimming: they can be real rest, coaching, "
                  "unrecognised drills or other movement. Nothing below changes the records.", ""]
        lines += ["Time reconciliation: residual_seconds = timer_seconds − swimming_seconds − rest_idle_seconds, "
                  "from the unrounded FIT values (session total_timer_time, session active_time, and the sum of "
                  "total_timer_time over laps with no active lengths). FIT stores times in milliseconds, so seconds "
                  "and the signed residual are shown with 3 decimals. Each FIT value is rounded to 1 ms, so it can be "
                  "off by up to 0.5 ms; the tolerance is 0.5 ms × (number of rest/idle laps summed + 2). Status is "
                  "\"reconciled\" when |residual| is within it; the actual residual is shown either way. The hh:mm:ss "
                  "columns are rounded independently, so they can appear not to add up (for example 1 s) while "
                  "the raw residual is zero.", ""]
        rows = []
        for a in swims:
            timer, swim, rest, diff, tolerance = swim_times(a)
            shown, status = residual(diff, tolerance)
            rows.append([f"{activity_date(a)} ({a['activityId']})", val(timer, "s", "{:.3f}"), val(swim, "s", "{:.3f}"),
                         val(rest, "s", "{:.3f}"), shown, status, hms(timer), hms(swim), hms(rest)])
        lines += md_table(["Swim", "Timer", "Garmin-detected swimming", "Garmin-recorded rest/idle", "Residual",
                           "Status", "Timer (rounded)", "Swimming (rounded)", "Rest/idle (rounded)"], rows)
        lines += ["", "Detection quality (review flags, records unchanged):", ""]
        rows = []
        for a in swims:
            active, strokes, fast, low, drills = swim_quality(a)
            mix = ", ".join(f"{k} {v}" for k, v in sorted(strokes.items(), key=lambda kv: -kv[1]))
            rows.append([f"{activity_date(a)} ({a['activityId']})", len(active), mix, len(fast), len(low), drills])
        lines += md_table(["Swim", "Active lengths", "Garmin-detected stroke types",
                           f"Lengths under {FAST_LENGTH_S} s", f"Lengths with ≤{LOW_STROKES} strokes",
                           "Drill intervals"], rows)
        lines += ["", "Fast lengths and low stroke counts limit confidence in pace and technique comparisons; they "
                  "do not prove a detection error. Lower SWOLF or faster detected pace is not confirmed technique "
                  "improvement.", ""]

    lines += ["### Metrics that could not be retrieved", "",
              "These metrics could not be retrieved through the endpoints used by this exporter as of the "
              "verification date. They are N/A throughout the report. The cause of each 404 is unresolved.", ""]
    lines += md_table(["Metric", "Endpoint or service", "HTTP status", "Checked"],
                      [[m, f"`{e}`", s, UNAVAILABLE_CHECKED] for m, e, s in UNAVAILABLE])
    return lines + [""]


def zones_settings_section(zones, title):
    lines = [f"## {title}", "", f"Retrieved {dt.datetime.now(TZ):%Y-%m-%dT%H:%M%z} from the Garmin Connect "
             "heart-rate zone settings (current values; activities keep the zones recorded in their own files).", ""]
    rows = []
    for z in zones:
        floors = [z.get(f"zone{i}Floor") for i in range(1, 6)]
        rows.append([z.get("sport", NA), z.get("trainingMethod", NA), val(z.get("maxHeartRateUsed"), "bpm"),
                     val(z.get("restingHeartRateUsed"), "bpm"), val(z.get("lactateThresholdHeartRateUsed"), "bpm")]
                    + [val(f, "bpm") for f in floors])
    lines += md_table(["Sport", "trainingMethod", "Max HR", "Resting HR used", "LTHR used",
                       "zone1Floor", "zone2Floor", "zone3Floor", "zone4Floor", "zone5Floor"], rows)
    return lines + ["", "trainingMethod: HR_RESERVE = % of heart-rate reserve, MAX_HR = % of max HR, "
                    "LACTATE_THRESHOLD = % of LTHR.", ""]


def activity_section(a):
    fit = a.get("_fit") or {}
    session = session_of(a)
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
    lines += hr_zones(a)
    if type_key == "lap_swimming" and fit:
        lines += swim_section(a)
    return lines


def hr_zones(a):
    z = session_zone(a)
    if not z or not z.get("time_in_hr_zone"):
        return ["Time in HR zones: not in the FIT file.", ""]
    times = z["time_in_hr_zone"]
    bounds = z.get("hr_zone_high_boundary") or []
    total = sum(t for t in times if t)
    rows = []
    for i, seconds in enumerate(times):
        if i == 0:
            name, raw = "Below Z1", f"up to {bounds[0]}" if bounds else NA
        elif i < len(bounds):
            name, raw = f"Z{i}", f"{bounds[i - 1]} to {bounds[i]}"
        else:
            name, raw = "Above Z5", f"from {bounds[-1]}" if bounds else NA
        if i in (0, len(times) - 1) and not seconds:
            continue
        rows.append([name, f"{raw} bpm", hms(seconds), f"{seconds / total * 100:.1f} %" if total else NA])
    timer = session_of(a).get("total_timer_time")
    gap = None if timer is None else round(timer - total, 3) + 0.0
    return ([f"Time in HR zones (this activity's FIT values: hr_calc_type={z.get('hr_calc_type', NA)}, "
             f"max {val(z.get('max_heart_rate'), 'bpm')}, resting {val(z.get('resting_heart_rate'), 'bpm')}, "
             f"LTHR {val(z.get('threshold_heart_rate'), 'bpm')}; raw boundaries):", ""]
            + md_table(["Zone", "Raw FIT boundaries", "Time", "Share"], rows)
            + ["",
               f"- Activity timer time: {hms(timer)} ({val(timer, 's', '{:.3f}')})",
               f"- Sum of recorded zone durations (all time_in_hr_zone values, including below Z1 and above Z5): "
               f"{hms(total)} ({total:.3f} s)",
               f"- Timer minus recorded zone time: {hms(gap) if gap is not None else NA} "
               f"({val(gap, 's', '{:+.3f}')}). "
               + ("Timer and recorded zone durations match." if gap == 0 else "Cause not established by the source."),
               f"- Share = zone_duration_seconds / {total:.3f} s (sum of recorded zone durations) × 100, "
               "calculated by exporter",
               ""])


def swim_section(a):
    fit = a["_fit"]
    session = session_of(a)
    laps = fit.get("lap_mesgs", [])
    timer, swim, rest, diff, tolerance = swim_times(a)
    unit = "m" if session.get("pool_length_unit") == "metric" else session.get("pool_length_unit") or ""
    lines = [
        "Pool swim:",
        "",
        f"- Pool length: {val(session.get('pool_length'))} {unit}",
        f"- Total distance: {val(session.get('total_distance'), 'm', '{:.0f}')}",
        f"- Timer time: {hms(timer)}",
        f"- Garmin-detected swimming time: {hms(swim)}",
        f"- Garmin-recorded rest/idle time: {hms(rest)} (sum of the rest/idle intervals, added by exporter)",
        "- Residual (timer − swimming − rest/idle, unrounded): {}, {}".format(*residual(diff, tolerance)),
        f"- Active lengths: {val(session.get('num_active_lengths'))}, avg strokes per length "
        f"{val(session.get('avg_strokes_per_length'))}, avg SWOLF {val(session.get('avg_swolf'))}",
        "- Stroke count: Garmin counts one stroke per full arm cycle of the arm wearing the watch.",
        "- Individual pool lengths are in the appendix.",
        "",
        "Intervals (Garmin laps):",
        "",
    ]
    rows = []
    for i, lap in enumerate(laps, 1):
        active = lap.get("num_active_lengths") or 0
        stroke = lap.get("swim_stroke")
        kind = "rest/idle" if not active else ("drill" if stroke == "drill" else "swim")
        rows.append([i, kind, val(lap.get("total_distance"), "m", "{:.0f}") if active else "0 m",
                     hms(lap.get("total_timer_time")), stroke or ("–" if not active else NA),
                     val(lap.get("total_strokes")) if active else "–",
                     val(lap.get("avg_swolf")) if active else "–",
                     f"{val(lap.get('avg_heart_rate'))}/{val(lap.get('max_heart_rate'))} bpm"
                     if lap.get("avg_heart_rate") else NA])
    lines += md_table(["#", "Garmin record type", "Distance", "Duration", "Garmin-detected stroke", "Strokes",
                       "SWOLF", "HR avg/max"], rows)
    return lines + [""]


def lengths_appendix(activities):
    lines = ["## Appendix: pool lengths", "",
             "Garmin length records (active lengths only). Stroke types are Garmin-detected.", ""]
    for a in activities:
        fit = a.get("_fit") or {}
        if a.get("activityType", {}).get("typeKey") != "lap_swimming" or not fit:
            continue
        laps = fit.get("lap_mesgs", [])
        owner = {}
        for i, lap in enumerate(laps, 1):
            first, count = lap.get("first_length_index"), lap.get("num_lengths") or 0
            if first is not None:
                for j in range(first, first + count):
                    owner[j] = i
        rows = []
        for j, length in enumerate(fit.get("length_mesgs", [])):
            if length.get("length_type") != "active":
                continue
            rows.append([j + 1, owner.get(j, NA), length.get("swim_stroke") or NA, hms(length.get("total_timer_time")),
                         val(length.get("total_strokes")), val(length.get("avg_swimming_cadence"), "strokes/min")])
        lines += [f"### {local_from_gmt(a['startTimeGMT']):%a %Y-%m-%d %H:%M} · {a['activityId']}", ""]
        lines += md_table(["Length", "Interval", "Garmin-detected stroke", "Time", "Strokes", "Cadence"], rows)
        lines.append("")
    return lines


def build_markdown(data, start, end, now, initial, zones_changed):
    year, week, _ = end.isocalendar()
    partial = now.date() == end
    flags = rhr_flags(data, start, end)
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
        f"- {PRECISION_NOTE}",
        "",
        "## Days",
        "",
    ]
    lines += day_section(data, start, end, partial, flags)
    s = start
    while s <= end:
        we = min(s + dt.timedelta(days=6), end)
        y, w, _ = s.isocalendar()
        lines += ["", f"## Week {y}-W{w:02d} ({s} to {we})", ""] + week_section(data, s, we, flags)
        s += dt.timedelta(days=7)
    lines += ["", "## Activities", ""]
    if not data["activities"]:
        lines += ["No recorded activity in this period.", ""]
    for a in data["activities"]:
        lines += activity_section(a)
    lines += quality_section(data, start, end, flags)
    if initial or zones_changed:
        lines += zones_settings_section(data["zones"], "Heart-rate zone settings" + (" (changed)" if zones_changed else ""))
    lines += lengths_appendix(data["activities"])
    return f"garmin-{year}-W{week:02d}", "\n".join(lines).rstrip() + "\n"


# HTML version of the same Markdown (only the subset this script writes)

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--head:#f5f5f7;--accent:#0a64e6;--warn:#b25000}
@media (prefers-color-scheme:dark){:root{--bg:#111113;--fg:#f2f2f7;--muted:#a1a1a6;--line:#2c2c2e;--head:#1c1c1e;--accent:#5ea2ff;--warn:#ffb15e}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.6em;margin:.4em 0}
h2{font-size:1.25em;margin:1.8em 0 .5em;padding-top:.6em;border-top:1px solid var(--line)}
h3{font-size:1.05em;margin:1.4em 0 .4em;color:var(--accent)}
ul{padding-left:1.2em;margin:.4em 0}
li{margin:.15em 0}
p{margin:.5em 0}
.table{overflow-x:auto;margin:.6em 0;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;font-size:13px;width:100%}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;font-variant-numeric:tabular-nums}
th{background:var(--head);position:sticky;top:0;font-weight:600}
tr:last-child td{border-bottom:0}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--head);padding:1px 4px;border-radius:4px}
.warn{color:var(--warn);font-weight:600}
nav{font-size:13px;color:var(--muted)}
nav a{color:var(--accent);margin-right:10px;text-decoration:none}
"""


def inline(text):
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text.replace("⚠ flagged", '<span class="warn">⚠ flagged</span>')


def markdown_to_html(markdown, title):
    out, i, toc = [], 0, []
    lines = markdown.splitlines()
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            head, body = block[0], block[2:]
            out.append('<div class="table"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join(
                           "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in body)
                       + "</tbody></table></div>")
            continue
        if line.startswith("- ") or line.startswith("  - "):
            out.append("<ul>")
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  - ")):
                out.append(f"<li>{inline(lines[i].lstrip()[2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        m = re.match(r"(#{1,3}) (.*)", line)
        if m:
            level = len(m.group(1))
            anchor = re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-")
            if level == 2:
                toc.append((anchor, m.group(2)))
            out.append(f'<h{level} id="{anchor}">{inline(m.group(2))}</h{level}>')
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
        i += 1
    nav = "<nav>" + "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in toc) + "</nav>"
    body = "\n".join(out)
    body = body.replace("</h1>", "</h1>" + nav, 1)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>\n")


# Telegram digest (Ukrainian, HTML)

def build_digest(data, start, end, now, initial):
    year, week, _ = end.isocalendar()
    week_start = end - dt.timedelta(days=6)
    days = dates(week_start, end)
    daily = [data["daily"].get(d.isoformat(), {}) for d in days]
    sleep = [data["sleep"].get(d.isoformat(), {}) for d in days]
    hrv = [data["hrv"].get(d.isoformat(), {}) for d in days]
    acts = [a for a in data["activities"] if week_start <= activity_date(a) <= end]
    steps = [d.get("movement", {}).get("steps", {}).get("value") for d in daily]
    moderate = sum(d.get("intensityMinutes", {}).get("moderate") or 0 for d in daily)
    vigorous = sum(d.get("intensityMinutes", {}).get("vigorous") or 0 for d in daily)
    last_hrv = next((h for h in reversed(hrv) if h), {})
    base = last_hrv.get("baseline") or {}
    load, _ = load_sum(acts)
    flags = rhr_flags(data, start, end)

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
        f"🔥 Інтенсивність: <b>{moderate + 2 * vigorous} хв</b> · навантаження "
        f"{val(load, fmt='{:.1f}') if load is not None else '–'} ({len(acts)} трен.)",
    ]
    week_flags = [d for d in flags if week_start <= d <= end]
    if week_flags:
        out.append("⚠ Пульс спокою під питанням: " + ", ".join(f"{d:%d.%m}" for d in week_flags))
    out += ["", "📎 Повний звіт для ChatGPT: .md і .html нижче" + (" (28 днів)." if initial else ".")]
    return "\n".join(out)


# delivery and schedule

def telegram(method, fields, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if files:
        boundary = "garminreport"
        body = b""
        for name, value in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, (filename, content, mime) in files.items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {mime}\r\n\r\n").encode() + content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def heartbeat(status, msg):
    if not KUMA_PUSH_URL:
        return
    query = urllib.parse.urlencode({"status": status, "msg": msg[:200]})
    try:
        urllib.request.urlopen(f"{KUMA_PUSH_URL}?{query}", timeout=15).close()
    except Exception as exc:  # the report itself already went out
        print(f"heartbeat failed: {exc}", flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def run_once(print_only=False, force_initial=False, html_path=None):
    now = dt.datetime.now(TZ)
    end = now.date() if now.weekday() == 6 else now.date() - dt.timedelta(days=now.weekday() + 1)
    state = load_state()
    initial = force_initial or not state.get("initial_done")
    start = end - dt.timedelta(days=27 if initial else 6)
    data = fetch(start, end)
    zones_hash = hashlib.sha256(json.dumps(data["zones"], sort_keys=True).encode()).hexdigest()
    zones_changed = bool(state.get("zones_hash")) and state["zones_hash"] != zones_hash
    name, markdown = build_markdown(data, start, end, now, initial, zones_changed)
    page = markdown_to_html(markdown, name)
    digest = build_digest(data, start, end, now, initial)
    if print_only:
        print(markdown)
        print("----- Telegram -----")
        print(digest)
        if html_path:
            with open(html_path, "w") as f:
                f.write(page)
        return
    telegram("sendMessage", {"chat_id": CHAT_ID, "text": digest, "parse_mode": "HTML"})
    telegram("sendDocument", {"chat_id": CHAT_ID}, {"document": (f"{name}.md", markdown.encode(), "text/markdown")})
    telegram("sendDocument", {"chat_id": CHAT_ID}, {"document": (f"{name}.html", page.encode(), "text/html")})
    state.update(initial_done=True, zones_hash=zones_hash, last_sent=now.isoformat())
    save_state(state)
    print(f"{now:%Y-%m-%d %H:%M} sent {name} ({start} to {end})", flush=True)
    heartbeat("up", f"sent {name}")


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
        html_path = sys.argv[sys.argv.index("--html") + 1] if "--html" in sys.argv else None
        return run_once(print_only=True, force_initial="--initial" in sys.argv, html_path=html_path)
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
                if attempt == 2:
                    heartbeat("down", f"report failed 3 times: {exc}")
                time.sleep(1200)


if __name__ == "__main__":
    main()
