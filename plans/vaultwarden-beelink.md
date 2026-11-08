# Vaultwarden on the Beelink, migrated from Bitwarden cloud

Checked 2026-11-08. Nothing in this plan is deployed yet.

## Decisions taken

- Source: Bitwarden cloud account. Migration is export and import, not a data copy.
- Reach: LAN and VPN only, `https://vault.local.ivanskiy.com`, behind `whitelist@file`. Off-site phones sync through the UniFi WireGuard VPN (192.168.4.0/24) and read from their offline cache in between.
- Host: Beelink (192.168.1.131), so the vault stays up when the NAS is planned off during outages.

## Current state

- `portainer/vaultwarden/docker-compose.yml` is an old NAS stack: unpinned `vaultwarden/server`, `unless-stopped`, Traefik labels on the public `https` entrypoint, `WEBSOCKET_ENABLED` (obsolete since 1.29), Watchtower on. No container runs on the NAS or the Beelink.
- `/volume1/docker/vaultwarden/data` on the NAS holds a dormant instance: 1.1 MB, `db.sqlite3` last written 2025-12-28, no attachments or sends.
- The Beelink Traefik uses the file provider only (no Docker labels), host network, LAN entrypoint `https-lan` on :443. Free local port picked: 8085.
- Beelink backup path today: Home Assistant writes nightly tars to `/volume1/BeelinkBackup/homeassistant` over SMB. Hyper Backup tasks "Beelink backup to Dropbox" and "Beelink backup to USB" copy `/BeelinkBackup` daily. "Backup to Hpmicro" is disabled. Nothing else on the Beelink is backed up.

## Phase 0: before starting

1. Look up the current Vaultwarden release (github.com/dani-garcia/vaultwarden/releases) and pin it, for example `vaultwarden/server:1.3x.y`. No Watchtower label: a password manager gets updated on purpose, with a backup taken first. Put a monthly reminder on it, because new Bitwarden clients stop working against an old server.
2. Get a push installation id and key at `https://bitwarden.com/host` (pick the same region as the cloud account). Push works LAN-only, it is outbound to Bitwarden's relay. Without it, mobile apps sync only on open or pull-down.
3. Decide on SMTP. Without it there are no invites, no email 2FA fallback and no new-device mails. MailPlus on the NAS can relay, but then mail depends on the NAS being on. Recommended: skip SMTP for a single user, rely on TOTP plus a passkey.
4. Check whether the dormant NAS instance holds anything the cloud account lacks (Phase 6, step 3) before deleting it.
5. Ask who else uses the cloud account (family organization, emergency access). Each person needs their own export and their own account here.

## Phase 1: deploy the stack

1. New file `portainer/vaultwarden/docker-compose.beelink.yml`:

   ```yaml
   services:
     vaultwarden:
       image: vaultwarden/server:1.3x.y   # pinned, see Phase 0
       container_name: vaultwarden
       restart: always
       user: "1000:1000"
       read_only: true
       tmpfs:
         - /tmp
       cap_drop: [ALL]
       security_opt:
         - no-new-privileges:true
       mem_limit: 512m
       ports:
         - "127.0.0.1:8085:8080"   # Traefik only, nothing on the LAN interface
       volumes:
         - /opt/portainer/volumes/vaultwarden/data:/data
       environment:
         DOMAIN: https://vault.${LOCAL_DOMAIN}
         ROCKET_PORT: "8080"             # non-root can't bind 80
         SIGNUPS_ALLOWED: "true"         # false after Phase 3, step 1
         INVITATIONS_ALLOWED: "false"
         SIGNUPS_VERIFY: "false"
         SHOW_PASSWORD_HINT: "false"
         PASSWORD_HINTS_ALLOWED: "false"
         ORG_CREATION_USERS: ${VW_OWNER_EMAIL}
         ADMIN_TOKEN: ${VW_ADMIN_TOKEN}  # argon2 PHC hash, removed after Phase 3
         LOGIN_RATELIMIT_SECONDS: "60"
         LOGIN_RATELIMIT_MAX_BURST: "5"
         ADMIN_RATELIMIT_SECONDS: "300"
         ADMIN_RATELIMIT_MAX_BURST: "3"
         ICON_BLACKLIST_NON_GLOBAL_IPS: "true"
         PUSH_ENABLED: "true"
         PUSH_INSTALLATION_ID: ${VW_PUSH_ID}
         PUSH_INSTALLATION_KEY: ${VW_PUSH_KEY}
         # PUSH_RELAY_URI / PUSH_IDENTITY_URI only for the EU region
         TZ: Europe/Kyiv
   ```

   The image ships its own healthcheck (`/healthcheck.sh`), so none is added. Stack env (`LOCAL_DOMAIN`, `VW_*`) lives in Portainer, not Git.
