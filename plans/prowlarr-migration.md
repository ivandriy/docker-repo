# Jackett to Prowlarr migration plan

Checked 2026-11-08.

## Why

Each arr holds its own hand-made copy of the Jackett indexers: 4 in Sonarr, 5 in Radarr, 2 in Lidarr, each with its own priority, minimum seeders and category list, and all pointing at `192.168.1.254:9117`. A change to a tracker means editing up to three arrs. Prowlarr keeps the indexers in one place, pushes them to the arrs over the API, and tracks indexer health and stats in one dashboard.

## Current state

Jackett 0.24 on the NAS (stack `jackett`, config `/volume1/docker/jackett`), with FlareSolverr v3.5.2 in the same stack since 2026-11-08 (commits 8a7481f, ddd3b4b).

| Indexer | Login in Jackett | Prowlarr support | Sonarr | Radarr | Lidarr |
|---|---|---|---|---|---|
| Toloka | username + password | built in (`Toloka.cs`) | prio 1, min seeders 1 | prio 1, min 2 | - |
| RuTracker | username + password, captcha, FlareSolverr | built in (`RuTracker.cs`) | prio 10, min 1 | prio 15, min 3 | prio 1, min 1 |
| 0day.kiev | username + password | definition `0daykiev` | prio 25, min 1 | prio 20, min 5 | - |
| BigFANGroup | public | definition `bigfangroup` | prio 25, min 1 | prio 25, min 1 | - |
| RuTor | public | definition `rutor` | - | prio 50, min 5 | - |
| LimeTorrents | public | definition `limetorrents` | - | - | prio 5, min 1 |

Most category lists are tracker-specific IDs (100000 + tracker category), picked by hand: about 280 RuTracker music forums in Lidarr, for example. Prowlarr 2.7.0.5723 is the current release (2026-10-27).

## Decisions to make before starting

1. **Sync level: "Add and Remove Only" (chosen 2026-11-08) or "Full Sync".** Full Sync overwrites each arr's indexer settings on every sync: one priority per indexer for all three arrs, and only Prowlarr's standard category mapping. That would drop today's per-arr priorities (Toloka 1, RuTor 50) and the hand-picked forum lists. "Add and Remove Only" creates the indexers once and then leaves their settings alone, so the current priorities, minimum seeders and categories can be copied onto them.
2. **Where FlareSolverr lives.** It moves from the Jackett stack into the Prowlarr stack, so Jackett can be removed without breaking RuTracker.
3. **Image tag.** The arrs run `linuxserver/*:latest` under Watchtower. For consistency Prowlarr does too, unless you'd rather pin it like Traefik and ESPHome.

## Phase 1: deploy Prowlarr next to Jackett

Nothing changes for the arrs in this phase.

1. Add `portainer/prowlarr/docker-compose.yml`: `linuxserver/prowlarr`, PUID 1026, PGID 100, TZ Europe/Kyiv, config `/volume1/config/prowlarr`, network `containers_bridge`, port 9696, Traefik router `prowlarr.${LOCAL_DOMAIN}` with `whitelist@file`, Homepage widget (`type: prowlarr`, key from stack env `PROWLARR_API_KEY`).
2. Move the `flaresolverr` service (tmpfs, `DISABLE_MEDIA`, 1 GB limit) from the Jackett compose into the Prowlarr compose. Redeploy Jackett first so the old container goes away, then create the Prowlarr stack in Portainer from Git (`refs/heads/main`). Jackett keeps `http://flaresolverr:8191`, so RuTracker is down only for the minute between the two deploys.
3. In Prowlarr (you): set forms login with your own password. Then I set "Disabled for local addresses" so the arrs and Homepage work, and add `prowlarr.local.ivanskiy.com,prowlarr,192.168.1.254` to allowed hosts.
4. Add an AdGuard rewrite for `prowlarr.local.ivanskiy.com` if wildcard rewrites don't cover it already.

