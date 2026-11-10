"""Records scheduled job runs and exports them as Prometheus metrics.

Jobs call  /ping/<job>        run finished ok (status=fail, or a subject with "fail" or "error", marks it failed)
           /ping/<job>/start  run started (gives the duration on the next finish)
           /ping/<job>/fail   run failed
GET or POST; a POST body is ignored except Kometa's error webhook, where "critical": false only counts a warning.
Jobs are listed in jobs.json; unknown names get 404. State survives restarts in STATE.
"""

import http.server
import json
import os
import threading
import time
import urllib.parse

JOBS = os.environ.get("JOBS_FILE", "/app/jobs.json")
STATE = os.environ.get("STATE_FILE", "/data/state.json")
PORT = int(os.environ.get("PORT", "9113"))
LOCK = threading.Lock()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


jobs = {j["job"]: j for j in load(JOBS, [])}
state = load(STATE, {})


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def record(job, event, msg, body):
    now = time.time()
    with LOCK:
        s = state.setdefault(job, {})
        if event == "start":
            s["started"] = now
        elif event == "fail" and isinstance(body, dict) and body.get("critical") is False:
            s["warnings"] = s.get("warnings", 0) + 1
        else:
            ok = event == "ok"
            s["last_run"] = now
            s["last_ok"] = 1 if ok else 0
            if ok:
                s["last_success"] = now
            started = s.pop("started", None)
            s["duration"] = round(now - started, 1) if started and now - started < 86400 else None
            s["msg"] = msg[:200]
            s["runs"] = s.get("runs", 0) + 1
        save(state)


def metrics():
    out = [
        "# HELP homelab_job_last_run_timestamp_seconds Unix time the job last reported a finished run",
        "# TYPE homelab_job_last_run_timestamp_seconds gauge",
        "# HELP homelab_job_last_success_timestamp_seconds Unix time of the job's last successful run",
        "# TYPE homelab_job_last_success_timestamp_seconds gauge",
        "# HELP homelab_job_last_status 1 if the last run succeeded, 0 if it failed",
        "# TYPE homelab_job_last_status gauge",
        "# HELP homelab_job_duration_seconds Duration of the last run, when the job reports its start",
        "# TYPE homelab_job_duration_seconds gauge",
        "# HELP homelab_job_max_age_seconds Allowed age of the last run before the job counts as overdue",
        "# TYPE homelab_job_max_age_seconds gauge",
        "# HELP homelab_job_running 1 between a start ping and the finish",
        "# TYPE homelab_job_running gauge",
        "# HELP homelab_job_warnings_total Non-critical errors the job reported",
        "# TYPE homelab_job_warnings_total counter",
    ]
    with LOCK:
        for name, j in jobs.items():
            s = state.get(name, {})
            labels = f'job_name="{esc(name)}",host="{esc(j["host"])}",schedule="{esc(j["schedule"])}"'
            out.append(f"homelab_job_max_age_seconds{{{labels}}} {j['max_age']}")
            out.append(f"homelab_job_running{{{labels}}} {1 if 'started' in s else 0}")
            out.append(f"homelab_job_warnings_total{{{labels}}} {s.get('warnings', 0)}")
            if "last_run" in s:
                out.append(f"homelab_job_last_run_timestamp_seconds{{{labels}}} {s['last_run']}")
                out.append(f"homelab_job_last_status{{{labels}}} {s['last_ok']}")
            if "last_success" in s:
                out.append(f"homelab_job_last_success_timestamp_seconds{{{labels}}} {s['last_success']}")
            if s.get("duration") is not None:
                out.append(f"homelab_job_duration_seconds{{{labels}}} {s['duration']}")
    return "\n".join(out) + "\n"


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, code, text, ctype="text/plain"):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_ping(self, body=None):
        url = urllib.parse.urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        if url.path == "/metrics":
            return self.reply(200, metrics(), "text/plain; version=0.0.4")
        if len(parts) not in (2, 3) or parts[0] != "ping" or parts[1] not in jobs:
            return self.reply(404, "unknown job\n")
        q = urllib.parse.parse_qs(url.query)
        event = parts[2] if len(parts) == 3 else "ok"
        if event not in ("start", "fail", "ok"):
            return self.reply(404, "unknown event\n")
        if q.get("status", [""])[0] in ("fail", "down", "error"):
            event = "fail"
        subject = q.get("subject", [""])[0]
        if any(w in subject.lower() for w in ("fail", "error")):
            event = "fail"
        record(parts[1], event, " ".join(q.get("msg", []) + [subject]).strip(), body)
        self.reply(200, "ok\n")

    def do_GET(self):
        self.handle_ping()

    def do_POST(self):
        raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        self.handle_ping(body)

    def log_message(self, fmt, *args):
        if "/metrics" not in self.path:
            print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"job-heartbeat on :{PORT}, {len(jobs)} jobs", flush=True)
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