2. Create `/opt/portainer/volumes/vaultwarden/data` owned by 1000:1000 (the parent is root-owned, so this step is yours with sudo).
3. Generate the admin token hash with `docker run --rm -it vaultwarden/server:1.3x.y /vaultwarden hash` and paste it into the stack env in single quotes. A plain-text token logs a warning and is weaker.
4. New Beelink Traefik rule `portainer/traefik/rules-beelink/vaultwarden.yml`, same shape as `uptime-kuma.yml`: router `vaultwarden` on `https-lan`, middlewares `whitelist` and `hsts` (not `secure-headers`: Vaultwarden sends its own CSP and frame headers), service `http://127.0.0.1:8085`. Copy it into `/opt/portainer/volumes/traefik/rules` (root-owned, read-only mount: write it through a throwaway `traefik:v3.7` container as done on 2026-11-08). Hot reload, no restart.
5. AdGuard rewrite `vault.local.ivanskiy.com` to `192.168.1.131` on the NAS AdGuard. adguardhome-sync carries it to the Beelink, so DNS works with the NAS off.
6. Create a Portainer stack on the Beelink from Git, `refs/heads/main`, compose path `portainer/vaultwarden/docker-compose.beelink.yml`.
7. Test: `curl --noproxy '*' -sI https://vault.local.ivanskiy.com/alive` returns 200 from the LAN; the same URL from mobile data without VPN gets nothing (no WAN route) and with VPN returns 200.

### Status 2026-11-08

- `portainer/vaultwarden/docker-compose.beelink.yml` written, pinned to `vaultwarden/server:1.37.5` (released 2026-11-01). It differs from the sketch above in three ways: `SIGNUPS_ALLOWED` comes from stack env `VW_SIGNUPS` (default false), `PUSH_ENABLED` from `VW_PUSH_ENABLED` (default false, so the stack starts before the push keys exist), and `ADMIN_TOKEN` defaults to empty, which disables `/admin`.
- Traefik rule live on the Beelink (`/opt/portainer/volumes/traefik/rules/vaultwarden.yml`, identical to Git). `https://vault.local.ivanskiy.com/alive` returns 502 until the container runs, so the router is loaded.
- AdGuard rewrite `vault.local.ivanskiy.com` to `192.168.1.131` added on the NAS (backup `AdGuardHome.yaml.bak-20261108-vault` in the confdir, config checked with `--check-config` first), synced to the Beelink by restarting adguardhome-sync. Both AdGuards answer 192.168.1.131.
- Deployed by you the same evening: data folder 1000:1000, Portainer stack from Git. Container healthy, runs as 1000:1000 with a read-only root and all caps dropped, listens only on 127.0.0.1:8085. `/alive` returns 200 through Traefik, with Vaultwarden's own CSP plus HSTS 86400. Signups on, push off.
- Open: the admin token in the stack env is plain text (Vaultwarden logs a NOTICE), not the argon2 hash. Portainer passes stack env through Compose interpolation, so a pasted hash needs every `$` doubled (`$$argon2id$$v=19$$...`). Or clear it until the admin panel is needed.
- Open: step 7's VPN and mobile-data checks, from your phone.

## Phase 2: backups, proven before any real data goes in

Goal: three copies, two media, one off-site, encrypted at rest, with an alert when a night is missed.

