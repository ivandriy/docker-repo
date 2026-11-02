"""Weekly Garmin report to Telegram.

Every Monday at REPORT_TIME (container TZ) it pulls the previous Monday to Sunday
from the garmin-mcp server, builds a compact Markdown summary and sends it to a
Telegram chat as a message plus a .md file, ready to paste or upload to ChatGPT.

Stdlib only, so it runs on the stock python image.
  python report.py           run forever on the weekly schedule
  python report.py --now     send last week's report once and exit
  python report.py --print   print last week's report and exit (no Telegram)
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.request

MCP_URL = os.environ.get("MCP_URL", "http://garmin-mcp:8491/mcp")
# the server only accepts localhost Host headers (DNS-rebinding check)
MCP_HOST = os.environ.get("MCP_HOST", "localhost:8491")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REPORT_TIME = os.environ.get("REPORT_TIME", "08:00")

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Mcp:
    def __init__(self):
        self.session = None
        self.next_id = 1
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "garmin-weekly-report", "version": "1"},
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
        with opener.open(req, timeout=120) as resp:
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


def hm(seconds):
    if not seconds:
        return "-"
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def num(value, fmt="{}"):
    return "-" if value is None else fmt.format(value)


def avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


def build_report(start, end):
    mcp = Mcp()
    span = {"start": start.isoformat(), "end": end.isoformat()}
    daily = {d["calendarDate"]: d for d in mcp.tool("garmin_get_daily_summaries", **span).get("data", [])}
    sleep = {s["calendarDate"]: s for s in mcp.tool("garmin_get_sleep_summaries", **span).get("results", [])}
    hrv = {h["calendarDate"]: h for h in mcp.tool("garmin_get_hrv_summaries", **span).get("hrvSummaries", [])}
    activities = mcp.tool("garmin_search_activities", **span).get("results", [])

    year, week, _ = start.isocalendar()
    lines = [
        f"# Garmin weekly report {year}-W{week:02d} ({start} to {end})",
        "",
        "## Days",
        "",
        "| Day | Steps | Active kcal | Intensity min | Resting HR | Stress avg | Body Battery min-max | Sleep | Sleep score | Deep/REM | HRV night | HRV status |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    totals = {"steps": [], "rhr": [], "stress": [], "sleep": [], "score": [], "hrv": [], "intensity": []}
    day = start
    while day <= end:
        key = day.isoformat()
        d, s, h = daily.get(key, {}), sleep.get(key, {}), hrv.get(key, {})
        steps = d.get("movement", {}).get("steps", {}).get("value")
        im = d.get("intensityMinutes", {})
        intensity = None if not im else (im.get("moderate") or 0) + 2 * (im.get("vigorous") or 0)
        rhr = d.get("heartRate", {}).get("restingValue")
        stress = d.get("stress", {}).get("avgLevel")
        bb = d.get("bodyBattery", {})
        score = s.get("sleepScores", {}).get("overall", {}).get("value")
        night = h.get("lastNightAvg")
        for name, value in (("steps", steps), ("rhr", rhr), ("stress", stress), ("sleep", s.get("sleepTimeSeconds")),
                            ("score", score), ("hrv", night), ("intensity", intensity)):
            totals[name].append(value)
        lines.append(" | ".join([
            f"| {day:%a %d.%m}",
            num(steps),
            num(d.get("calories", {}).get("burnedActive")),
            num(intensity),
            num(rhr),
            num(stress),
            f"{num(bb.get('minValue'))}-{num(bb.get('maxValue'))}" if bb else "-",
            hm(s.get("sleepTimeSeconds")),
            num(score),
            f"{hm(s.get('deepSleepSeconds'))}/{hm(s.get('remSleepSeconds'))}" if s else "-",
            num(night),
            f"{h.get('status', '-')} |",
        ]))
        day += dt.timedelta(days=1)

    last_hrv = hrv.get(end.isoformat()) or (list(hrv.values())[-1] if hrv else {})
    baseline = last_hrv.get("baseline") or {}
    lines += [
        "",
        "## Week",
        "",
        f"- Steps: {num(sum(v for v in totals['steps'] if v))} total, {num(avg(totals['steps']))} per day",
        f"- Intensity minutes (vigorous counted twice): {sum(v for v in totals['intensity'] if v)}",
        f"- Resting HR avg: {num(avg(totals['rhr']))} bpm",
        f"- Stress avg: {num(avg(totals['stress']))}",
        f"- Sleep avg: {hm(avg(totals['sleep']))}, score avg {num(avg(totals['score']))}",
        f"- HRV night avg: {num(avg(totals['hrv']))} ms, 7-day avg {num(last_hrv.get('weeklyAvg'))} ms, "
        f"balanced baseline {num(baseline.get('balancedLow'))}-{num(baseline.get('balancedUpper'))} ms",
        "",
        "## Activities",
        "",
    ]
    if not activities:
        lines.append("None.")
    for a in sorted(activities, key=lambda a: a.get("startTimeLocal", "")):
        parts = [
            a.get("startTimeLocal", "")[:16],
            a.get("activityName") or a.get("activityType", {}).get("typeKey", "activity"),
            hm(a.get("duration")),
        ]
        if a.get("distance"):
            km = a["distance"] / 1000
            parts.append(f"{km:.2f} km")
            if a.get("duration") and a.get("activityType", {}).get("typeKey", "").endswith("running"):
                pace = a["duration"] / km
                parts.append(f"pace {int(pace // 60)}:{int(pace % 60):02d}/km")
        if a.get("averageHR"):
            parts.append(f"HR {round(a['averageHR'])} avg / {num(a.get('maxHR') and round(a['maxHR']))} max")
        if a.get("calories"):
            parts.append(f"{round(a['calories'])} kcal")
        if a.get("aerobicTrainingEffect") is not None:
            parts.append(f"TE {a['aerobicTrainingEffect']:.1f} aerobic / {a.get('anaerobicTrainingEffect') or 0:.1f} anaerobic")
        if a.get("activityTrainingLoad"):
            parts.append(f"load {round(a['activityTrainingLoad'])}")
        lines.append("- " + ", ".join(parts))
    return f"garmin-{year}-W{week:02d}.md", "\n".join(lines) + "\n"


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


def send(filename, text):
    # Telegram caps a message at 4096 characters; split on line breaks
    chunk = ""
    for line in text.splitlines(keepends=True):
        if len(chunk) + len(line) > 4000:
            telegram("sendMessage", {"chat_id": CHAT_ID, "text": chunk})
            chunk = ""
        chunk += line
    if chunk:
        telegram("sendMessage", {"chat_id": CHAT_ID, "text": chunk})
    telegram("sendDocument", {"chat_id": CHAT_ID}, {"document": (filename, text.encode())})


def last_week(today):
    start = today - dt.timedelta(days=today.weekday() + 7)
    return start, start + dt.timedelta(days=6)


def run_once(print_only=False):
    filename, text = build_report(*last_week(dt.date.today()))
    if print_only:
        print(text)
    else:
        send(filename, text)
        print(f"{dt.datetime.now():%Y-%m-%d %H:%M} sent {filename}", flush=True)


def seconds_until_next_run():
    hour, minute = map(int, REPORT_TIME.split(":"))
    now = dt.datetime.now()
    target = (now + dt.timedelta(days=-now.weekday())).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=7)
    return (target - now).total_seconds(), target


def main():
    if "--print" in sys.argv:
        return run_once(print_only=True)
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    if "--now" in sys.argv:
        return run_once()
    while True:
        wait, target = seconds_until_next_run()
        print(f"next report {target:%Y-%m-%d %H:%M}", flush=True)
        time.sleep(wait)
        for attempt in range(3):
            try:
                run_once()
                break
            except Exception as exc:  # retry later in the morning, then wait for next week
                print(f"report failed (attempt {attempt + 1}): {exc}", flush=True)
                time.sleep(1800)


if __name__ == "__main__":
    main()