## Phase 2: indexers in Prowlarr

1. Add a FlareSolverr indexer proxy at `http://flaresolverr:8191` with tag `flaresolverr`.
2. Add the six indexers. You type the logins for Toloka, RuTracker and 0day.kiev (I can't enter passwords); RuTracker gets the `flaresolverr` tag. I add the three public ones.
3. Test each indexer and run one search per indexer in Prowlarr. Done when all six return results.
4. Check each indexer's capabilities to confirm the category IDs match Jackett's (100000 + tracker category). If any differ, I map them before Phase 3.

## Phase 3: connect the arrs

1. Add each arr as an app in Prowlarr with the decided sync level, using container names (`http://prowlarr:9696`, `http://sonarr:8989`, and so on).
2. Prowlarr creates its indexers in each arr next to the Jackett ones. The arr gets an indexer only if the tracker has matching categories, so Lidarr may gain Toloka's music sections.
3. With "Add and Remove Only", I copy each arr's current priority, minimum seeders and category list from the Jackett indexer onto the matching Prowlarr one through the arr API, and print a before and after table.
4. Disable (don't delete) the old Jackett indexers in each arr.
5. Test: an interactive search per arr for a known title, then wait for one RSS sync. Done when results come only from Prowlarr indexers and the health page is clean.

## Status 2026-11-08: phases 1 to 3 done

- Prowlarr runs as Portainer stack `prowlarr` (id 81) with FlareSolverr. All six indexers return results.
- Category IDs match Jackett's. Dropped on copy: 0day.kiev 100023 (Jackett already flagged it unsupported) and RuTracker 2030/2050 in Radarr (not in Prowlarr's RuTracker).
- The arrs test new indexers with a search in the standard categories. BigFANGroup and RuTor (Radarr) and LimeTorrents (Lidarr) return nothing there, so the arrs rejected them. They were added by hand in those arrs, as Torznab at `http://prowlarr:9696/<id>/` with the old tracker categories and `forceSave`.
- Prowlarr doesn't adopt indexers it didn't create, and kept retrying those. Fix: tags. Each app has a tag (`sonarr`, `radarr`, `lidarr`), and an indexer syncs to an app only when it carries that tag. A sync now logs no warnings. Tags per indexer:
  - RuTracker: `sonarr`, `radarr`, `lidarr`
  - 0day.kiev and Toloka: `sonarr`, `radarr`
  - BigFANGroup: `sonarr`
  - RuTor, LimeTorrents: no app tag (added by hand where needed)
- **A new indexer in Prowlarr reaches an arr only once you give it that arr's tag.**
- Each arr's priorities, minimum seeders and categories were copied onto its Prowlarr indexers. The Jackett indexers are disabled, not deleted.
- Disabled extras from the first sync: LimeTorrents in Sonarr; 0day.kiev and Toloka in Lidarr.
- Test searches (Friends S01E01, Dune, one album) returned results from every enabled indexer, apart from 0day.kiev in Sonarr, which had nothing for Friends.

## Phase 4: retire Jackett (about two weeks later, around 2026-11-22)

1. Delete the disabled Jackett indexers from the arrs.
2. Stop the Jackett stack in Portainer and keep `/volume1/docker/jackett` for another two weeks as the rollback.
3. Remove the stack, the `portainer/jackett` folder, its Homepage card, and any Uptime Kuma monitor for it.
4. Update the `arr-stack-layout` memory and close this plan.

## Rollback

Until Phase 4, Jackett and its arr indexers stay in place. Re-enable the Jackett indexers in each arr and delete the Prowlarr apps (Prowlarr then removes the indexers it created). FlareSolverr keeps working for Jackett wherever it runs, because the URL doesn't change.

## Optional after the move

- More music indexers for Lidarr, which today has only LimeTorrents and RuTracker.
- Prowlarr's indexer stats page, to drop trackers that never return a grabbed release.
