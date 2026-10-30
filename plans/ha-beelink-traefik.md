# Second Traefik on the Beelink for Home Assistant

Status: done 2026-10-30. All steps passed, including a test with the NAS Traefik stopped.

Goal: `https://ha.ivanskiy.com:8443` reaches Home Assistant from the internet whether the NAS is on or off. The NAS Traefik keeps serving everything else, including `ha.ivanskiy.com` on 443 and `ha.local.ivanskiy.com`.

## What is there now (checked 2026-10-30)

| Item | Value |
| --- | --- |
| NAS Traefik | `traefik:v3.7`, macvlan `192.168.1.12`, config in `/volume1/config/traefik` |
| Home Assistant | on the Beelink, `network_mode: host`, `192.168.1.131:8123` |
| NAS route to HA | `rules/ha.yml`, `HA_HOST=192.168.1.131:8123`, middleware `hsts` only on the public router |
| HA `http:` block | `use_x_forwarded_for: true`, `trusted_proxies`: `192.168.1.12`, `192.168.1.0/24`, `172.20.0.0/16` |
| HA URLs | `external_url` and `internal_url` both unset |
| Beelink ports | 80, 443, 8082, 8443 free. Docker 28.0.0, 346 GB disk free |
| Beelink Watchtower | `WATCHTOWER_LABEL_ENABLE=true`, so containers need the enable label |
| Beelink stack data | `/opt/portainer/volumes/<stack>` |
| Repo vs NAS rules | identical, apart from a trailing newline in `tls.yml` |

`trusted_proxies` does not cover the new proxy: on the host network Traefik connects to HA from `127.0.0.1`, which isn't in `192.168.1.0/24`, so HA answers 400 ("untrusted proxy 127.0.0.1"). Step 3 adds `127.0.0.1/32` and `::1/128`. Step 6 then removes the broad ranges.

The `http:` block in `configuration.yaml` is dead. HA 2026.9 migrated it once (2026-09-13) into `.storage/http` and has ignored the YAML since, so changes go through Settings, System, Network. A saved change is tried on the next restart and reverts after 5 minutes unless you confirm it.

## Design choices

