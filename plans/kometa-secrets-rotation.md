# Kometa secrets: rotate and move out of the repo

Found 2026-11-08.

## Status 2026-11-08: rotated

Every leaked value is dead. Checked against each service: the old Radarr and Sonarr keys, both old Plex tokens (the repo one and the newer live one) and the old TMDb key are refused. The OMDb key was already invalid before the rotation. Trakt is no longer supported by Kometa.

- Kometa reads `config.env.yml` with placeholders. Its stack env holds the new Plex, TMDb, Radarr and Sonarr values, and each one checked out against its service.
- OMDb and Trakt were removed from Kometa (commit e384799). Both only produced errors.
- Prowlarr and the Homepage Sonarr and Radarr widgets have the new keys. The `plex` stack has the same new token as Kometa.
- Left for you:
  - Reconfigure the Home Assistant Sonarr integration; its entities are unavailable.
  - Re-authorise the Home Assistant Plex integration and the Plex watchlist lists in Sonarr and Radarr, if they ask.
  - Delete the old Kometa config files on the Beelink. They hold only dead values; my delete was blocked.
  - Optional: remove the unused `KOMETA_OMDBKEY`, `KOMETA_TRAKTID` and `KOMETA_TRAKTSECRET` from the stack env.
- History rewrite: not needed, the values in history are dead.

## What leaked

`github.com/ivandriy/docker-repo` is public. Since 2025-02-22, `portainer/kometa/config/config.yml` has held real values for seven credentials. The Radarr and Sonarr keys match the live ones exactly. A scan of every tracked file and of the old `.env` files in history found no other secrets.

| Credential | Risk | Used by today |
|---|---|---|
| Plex token | High: your Plex account and server, from anywhere | Kometa; Homepage Plex widget (`PLEX_TOKEN`, Beelink stack `plex`); Home Assistant Plex integration; Plex watchlist import lists in Sonarr and Radarr; every Plex app you're signed in to |
| Trakt client ID and secret | Medium: your Trakt API app | Kometa |
| TMDb API key | Low: quota | Kometa |
| OMDb API key | Low: quota | Kometa |
| Radarr API key | Low: LAN and VPN only | Kometa; Prowlarr app; Homepage widget (`RADARR_API_KEY`, NAS stack `radarr`) |
| Sonarr API key | Low: LAN and VPN only | Kometa; Prowlarr app; Homepage widget (`SONARR_API_KEY`, NAS stack `sonarr`); Home Assistant Sonarr integration |

The Kometa container reads `/opt/portainer/volumes/kometa/config/config.yml` on the Beelink, a bind mount the stack doesn't take from Git. The repo copy is only a reference. Editing it changes nothing live.

## Who does what

I can't type passwords, tokens or keys into a site or a form, and copying the Plex token from the Beelink to the NAS was blocked by the permission check. So anything that means signing in or pasting a new secret is yours. I do the plumbing: config edits, compose changes, commits, propagating new arr keys between containers on the NAS, and checks.

## Phase 1: make Kometa read secrets from the environment (no rotation yet)

Kometa loads every `KOMETA_<NAME>` environment variable as a config secret, used in the config as `<<name>>`. No `_` in names.

1. I change `portainer/kometa/docker-compose.yml` to pass seven variables from the Portainer stack env: `KOMETA_PLEXTOKEN`, `KOMETA_TMDBKEY`, `KOMETA_OMDBKEY`, `KOMETA_RADARRKEY`, `KOMETA_SONARRKEY`, `KOMETA_TRAKTID`, `KOMETA_TRAKTSECRET`.
2. I replace the seven values in the live `config.yml` with `<<plextoken>>`, `<<tmdbkey>>` and so on, after a backup. The repo copy gets the same placeholders, so the current commit no longer carries any value.
3. I commit and push the compose and config changes. Kometa keeps working on its old config until the stack is redeployed in step 4.
4. You: in the Beelink Portainer, open the `kometa` stack, add the seven variables with the **new** values from Phase 2, and redeploy. Doing Phase 2 first means you enter each secret once.

Check: Kometa's Trakt login writes tokens back into `config.yml`. After the first run I check that Kometa didn't write any real value back over a placeholder.

## Phase 2: rotate

Plex goes last. By then everything else is ready, so the sign-out disrupts your devices for as short a time as possible.

1. **TMDb:** themoviedb.org > Settings > API: regenerate the key. (You)
2. **OMDb:** request a new key at omdbapi.com, then deactivate the old one if the account page offers that. (You)
3. **Trakt:** trakt.tv > Settings > Your API Apps: create a new app for Kometa (or reset the secret), then delete the old app. Kometa then needs its one-time PIN authorisation again, which I'll walk you through. (You)
4. **Radarr and Sonarr keys:**
   - You: Settings > General > API Key > regenerate, in each of the two.
   - Me: update the Prowlarr app entries and the `RADARR_API_KEY` and `SONARR_API_KEY` stack env on the NAS, then redeploy those two stacks so Homepage works.
   - You: reconfigure the Sonarr integration in Home Assistant.
   - You: put both new keys into the Kometa stack env.
5. **Plex:** plex.tv > Account > Devices: "Sign out of all devices". This invalidates the leaked token everywhere.
   - You: sign back in on each TV, phone and browser.
   - You: re-authorise the Home Assistant Plex integration, and the Plex watchlist lists in Sonarr and Radarr.
   - You: take a fresh token from Plex Web for the `PLEX_TOKEN` env of the Beelink `plex` stack (Homepage) and for `KOMETA_PLEXTOKEN`.
   - Me: check the Plex server still shows as signed in under Settings > General afterwards. If it doesn't, you sign it in again from there.

## Phase 3: verify

- Kometa: one run (`--run`), log shows Plex, TMDb, OMDb, Trakt, Radarr and Sonarr all connected.
- Homepage widgets for Plex, Sonarr, Radarr show data.
- Prowlarr: sync to Sonarr and Radarr succeeds.
- Home Assistant: `sensor.sonarr_upcoming` and the Plex media players are available again.
- Old values: a Radarr or Sonarr request with the old key gets 401.

## Phase 4: history and prevention

Rotation is what matters.

- **Git history:** after rotation the old values are useless, so rewriting history is optional. If you want them gone anyway: `git filter-repo` on the file, then a force-push of `main`. Portainer stacks pull `refs/heads/main` and would follow. Forks and clones made earlier keep the old history either way.
- **Prevention:** nothing in this repo checks for secrets today. A `gitleaks` pre-commit hook would block a commit that adds a key. Alternatively, make the repo private: Portainer would then need a GitHub token for every Git stack.
