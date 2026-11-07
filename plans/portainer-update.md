# Portainer update plan

Checked 2026-11-08.

## Current state

| | NAS (192.168.1.254) | Beelink (192.168.1.131) |
|---|---|---|
| Version | 2.21.3 (image built 2024-10-07) | 2.27.0 (image built 2025-02-19) |
| Image ref | `portainer/portainer-ce` (implicit `latest`) | `portainer/portainer-ce:latest` |
| Started by | plain `docker run`, created 2024-10-23, no compose file | `/opt/portainer/compose/docker-compose.yml` (not in this repo) |
| Data | `/volume1/docker/portainer` (7.2 MB) | `/opt/portainer/data` (852 KB, root-owned) |
| Ports | 8000, 9000 (HTTP); 9443 not published | 8000, 9443 |
| Restart | always | always |
| Docker engine | 24.0.2 (API 1.43) | 28.0.0 (API 1.48) |
| Watchtower | not labelled, so never auto-updated | not labelled, so never auto-updated |

Both report the same InstanceID (`ed64e072-...`), so the Beelink data folder was probably seeded from a copy of the NAS one. Harmless for the update; worth knowing if a tool ever keys on instance ID.

What depends on the ports:

- NAS Traefik `rules/portainer.yml` goes to `http://172.20.0.1:9000`.
- NAS Traefik `rules/portainer2.yml` goes to `https://192.168.1.131:9443` (insecure transport).
- Homepage widget for NAS Portainer uses `http://192.168.1.254:9000`; Beelink tile monitors `https://192.168.1.131:9443`.
- Uptime Kuma monitor `portainer_beelink` (Docker container check over the 2376 TLS API).

So the NAS container must keep publishing 9000. Newer Portainer still serves HTTP on 9000 inside the container; the docs only stopped publishing it by default.

## Release situation

- Newest Community Edition is **2.45.3** (Docker Hub, 2026-10-21). 2.45 is an LTS. `latest` and `lts` on `portainer/portainer-ce` now point at the 2.45 line.
- **Portainer 3.x has no CE build.** 3.0.1 exists only as `portainer/portainer-ee` (2026-11-03). The 3.0 docs say CE releases stopped at 2.45.
- 2.45 LTS end of support: May 2027 on the lifecycle page, August 2027 in Portainer's 2.45 blog post. Plan for May 2027.
- The free route to 3.x is the "3 Nodes Free" Business Edition licence: closed source, one per organisation, requested through a form. Two hosts fit inside it, but the terms are Portainer's to change.

## Phase 1: move both hosts to 2.45.3 CE now

Pin `2.45.3`, not `latest`, to match how Traefik and ESPHome are pinned. Do the Beelink first: it is a smaller jump (2.27 to 2.45) and nothing outside Portainer depends on its UI.

The database migrates forward on first start. There is no supported downgrade, so the rollback is "restore the tarball and run the old image".

### Beelink

Done 2026-11-08: now 2.45.3, DB migrated 2.27.0 to 2.45.3 with no errors (includes the 2.43 step that converts Git-backed stacks to Source+Workflow records). Rollback image `portainer/portainer-ce:rollback-2.27.0`, backup `/home/nomad/portainer-backups/portainer-20261108.tgz` (data + compose), old compose `/opt/portainer/compose/docker-compose.yml.bak-20261108`. Repo copy: `portainer/portainer/docker-compose.beelink.yml`. Gotcha: every Git stack had an empty reference before (meaning "default branch"); after the migration a redeploy fails with "The git reference could not be found on the remote repository". Fixed by setting `refs/heads/main` in each stack's Git settings, then pull and redeploy; all stacks except traefik were redeployed fine on 2026-11-08 (traefik has the ref set but was left running). Do the same on the NAS right after its upgrade.

1. Keep the old image for rollback: `docker tag portainer/portainer-ce:latest portainer/portainer-ce:rollback-2.27.0`.
2. Stop and back up (data is root-owned, so tar through a throwaway container):
   `docker stop portainer && docker run --rm -v /opt/portainer:/src -v /tmp:/out alpine tar czf /out/portainer-data-20261108.tgz -C /src data`, then move the tarball to a folder you keep.
