# Homelab status: morning check

Goal: open https://grafana.local.ivanskiy.com/d/homelab-status in the morning and see, at the top, whether every overnight job ran and what else happened since the evening. Today the dashboard shows current state (targets, Kuma, disks, UPS, versions) but nothing about last night.

Dashboard JSON: `portainer/synology-monitoring/grafana-dashboards/general/homelab-status.json` (provisioned, `allowUiUpdates: false`; copy to `/volume1/docker/synology-monitoring/grafana-dashboards` with `COPYFILE_DISABLE=1`).

## What runs overnight

| Time (Kyiv) | Job | Host | Signal today |
|---|---|---|---|
| hourly | adguardhome-sync | NAS | none (`API_PORT: '0'`, so no metrics) |
| hourly | Cleanuparr queue cleaner | NAS | none |
| 00:00 (`@daily`) | Recyclarr sync | NAS | none, logs only |
| 02:00 | Vaultwarden backup (ttionya) | Beelink | Beelink Kuma push monitor, not in Prometheus |
| 03:00 Sun | ImageMaid (`MODE=move` now) | Beelink | none |
| 03:30 | qbit_manage | NAS | none |
| 04:00 / 04:30 | Watchtower NAS / Beelink | both | Prometheus `watchtower_*`, rules WatchtowerUpdateFailed / ScanFailed |
| 05:00 | Kometa | Beelink | none |
| per DSM schedule | Hyper Backup tasks | NAS | hyperbackup-exporter, rules HyperBackupStale / Failed |
| Sun 19:00 | Garmin weekly report | Beelink | NAS Kuma push monitor 65 |

Other overnight events worth a look: container restarts and crashes, host reboots, power outages and UPS on battery, alerts that fired and resolved while asleep, Kuma monitors that flapped, certificates close to expiry.

## Phase 0: check facts (read only)

- Grafana alert state history backend: annotations (default) or Loki. The "Alerts overnight" panel below needs annotations.
- `container_start_time_seconds` present for both cAdvisor jobs.
- NAS uptime metric in the SNMP job (`hrSystemUptime` or `sysUpTime`) and `node_boot_time_seconds` for the Beelink.
- DSM Task Scheduler: list user tasks under `/usr/syno/etc/synoschedule.d/` and decide which ones belong in the table.
- HA automatic backups: enabled, and at what time.
- Kometa's real run time: memory says daily 05:00, compose has no schedule env. Read it from the Kometa config/logs.
- ttionya backup: does it support a generic `PING_URL` next to the `_WHEN_SUCCESS/_FAILURE` ones already used for Kuma, so it can ping two places.

## Phase 1: dashboard panels from data that already exists (done 2026-11-10)

No new services. New row "Last night" at the top (y=0, push the rest down), each panel with `timeFrom: 12h`:

1. Restarted overnight (table): cAdvisor containers with `container_start_time_seconds > now - 12h`, both hosts, name, host, start time, version (reuse panel 28's version label logic). Covers Watchtower updates, crash loops and reboots in one list.
2. Host uptime (two stats): NAS and Beelink, red under 12 h.
3. Alerts overnight (annotation list): built-in alert annotations, all dashboards, 12 h. Shows rules that fired and resolved while the Firing alerts panel is already empty.
4. Kuma flaps (table): `changes(monitor_status[12h]) > 0` by monitor name, sorted by change count.
5. Power (stat + mini timeline): minutes on UPS battery in 12 h from the SNMP UPS status, plus the HA grid/EcoFlow state from the `hass_*` metrics.
6. Watchtower last scan (two stats): containers updated and failed per host from `watchtower_*`, until Phase 2 folds them into the jobs table.
7. Certificates (table): five nearest `traefik_tls_certs_not_after` and blackbox `probe_ssl_earliest_cert_expiry`, red under 20 d.

Done when: the row renders on the live dashboard, every query returns data or a deliberate "none" text, and a test restart of one container shows up in panel 1.

## Phase 2: job heartbeat exporter

One small service records every scheduled job run, so the jobs table and a staleness alert work the same way for all of them.

- `job-heartbeat` in the synology-monitoring stack, same pattern as hyperbackup-exporter (`python:3.13.18-alpine`, script in the repo, live copy next to `prometheus.yml`, healthcheck on `/metrics`).
- `GET|POST /ping/<job>?status=ok|fail&duration=<s>&msg=`; also accepts the Kometa and qbit_manage webhook JSON (map their run-end and error events to ok/fail).
- Config `jobs.yml`: job, host, schedule text, `max_age` (for example 26 h daily, 8 d weekly, 2 h hourly).
- State kept in a JSON file on a volume, so a restart does not forget the last runs.
- Metrics: `homelab_job_last_run_timestamp_seconds`, `homelab_job_last_status` (1 ok, 0 fail), `homelab_job_duration_seconds`, `homelab_job_max_age_seconds`, all labelled `job`, `host`, `schedule`.
- Reachable from the Beelink on a NAS host port, LAN only; scrape job `job-heartbeat`.

Wire the jobs:

| Job | How it reports |
|---|---|
| Vaultwarden backup | second ping URL in the ttionya sidecar (Beelink Kuma keeps its own, it must survive NAS off) |
| Garmin report | second URL env next to `KUMA_PUSH_URL` in `report.py` |
| Kometa | `webhooks.run_end` and `webhooks.error` in `config.env.yml` |
| qbit_manage | `webhooks.run_end` / `run_start` in its config |
| Recyclarr | wrap the cron command so it pings after `recyclarr sync` exits, with the exit code as status |
| ImageMaid | webhook if it has one, otherwise skip and rely on panel 1 of Phase 1 |
| adguardhome-sync | turn on its API port with metrics and scrape it instead of pinging |
| HA backup, DSM tasks | HA automation on backup events / DSM task "run command" curl, after Phase 0 |
| Hyper Backup, Watchtower | already exported, joined into the table by query |

Dashboard: "Scheduled jobs" table as the first panel of the row: job, host, schedule, last run (time ago), duration, status, overdue. Red for fail or `time() - last_run > max_age`. Hyper Backup and Watchtower rows come from their own metrics through `label_replace` and a merge transform.

Done when: every job in the table has run at least once and shows green, and a forced failure (`/ping/<job>?status=fail`) turns its row red.

## Phase 3: alerts

- `ScheduledJobFailed` (last status 0) and `ScheduledJobOverdue` (age over `max_age`) in `grafana-alerting/homelab.yml`, with the `nas_planned_off` gate that nine rules already carry.
- Keep the Hyper Backup and Watchtower rules as they are; no duplicates for those two.
- Validate with the provisioning reload, then a test fail ping that reaches Telegram.

## Phase 4 (optional): morning digest

An 08:00 Telegram message with the jobs table summary and the overnight counts (restarts, alerts, flaps, minutes on battery), sent by the exporter or an HA automation reading Prometheus. Skip if the dashboard alone is enough after a week.

## Morning routine once done

Open the dashboard, read the "Last night" row top to bottom: jobs table all green, nothing unexpected in Restarted overnight, Alerts overnight empty or explained. Only then scroll to the current-state rows.

## Rollback

Phase 1: restore `homelab-status.json.bak-<date>` on the NAS. Phase 2: remove the service and scrape job; the ping URLs added to jobs fail quietly. Phase 3: delete the two rules and reload.