What gets backed up: `db.sqlite3` (taken with SQLite's online backup, never a plain file copy while running), `attachments/`, `sends/`, `rsa_key*.pem` (signs login tokens; losing it logs every client out), `config.json` (only if the admin panel saved settings). `icon_cache` is skipped.

1. Add a backup sidecar to the same compose, `ttionya/vaultwarden-backup` pinned to its current release. It mounts `/data` read-only, runs nightly at 02:00 (after HA's 00:00 backup, before Watchtower at 04:30), writes a 7z archive with AES-256 (`ZIP_TYPE: 7z`, `ZIP_PASSWORD` from stack env), keeps 30 days (`BACKUP_KEEP_DAYS: 30`).
2. Destinations, both through its rclone config:
   - Local: `/opt/portainer/volumes/vaultwarden/backup` on the Beelink disk, so a night with the NAS off still produces a backup.
   - NAS: rclone SMB remote to `//192.168.1.254/BeelinkBackup/vaultwarden`, user `nomad`. From there the existing daily Hyper Backup tasks put it on Dropbox and the USB disk, with Hyper Backup's own version history on top.
3. Heartbeat: a push monitor "Vaultwarden backup" on the Beelink Kuma (26 h interval, Telegram), called from the sidecar's ping URL on success, failure ping on error. Check the sidecar's README for the exact `PING_URL*` variable names on the pinned version. The existing `HyperBackupStale` / `HyperBackupFailed` Grafana rules already cover the NAS-to-Dropbox and USB legs.
4. Offline recovery sheet, on paper, kept at home: master password, the Bitwarden two-step login recovery code, the backup archive password, and the restore steps below. The backup password must never live only inside the vault it protects.
5. Restore drill before Phase 4, then every quarter:
   1. Copy the newest archive from the NAS share (not the Beelink copy, so the off-host leg is what gets tested).
   2. Unpack into a scratch folder, run a throwaway `vaultwarden/server` on another localhost port with that folder as `/data` and `DOMAIN=http://127.0.0.1:<port>`.
   3. Log in in a browser, open three items and one attachment, check the item count matches. Remove the container and folder.
   Done when the drill passes once on a test account.
6. Monthly, by hand: Bitwarden "encrypted JSON (password protected)" export from the web vault, stored next to the archives. It restores into any Bitwarden-compatible server, so it survives a broken SQLite file or a Vaultwarden bug.

### Status 2026-11-08

- Sidecar `vaultwarden-backup` added to the compose: `ttionya/vaultwarden-backup:1.27.2` (rclone 1.75.2), user 1000:1000, read-only root with tmpfs on `/tmp` and `/bitwarden` (the script deletes its scratch dir `/bitwarden/backup` after each run, so that dir can't be a mount point itself), caps dropped. The data mount is read-write because `sqlite3 .backup` on a WAL database needs the `-shm` file. Remotes: `BeelinkDisk:/backup-local` and `NasBackup:BeelinkBackup/vaultwarden`. Ping URLs come from stack env `VW_BACKUP_PUSH_URL` and stay empty when it is unset.
- The script succeeds when at least one remote is reachable, but any failed upload sends the failure ping. A night with the NAS off therefore pings Kuma down; that is expected.
- The archive password is passed to `7z` on its command line, so it is visible in the Beelink process list for the few seconds a run takes. Acceptable on a single-user host.
- Prepared: `/opt/portainer/volumes/vaultwarden/{backup,backup-config}` (0700, 1000:1000), `backup-config/rclone/rclone.conf` with the `BeelinkDisk` local remote, NAS folder `/volume1/BeelinkBackup/vaultwarden`.
- Dry run with a throwaway key: archive holds `db.<date>.sqlite3` and `rsakey.<date>.tar` (no attachments or sends yet), 7zAES with encrypted headers, wrong password rejected.
- Done by you the same evening: DSM user `vwbackup` (SMB, `BeelinkBackup` only), `NasBackup` remote, `zip-password`, Kuma push monitor "Vaultwarden backup" (26 h), stack env `VW_BACKUP_PUSH_URL`, redeploy.
- First real backup 21:35: `backup.20261108.7z` (6.5 KB) on the Beelink disk and on the NAS share, written as `vwbackup`. Retention pass ran on both.
- Technical restore drill passed: the NAS copy was pulled back over SMB, unpacked with the sidecar's `restore`, `pragma integrity_check` ok, 58 schema migrations, and a throwaway `vaultwarden/server:1.37.5` on 127.0.0.1:18085 answered `/alive` 200 with the restored rsa key. Scratch container and folder removed.
- `VW_BACKUP_PUSH_URL` fixed to `http://` (Kuma on 3002 is plain HTTP); the 21:43 run's success ping reached Kuma.
- Full restore drill passed 21:50, after your account and one dummy item existed: NAS copy pulled back, restored, integrity ok, users/ciphers/folders and newest item timestamp identical to live, rsa key hash identical. You logged in to the restored copy with master password and 2FA and read the item.
- The web vault refuses plain HTTP, even on `http://localhost` through a tunnel. The drill needed a temporary HTTPS name: Beelink Traefik rule `vault-drill.yml` to `127.0.0.1:18085`, AdGuard rewrite `vault-drill.local` to 192.168.1.131, and the drill container started with `DOMAIN=https://vault-drill.local.ivanskiy.com`. All three removed afterwards. Reuse this for the quarterly drill. After an AdGuard swap, wait until it answers before restarting adguardhome-sync, or the sync errors out and leaves the Beelink stale.
- Open: `zip-password` still dates from 21:26:56, the run whose output went into the chat. Replace it in a Terminal tab, then delete the 20261108 archives on both remotes and run a new backup.

## Phase 3: account and hardening

1. Create your account in the web vault. Then set `SIGNUPS_ALLOWED: "false"` and redeploy.
2. Account settings: KDF Argon2id (defaults 3 iterations, 64 MB, 4 threads), a long master password, two-step login with TOTP plus a passkey or security key (WebAuthn works because `DOMAIN` is HTTPS). Save the recovery code to the paper sheet.
3. Admin panel: check Diagnostics once (time sync, DOMAIN, websocket, push), then remove `ADMIN_TOKEN` from the stack env and redeploy. That disables `/admin` entirely. Settings saved in the panel go to `config.json` and override env, so prefer env and leave `config.json` absent.
4. Monitoring: HTTP monitor `https://vault.local.ivanskiy.com/alive` on the Beelink Kuma, added to maintenance window "Beelink Watchtower 04:30" only if a Watchtower label is ever added. A blackbox `http_2xx` probe in `blackbox.yml` covers it in Grafana too.
5. Exposure check from outside: confirm the UCG Max has no port forward to 192.168.1.131:443 (only 8443 for HA), and that `ss -ltn` shows 8085 bound to 127.0.0.1 only.

## Phase 4: move the data from Bitwarden cloud

The JSON export carries logins, cards, identities, notes, SSH keys, folders, custom fields, TOTP seeds, password history and passkeys stored in login items. It does not carry attachments, Sends, trash, or the account's own 2FA settings.

1. In the cloud web vault, empty or review the trash (trash is not exported), and list items with attachments (search filter or open each with the paperclip icon).
2. Download every attachment to a local folder outside any synced location (not iCloud Drive, not Dropbox).
3. Export the individual vault: Tools > Export, format `.json (Encrypted)`, type "Password protected". Repeat per organization from the organization's admin console.
4. In the new vault: Tools > Import data, format "Bitwarden (json)", the password from step 3. For an organization, create it here first, then import into it so collections come through.
5. Compare counts per type (logins, cards, identities, notes) between the two web vaults and spot-check ten items, including one with TOTP and one with a passkey.
6. Re-upload the attachments onto their items.
7. Recreate any Sends you still need.
8. Securely delete the export files and the attachment folder (`rm -P` is gone on recent macOS; delete and empty the trash, the disk is FileVault-encrypted).

## Phase 5: switch the clients

For each client: log out of the cloud account, pick "Self-hosted" on the login screen, server URL `https://vault.local.ivanskiy.com`, log in. Order: browser extension on the Mac first, then iPhone (app and AutoFill provider), then any other device.

- First login from each device needs LAN or VPN. After that the cached vault works offline for autofill; edits sync on the next connection.
- On iPhone, consider the UniFi WireGuard on-demand rule so the tunnel comes up off home Wi-Fi without thinking about it.
- Check that a new item made on the phone shows up in the browser extension within seconds (push and websocket both working).

## Phase 6: wind down

1. Keep the cloud account for 30 days, unused, as a fallback. Turn off its Premium auto-renew now.
2. After 30 days: one last export kept with the backups, then delete the cloud account (Account settings > Danger zone).
3. Dormant NAS instance: start it once on the NAS with a different port and no Traefik, log in, check for anything not in the new vault, then archive `/volume1/docker/vaultwarden/data` into `BeelinkBackup/vaultwarden/legacy-nas-2025.7z` with a password and remove the folder.
4. Delete `portainer/vaultwarden/docker-compose.yml` (the NAS one) and its Traefik `bitwarden` router, so nothing can redeploy it on the public entrypoint.
5. Update Homepage with a Vaultwarden card (siteMonitor on `/alive`), and the memory notes.

## Rollback

Until Phase 6, step 2, the cloud account is untouched: log the clients back into bitwarden.com. After that, rollback means restoring the newest archive (Phase 2, step 5) or importing the monthly JSON export into a fresh account anywhere.

## What this plan does not cover

- Public access. If it is ever wanted, it needs a WAN router on the NAS Traefik, CrowdSec or fail2ban on Vaultwarden's log, `/admin` blocked at Traefik, and a new risk review.
- Other people's accounts, until Phase 0, step 5 is answered.