3. Copy `/opt/portainer/compose/docker-compose.yml` into this repo as `portainer/portainer/docker-compose.beelink.yml`, set `image: portainer/portainer-ce:2.45.3`, drop the obsolete `version:` key.
4. Put the edited file back (throwaway container again, folder is root-owned) and run `docker compose -f /opt/portainer/compose/docker-compose.yml up -d`.
5. Verify: `curl -sk https://192.168.1.131:9443/api/status` shows 2.45.3, log in through `portainer2.local.ivanskiy.com`, open each stack, redeploy one Git stack (dozzle or garmin-mcp), Uptime Kuma `portainer_beelink` green.

### NAS

1. Record the current run config before removing anything: `docker inspect portainer > /volume1/docker/portainer-inspect-20261108.json`.
2. Keep the old image: `docker tag portainer/portainer-ce:latest portainer/portainer-ce:rollback-2.21.3`.
3. Stop and back up: `docker stop portainer && tar czf /volume1/docker/portainer-backup-20261108.tgz -C /volume1/docker portainer`.
4. Recreate it from a compose file in this repo (`portainer/portainer/docker-compose.nas.yml`) instead of a bare `docker run`, so the config is tracked:
   image `portainer/portainer-ce:2.45.3`, `restart: always`, ports `8000:8000` and `9000:9000` (add `9443:9443` only if wanted), volumes `/var/run/docker.sock` and `/volume1/docker/portainer:/data`. Run it with `docker compose` over ssh, not as a Portainer stack (Portainer cannot safely redeploy itself).
5. Verify: `/api/status` shows 2.45.3; `portainer.local.ivanskiy.com` loads through Traefik; Homepage widget shows counts; every Git stack still lists its repo and branch; redeploy the homepage stack (still pending from 2026-11-07) as the real test.

### Rollback (either host)

Stop the new container, restore the tarball over the data folder, start the `rollback-<version>` image with the same ports and mounts.

### After phase 1

- Leave Portainer out of Watchtower. A silent jump across a DB migration is what the backup step exists for.
- Bump the 2.45.x patch by hand (same steps, no folder restructuring) when a new patch lands.

## Phase 2: decide before May 2027

| Option | Cost | Fit for this setup | Risk |
|---|---|---|---|
| Stay on CE 2.45 until end of support | none | everything keeps working as is | no fixes after May 2027; CVEs after that stay open |
| BE 3.x with "3 Nodes Free" | free licence, form-based | same workflow, two hosts fit | closed source; licence terms or the programme can change; 3.x is Kubernetes-first |
| [Komodo](https://github.com/moghtech/komodo) | none, open source | Git-synced compose stacks, Core on the NAS plus a Periphery agent on the Beelink; stacks are already compose files in this repo | heavier: Core needs its own database; new UI to learn |
| [Arcane](https://github.com/getarcaneapp/arcane) | none, BSD-3-Clause | clean single-host UI, remote hosts via agent | Git deploy support unclear in the reviews; check before relying on it |
| Dockhand | free tier | closest look and feel to Portainer, Git deploys, built-in image scanning | licence reported as BSL 1.1 or proprietary; still young (1.0.x) |
| Dockge | none | fine for editing compose files on one box | no Git sync; release cadence has stalled (1.5.0, March 2025) |

Recommendation: do phase 1 now, then trial Komodo on the Beelink in early 2027 with one low-risk stack (dozzle-agent or garmin-mcp). If it handles Git stacks and the Kuma/Homepage checks are moved over, migrate the rest stack by stack before May 2027 and retire Portainer. Fall back to the BE free licence only if Komodo does not work out.

Items to move when replacing Portainer: Traefik rules `portainer.yml` / `portainer2.yml`, the two Homepage tiles and the widget, the Kuma `portainer_beelink` monitor, and the stack env vars that live only in Portainer (Traefik `SYNOLOGY_HOST`/`HA_HOST`, adguard-sync credentials, garmin-mcp key, `TRAEFIK_BASIC_AUTH`). Export those first: they are not in Git.

Sources: [Portainer lifecycle](https://docs.portainer.io/start/lifecycle), [2.45 LTS release](https://www.portainer.io/blog/portainer-2-45-lts-release), [Portainer 3 is Kubernetes-first](https://www.portainer.io/blog/portainer-3-is-kubernetes-first-not-kubernetes-only), [3.0 docs](https://docs.portainer.io/portainer-documentation/3.0-sts), [upgrade on Docker](https://docs.portainer.io/start/upgrade/docker), [selfhostpilot on 3.0](https://selfhostpilot.com/portainer-3-0-drops-community-edition/), [bitdoze alternatives](https://www.bitdoze.com/portainer-alternatives/), [lumadock alternatives](https://lumadock.com/tutorials/portainer-alternatives).