- **Host network for the Beelink Traefik.** HA is on the host network too, so Traefik reaches it at `127.0.0.1:8123`. There's no Docker NAT in the path, so the client IP Traefik sees is the real one, and HA sees the request coming from `127.0.0.1`.
- **File provider only, no Docker socket.** It routes one service. Without the socket, a Traefik compromise doesn't hand over root on the Beelink.
- **Entrypoint on 8443 only.** No port 80 (the DNS challenge doesn't need it), no dashboard, and the ping is bound to `127.0.0.1`.
- **Its own `acme.json`.** It requests `ivanskiy.com` and `*.ivanskiy.com` through the same DigitalOcean DNS challenge. Two instances holding the same certificate is fine.
- **Same image tag as the NAS** (`traefik:v3.7`), updated by Watchtower on both hosts.

## Step 1. Add the Beelink files next to the NAS ones

Both instances live in `portainer/traefik/`, with one compose file per host. A change for one host can't redeploy or break the other, because each Portainer stack points at its own compose path.

```
portainer/traefik/
  docker-compose.yml           # NAS, unchanged
  docker-compose.beelink.yml   # Beelink (new)
  traefik.yml                  # NAS, unchanged
  traefik.beelink.yml          # Beelink (new)
  rules/                       # NAS rules; headers.yml and tls.yml are shared with the Beelink
  rules-beelink/ha.yml         # Beelink only: the 8443 HA router (new)
```

`rules-beelink/ha.yml` stays out of `rules/`: the NAS has no `https-alt` entrypoint, so the router would log an error there on every reload.

`docker-compose.beelink.yml`

```yaml
services:
  traefik:
    image: "traefik:v3.7"
    container_name: "traefik"
    restart: unless-stopped
    network_mode: host
    volumes:
      - "/opt/portainer/volumes/traefik/traefik.yml:/traefik.yml:ro"
      - "/opt/portainer/volumes/traefik/rules:/rules:ro"
      - "/opt/portainer/volumes/traefik/letsencrypt:/letsencrypt"
    environment:
      - DO_AUTH_TOKEN=${DO_AUTH_TOKEN}
      - MAIN_DOMAIN=${MAIN_DOMAIN}
    healthcheck:
      test: ["CMD", "wget", "http://127.0.0.1:8082/ping", "--spider"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 5s
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

`traefik.beelink.yml` (copied to the Beelink as `traefik.yml`)

```yaml
---
global:
  checkNewVersion: false
  sendAnonymousUsage: false
certificatesResolvers:
  mydnschallenge:
    acme:
      dnsChallenge:
        propagation:
          delayBeforeChecks: 10s
          disableChecks: true
        provider: digitalocean
        resolvers:
          - "1.1.1.1:53"
          - "8.8.8.8:53"
      email: andrii@ivanskiy.com
      storage: /letsencrypt/acme.json
entryPoints:
  https-alt:
    address: ":8443"
    transport:
      respondingTimeouts:
        readTimeout: 600s
        idleTimeout: 600s
        writeTimeout: 600s
  ping:
    address: "127.0.0.1:8082"
log:
  level: INFO
accessLog: {}
providers:
  file:
    directory: /rules
    watch: true
api:
  dashboard: false
ping:
  entryPoint: ping
```

`rules-beelink/ha.yml` (copied to the Beelink as `rules/ha.yml`)

```yaml
http:
  routers:
    homeassistant_main:
      entryPoints:
        - "https-alt"
      service: homeassistant
      middlewares:
        - hsts
      rule: Host(`ha.{{ env "MAIN_DOMAIN" }}`)
      tls:
        certResolver: mydnschallenge
        domains:
          - main: '{{ env "MAIN_DOMAIN" }}'
            sans:
              - '*.{{ env "MAIN_DOMAIN" }}'

  services:
    homeassistant:
      loadBalancer:
        passHostHeader: true
        servers:
          - url: "http://127.0.0.1:8123"
```

`rules/headers.yml` and `rules/tls.yml`: no new files. Git has one copy of each, and step 2 puts it on both hosts. `headers.yml` brings the `hsts` middleware. `tls.yml` brings TLS 1.2+ and `sniStrict`. With `sniStrict`, a request to the bare IP on 8443 gets no certificate, which hides the service from IP scanners.

When either file changes (for example HSTS going to `31536000` after 2026-11-05), copy it to `/volume1/config/traefik/rules` on the NAS and `/opt/portainer/volumes/traefik/rules` on the Beelink.

## Step 2. Put the files on the Beelink

```bash
ssh beelink 'sudo mkdir -p /opt/portainer/volumes/traefik/rules /opt/portainer/volumes/traefik/letsencrypt'
```

```bash
scp portainer/traefik/traefik.beelink.yml portainer/traefik/rules-beelink/ha.yml portainer/traefik/rules/headers.yml portainer/traefik/rules/tls.yml beelink:/tmp/
```

Then move them into place with `sudo`: `traefik.beelink.yml` becomes `/opt/portainer/volumes/traefik/traefik.yml`, and the three rule files go into `/opt/portainer/volumes/traefik/rules/`. Check that `letsencrypt/` is writable by root only. Traefik creates `acme.json` with mode 600 on first start.

## Step 3. Deploy in Portainer on the Beelink

Create the stack from Git in the Beelink Portainer (`portainer2.local.ivanskiy.com`), compose path `portainer/traefik/docker-compose.beelink.yml`. The NAS stack keeps `portainer/traefik/docker-compose.yml`. Set the stack env vars:

- `MAIN_DOMAIN=ivanskiy.com`
- `DO_AUTH_TOKEN`: either the NAS token, or (better) a new DigitalOcean token scoped to domain read and write only. Both Traefiks can use the new one, and you can then revoke the old full-access token.

Check:

```bash
ssh beelink 'docker logs traefik 2>&1 | tail -30'
```

Look for the certificate being obtained, and for no errors loading `/rules`.

```bash
curl -sI --resolve ha.ivanskiy.com:8443:192.168.1.131 https://ha.ivanskiy.com:8443/
```

That should return `200` or `405` from HA with a valid certificate. This tests locally before anything is open to the internet.

## Step 4. Port forward on the UCG Max

UniFi Network: Settings, Routing, Port Forwarding. Add WAN TCP `8443` to `192.168.1.131:8443`. Leave the existing 80/443 forward to `192.168.1.12` untouched.

Before this step, confirm `192.168.1.131` is a fixed DHCP reservation, so the forward target can't move.

No DNS change: `ha.ivanskiy.com` already points at the WAN IP.

## Step 5. Test from outside

On mobile data, with Wi-Fi off:

1. Open `https://ha.ivanskiy.com:8443` in a browser and log in.
2. Check the client IP is real: in `docker logs traefik` on the Beelink the access log line shows your mobile IP, and a failed login in HA's log names that IP and not `127.0.0.1`.
3. Simulate the NAS being off: `ssh nas '/usr/local/bin/docker stop traefik'`. Load `:8443` again, then start the NAS Traefik again. This is safer than powering off the NAS for a test.

## Step 6. Harden HA (Settings, System, Network)

Trusted proxies: add `127.0.0.1/32` and `::1/128` (done in step 3). IP ban is already on. Set the login attempts threshold to 5 (it was -1, which never bans). Restart, then confirm the change within 5 minutes.

Decision 2026-10-30: `192.168.1.0/24` and `172.20.0.0/16` stay in the list. Accepted trade-off: a LAN device can spoof `X-Forwarded-For`.

Then delete the dead `http:` block from `configuration.yaml`; HA shows a repair issue while it is there.

This drops `192.168.1.0/24` and `172.20.0.0/16`. Today any device on the LAN can send a fake `X-Forwarded-For` and HA will believe it. With IP bans on, that matters, because a spoofed header could get someone else banned or dodge a ban.

Check first: does anything else reach HA through a proxy from `172.20.0.0/16` or another LAN host? I found nothing in the repo, but Homepage and ESPHome on that network talk to HA directly with tokens, which `trusted_proxies` doesn't affect. Restart HA after the change and re-test both URLs.

Rescue if you lock yourself out: delete the line from `/opt/portainer/volumes/homeassistant/ip_bans.yaml` and restart HA.

## Step 7. Companion app

On each phone: Settings, Companion app, Server.

- External URL: `https://ha.ivanskiy.com:8443`
- Internal URL: `http://192.168.1.131:8123`, with the home Wi-Fi SSID set, so at home the app doesn't depend on the NAS (Traefik or AdGuard) either.

Optionally set the same two URLs in HA under Settings, System, Network, so links in notifications use them.

## Step 8. Commit and update notes

Commit the three new files in `portainer/traefik/` and this plan. Update the Traefik memory note: two instances in one folder, which compose file each Portainer uses, which one serves what, and that rule changes to `headers.yml` and `tls.yml` go to both hosts.

## Rollback

Delete the 8443 port forward. That alone closes the new path. Then remove the stack in the Beelink Portainer and, if step 6 caused trouble, restore the old `trusted_proxies` list.

## Risks and things this doesn't cover

- Networks that block outbound 8443 (some hotel and office Wi-Fi). The 443 route on the NAS still works there while the NAS is on.
- With the NAS off, home DNS still depends on clients falling back to AdGuard2. Not part of this change.
- One more internet-facing Traefik to keep patched. Watchtower handles the image. The config copies are the part that can drift.
