"""Prometheus metrics for Synology Hyper Backup tasks, read on every scrape.

Uses only files DSM keeps world-readable, so it needs no root and no DSM task.
"""

import configparser
import http.server
import os

LAST = "/hb/last_result/backup.last"
CONF = "/hb/synobackup.conf"
SCHED = "/hb/schedule"
# How old the last success may get before an alert, by schedule type: one run plus slack
MAX_AGE = {"daily": 36 * 3600, "weekly": 8 * 86400, "monthly": 32 * 86400}


def read(path, section=None):
    c = configparser.ConfigParser(interpolation=None, strict=False)
    with open(path) as f:
        text = f.read()
    # DSM .task files have no [section] header
    c.read_string(f"[{section}]\n{text}" if section else text)
    return c


def unquote(v):
    return (v or "").strip().strip('"')


def metrics():
    last, conf = read(LAST), read(CONF)
    out = [
        "# HELP hyperbackup_last_success_timestamp_seconds Unix time of the task's last successful backup",
        "# TYPE hyperbackup_last_success_timestamp_seconds gauge",
        "# HELP hyperbackup_last_run_ok 1 if the last run finished without an error, else 0",
        "# TYPE hyperbackup_last_run_ok gauge",
        "# HELP hyperbackup_max_age_seconds Allowed age of the last success for the task's schedule",
        "# TYPE hyperbackup_max_age_seconds gauge",
    ]
    for task in conf.sections():
        if not task.startswith("task_"):
            continue
        try:
            s = read(os.path.join(SCHED, unquote(conf[task].get("sched_id")) + ".task"), "s")["s"]
        except OSError:
            continue
        kind = unquote(s.get("type"))
        if unquote(s.get("state")) != "enabled" or kind not in MAX_AGE:
            continue
        name = unquote(conf[task].get("name")).replace("\\", "").replace('"', "")
        labels = f'task="{task}",name="{name}",schedule="{kind}"'
        r = last[task] if last.has_section(task) else {}
        result, code = unquote(r.get("result")), unquote(r.get("error_code")) or "0"
        ok = 0 if code != "0" or result in ("failed", "partial") else 1
        out.append(f"hyperbackup_last_success_timestamp_seconds{{{labels}}} {unquote(r.get('last_backup_success_time')) or 0}")
        out.append(f"hyperbackup_last_run_ok{{{labels}}} {ok}")
        out.append(f"hyperbackup_max_age_seconds{{{labels}}} {MAX_AGE[kind]}")
    return "\n".join(out) + "\n"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            body, status = metrics().encode(), 200
        except Exception as exc:  # report the error to Prometheus as a failed scrape
            body, status = f"error: {exc}\n".encode(), 500
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("", 9112), Handler).serve_forever()
