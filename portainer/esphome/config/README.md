# ESPHome device configs

Copies of the device YAMLs that live on the Beelink in `/opt/portainer/volumes/esphome/config`. The container reads that folder, not this one, so copy a change there after committing it.

`secrets.yaml` stays on the Beelink only. It holds these keys:

- `wifi_ssid`, `wifi_password`
- `apollo_air_1_api_key`
- `apollo_plt_1b_api_key`

OTA updates are encrypted with each device's API key (`ota: encryption:`, ESPHome 2026.9+), so there is no OTA password. Losing the API key means a serial flash.

Package tags are pinned. To upgrade, bump the `@tag`, check the Apollo release notes for the minimum ESPHome version, validate, then flash. The PLT-1B sleeps: turn on its Prevent Sleep switch in HA before flashing and off afterwards.
