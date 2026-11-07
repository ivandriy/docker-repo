# Move Zigbee from HomeBridge to Home Assistant

Status: draft 2026-11-07, nothing changed yet. Revised 2026-11-07: one HomeKit bridge per HA area instead of one Zigbee bridge. Step 2.1 to 2.5 done 2026-11-07 (six bridges loaded in HA, not yet paired); step 2.6 pairing is next.

Goal: Apple Home gets every Zigbee accessory from Home Assistant, and HomeBridge is cut down to the one plugin HA can't replace: UniFi Protect, for HomeKit Secure Video, face recognition and the doorbell chime on the HomePod minis.

Decision (2026-11-07): the cameras stay on HomeBridge, because HA's HomeKit Bridge doesn't support HomeKit Secure Video.

## What is there now (checked 2026-11-07)

| Item | Value |
| --- | --- |
| HomeBridge | `homebridge/homebridge:2026-11-02` 2.5.0 (image 2026-11-02, UI 5.30.0, Node 24.22.2; updated 2026-11-07) on the NAS, `restart: unless-stopped`, macvlan `HB_MACVLAN_IP`, UI `hb.local.ivanskiy.com` via `rules/hb.yml`, data `/volume1/docker/homebridge` |
| Child bridge 1 | `homebridge-unifi-protect` 8.2.0 (upgraded 2026-11-07 from 7.21.0, which refused Protect 7), UCG Max `192.168.1.1`, options `Enable.Doorbell.Trigger`, `Enable.Doorbell.Mute.<doorbell MAC>`. Stays |
| Child bridge 2 | `homebridge-z2m` 1.11.2 (upgraded 2026-11-07 from 1.9.3, which failed on 30 battery accessories under Homebridge 2), MQTT `192.168.1.131:1883`, 44 accessories. Moves to HA |
| Dead plugins | `homebridge-hue` and `homebridge-dummy` had no platform in `config.json`; uninstalled 2026-11-07 |
| HA | 2026.9.2 on the Beelink, `192.168.1.131` |
| HA HomeKit Bridge entries | 8 accessory-mode (3 TVs, Denon, Shield, 2 ACs, `vacuum.wall_e`, `alarm_control_panel.apartment`) and 2 bridges: `21063` from YAML (`input_text.homepod_announcement`, which HomeKit doesn't support, so it exposes nothing) and `21065` (2 aquarium binary sensors) |
| HA Zigbee | all 44 HomeBridge devices exist through Z2M MQTT discovery, with Ukrainian names and areas (45 Z2M devices in HA) |

The z2m bridge exposes, by HomeKit service: 12 switches, 7 temperature, 5 humidity, 5 contact, 5 occupancy, 3 motion, 3 leak, 3 thermostats, 2 smoke, 3 stateless programmable switches. The programmable switches come from the 4-button scene switch in the corridor, the bedroom smart knob and the Aqara H1 double rocker in the kitchen.

## What stays on HomeBridge

| Camera | Services |
| --- | --- |
| `Камера домофон` (G4 Doorbell) | stream, mic, speaker, motion, light sensor, Doorbell, HomeKit Secure Video, switches `Doorbell Trigger` and `Doorbell Mute` |
| `Камера коридор` | stream, mic, speaker, motion, Doorbell (from the global `Enable.Doorbell.Trigger`), HomeKit Secure Video, switch `Doorbell Trigger` |

The HomePod chime is a Home app setting on the doorbell accessory (Notifications, "Chime on HomePod"), so it keeps working without changes. HA's UniFi Protect integration runs next to the plugin as it does now; both read the same UCG Max.

After this plan, keeping HomeBridge running means one container and one plugin, with no MQTT or Zigbee dependency.

## Design choices

- **One bridge per HA area, defined in YAML.** HomeKit has no API for a bridge to set rooms: the Home app puts every bridged accessory in the bridge's room at pairing. A bridge per area turns that limit into the room mapping. Six bridges, `mode: bridge`, `include_entities` only:

  | Bridge | Port | HA area |
  | --- | --- | --- |
  | `HA Ванна` | 21080 | Ванна |
  | `HA Вітальня` | 21081 | Вітальня |
  | `HA Дитяча` | 21082 | Дитяча |
  | `HA Коридор` | 21083 | Коридор |
  | `HA Кухня` | 21084 | Кухня |
  | `HA Спальня` | 21085 | Спальня |

  `entity_config` (name, type) is YAML-only; the UI options flow has no field for it. So `homekit:` in `configuration.yaml` becomes a list: the existing `21063` entry first, unchanged, then the six area bridges, each with `name`, `port`, `filter.include_entities` and `entity_config`. Edits to an existing YAML bridge apply with the `homekit.reload` action. A new bridge in the list is imported only at HA start, so adding one needs a restart (found 2026-11-07). The accessory-mode entries stay in the UI as they are. An include list keeps new Z2M entities out of Apple Home until they are added on purpose.
- **Names set in HA before pairing.** HomeKit takes the name once, at pairing. Each bridge gets `entity_config: <entity>: name:` with a short name, because the Home app already shows the room: "Датчик температури в ванній" becomes "Температура", shown as "Ванна · Температура". This also replaces the 7 Z2M friendly names (see open questions). A rename after pairing is done in the Home app, or by resetting the bridge and pairing again.
- **Tile type set in HA.** The Home app picks icon and tile from the HomeKit service. Sockets get `entity_config: type: outlet`: `switch.bedroom_tv_appliances_socket`, `switch.livingroom_tv_appliances_socket`, `switch.livingroom_air_humidifier` (the living room plug) and `switch.aquarium_lights_plug` (already `device_class: outlet`). The boiler, towel dryer, exhaust fan, EcoFlow input, fingerbots and the USB switch channels stay `switch`. The two kitchen worktop channels, `switch.kitchen_worktop_lights_left` and `switch.kitchen_worktop_lights_right`, go through the "Change device type of a switch" helper to a `light` entity first, so it gets a bulb tile and answers "turn off the lights".
- **Entity IDs are fixed after pairing.** The HomeKit accessory ID follows the `entity_id`. Renaming an entity_id, or moving an entity to another area's bridge, creates a new accessory, which loses its room and any Home app scene or favourite on the old one.
- **Entities, not devices.** HomeKit Bridge exposes one accessory per entity, so the list has the main entity of each device: the switch or climate entity, or the sensor that carries the reading. Power, energy, link quality and battery entities stay out. HA links a battery sensor from the same device by itself. Expect about 55 to 60 accessories in total, 5 to 15 per bridge, well under the 150 per-bridge limit.
- **Buttons through device triggers.** The 4-button switch, the knob and the H1 rocker go into their area bridge's `devices` option (Коридор, Спальня, Кухня), which exposes their triggers as stateless programmable switches.
- **Run both side by side, then cut over.** The z2m child bridge stays until the HA version of every accessory has been tested in the Home app.

## Step 1. Inventory and backup

1. The Home app has no automations (user, 2026-11-07); every automation runs in HA. Check on the phone for Home scenes, favourites and Siri phrases that use Zigbee accessories. Those have to be redone, because new accessories have new IDs.
2. Check that every Zigbee device has the right area in HA (checked 2026-11-07: all 45 have one of the 6 areas). The HA area is now the room in the Home app, so fix it in HA, not in Home.
3. Back up `/volume1/docker/homebridge` on the NAS to `/volume1/docker/homebridge-backup-<date>.tgz`.

## Step 2. Area bridges in HA

1. For each area, list the main entity of every Zigbee device in it: 12 switches, 3 climate, 5 contact, 3 leak, 2 smoke, every presence binary sensor (occupancy, motion, vibration, motion-with-lux), plus the temperature, humidity and light sensors. Leave out power, energy, link quality and battery entities.
2. Create the light helper for each switch that drives a light, and use the new `light.*` entity in the list instead of the switch.
3. Add the six bridges to `homekit:` in `configuration.yaml` (keep a copy of the file first), each with its area's list, the `entity_config` names, and `type: outlet` for the sockets. Check the configuration, then run `homekit.reload`.
4. Check the mappings that are not one-to-one:
   - Vibration sensors (3, including the cat litter box) have no HomeKit class and come through as occupancy, the same as in HomeBridge now.
   - Fingerbots (2) and the 3-channel USB switch come through as switches.
   - The 2 radiator valves and the floor heating thermostat come through as thermostats. Check that heat and off modes match what the Home app showed before.
   - The 3 Aqara T1 light sensors come through as light sensors.
5. Add the 3 button devices to `devices` on their area bridge. Check first that each has device triggers in HA (Settings, Devices, the device, Automations). Z2M only publishes a trigger after the button has sent that action once, so press every button before you add the device.
6. Pair the bridges one at a time. When the Home app asks for a room, pick the one that matches the bridge, so every accessory on it lands there. Fix any name that came out wrong in HA and reset that bridge, rather than renaming in the Home app, so HA stays the source.

## Step 3. Cut over

1. Redo any Home scenes and favourites found in step 1 on the new accessories. There are no Home automations to rebuild.
2. In the HomeBridge UI, remove the zigbee2mqtt platform from the config and restart. Its child bridge then drops out of the Home app; if it doesn't, remove it there by hand.
3. Uninstall `homebridge-z2m` in the HomeBridge UI (`homebridge-hue` and `homebridge-dummy` are already gone), so `package.json` lists only `homebridge` and `homebridge-unifi-protect`.
4. Check the doorbell once more: press it, and the HomePods should chime.

## Step 4. Make the remaining HomeBridge survive reboots

The doorbell chime now depends on this one container. Its policy is `unless-stopped`, and Synology's shutdown stops every container by name, so after a NAS reboot it comes back only if the boot script's start call works. It worked after the last reboot (`ContainerManager.log`), but that same path kept Homepage down for a week until 453eecb.

1. Change `restart: unless-stopped` to `restart: always` in `portainer/homebridge/docker-compose.yml`, the same fix as Homepage, and redeploy the stack from Git.
2. Pin the image to the running version (`homebridge/homebridge:<tag>`) instead of `latest`. Watchtower is already `monitor-only` for it, so updates stay manual.

## Rollback

Until step 3.2, rollback is removing the six area bridges from `homekit:` in `configuration.yaml`, running `homekit.reload`, and deleting the six entries left in Settings, Devices & Services. After it, put the zigbee2mqtt platform back in `config.json` from the step 1 backup and reinstall `homebridge-z2m`. The child bridge then has to be paired with the Home app again and its rooms set by hand. With no Home automations, that is the whole cost, so step 3.2 can be undone in about half an hour.

## Open questions

- 7 Z2M devices are named by Z2M friendly name in HomeBridge (`bedroom_temperature_sensor`, `aquarium_lights_plug`, `bedroom_window_sensor`, `childroom_window_sensor`, `kitchen_pir_sensor`, `bedroom_tv_appliances_socket`, `livingroom_tv_appliances_socket`). Match them to HA entities by hand in step 2.1; the `entity_config` names replace them.
- Keeping include lists in step with HA areas: a script in `portainer/homeassistant/tools` could rebuild each bridge's list from area membership and report what changed. Not built yet.

## Area bridges (names and lists approved 2026-11-07)

Built from the live HA entity list on 2026-11-07. The Home app shows the room above each tile, so names leave the room out. Names are unique within a room. Left out on purpose: battery, tamper, tilt, child lock, indicator, schedule, energy and power entities, the radiator valves' separate local temperature sensors (the thermostat tile shows it), the presence sensors' `illumination` text sensors, the H1 switch's device temperature, and the Z2M bridge entities.

### Ванна (port 21080), 9 accessories

| Entity | Home name | Notes |
| --- | --- | --- |
| `switch.bathroom_boiler` | Бойлер | switch |
| `switch.bathroom_exhaust_fan` | Витяжка | switch |
| `switch.towel_warmer_plug` | Рушникосушарка | switch |
| `binary_sensor.bathroom_door_sensor_contact` | Двері | contact |
| `binary_sensor.bathroom_presence_sensor_occupancy` | Присутність | occupancy, `unavailable` since 15:41 UTC today |
| `binary_sensor.bathroom_water_sensor_water_leak` | Протікання | leak |
| `binary_sensor.bathroom_cat_litter_vibration_sensor_vibration` | Котячий лоток | shows as occupancy |
| `sensor.bathroom_temperature_sensor_temperature_2` | Температура | |
| `sensor.bathroom_temperature_sensor_humidity_2` | Вологість | |

### Вітальня (port 21081), 12 accessories

| Entity | Home name | Notes |
| --- | --- | --- |
| `switch.livingroom_air_humidifier` | Зволожувач | `type: outlet` (the plug) |
| `switch.livingroom_air_humidifier_on` | Зволожувач увімкнути | fingerbot |
| `switch.livingroom_air_humidifier_off` | Зволожувач вимкнути | fingerbot |
| `switch.livingroom_tv_appliances_socket` | ТВ розетка | `type: outlet` |
| `switch.livingroom_usb_switch_l1` | USB 1 | switch |
| `switch.livingroom_usb_switch_l2` | USB 2 | switch |
| `switch.livingroom_usb_switch_l3` | USB 3 | switch |
| `binary_sensor.livingroom_presence_sensor_occupancy` | Присутність | occupancy |
| `binary_sensor.livingroom_pir_sensor_occupancy` | Рух | occupancy |
| `sensor.livingroom_light_sensor_illuminance` | Освітленість | light sensor |
| `sensor.livingroom_temperature_sensor_temperature` | Температура | |
| `sensor.livingroom_temperature_sensor_humidity` | Вологість | |

### Дитяча (port 21082), 9 accessories

| Entity | Home name | Notes |
| --- | --- | --- |
| `climate.childroom_radiator_valve` | Радіатор | thermostat |
| `switch.aquarium_lights_plug` | Світло акваріума | `type: outlet` |
| `binary_sensor.childroom_door_sensor_contact` | Двері | contact |
| `binary_sensor.childroom_window_sensor_contact` | Вікно | contact |
| `binary_sensor.childroom_vibration_sensor_vibration` | Вібрація | shows as occupancy |
| `binary_sensor.childroom_water_sensor_water_leak` | Протікання акваріума | leak |
| `sensor.childroom_aquarium_temperature_sensor_temperature` | Температура акваріума | |
| `sensor.childroom_temperature_sensor_temperature` | Температура | |
| `sensor.childroom_temperature_sensor_humidity` | Вологість | |

### Коридор (port 21083), 6 accessories and 1 button device

| Entity | Home name | Notes |
| --- | --- | --- |
| `climate.corridor_warm_floor_thermostat` | Тепла підлога | thermostat, `unavailable` since 15:45 UTC today |
| `switch.corridor_ecoflow_input` | Вхід EcoFlow | switch |
| `binary_sensor.appartment_entry_door_sensor_contact` | Вхідні двері | contact |
| `binary_sensor.corridor_smoke_sensor_smoke` | Дим | smoke |
| `sensor.sofa_temperature_sensor_temperature` | Шафа температура | server cabinet |
| `sensor.sofa_temperature_sensor_humidity` | Шафа вологість | server cabinet |
| device `82cefa85f3aa2d627c3266e9bb5c9887` | Перемикач сцен | 4-button switch, in `devices` |

### Кухня (port 21084), 7 accessories and 1 button device

| Entity | Home name | Notes |
| --- | --- | --- |
| `light.kitchen_worktop_lights_left` | Підсвітка ліва | light, from the switch helper on `switch.kitchen_worktop_lights_left` |
| `light.kitchen_worktop_lights_right` | Підсвітка права | light, from the switch helper on `switch.kitchen_worktop_lights_right` |
| `binary_sensor.kitchen_presence_sensor_occupancy` | Присутність | occupancy |
| `binary_sensor.kitchen_pir_sensor_occupancy` | Рух | occupancy |
| `binary_sensor.kitchen_smoke_sensor_smoke` | Дим | smoke |
| `binary_sensor.kitchen_water_sensor_water_leak` | Протікання | leak |
| `sensor.kitchen_light_sensor_illuminance` | Освітленість | light sensor |
| device `2027494d21d6c36500451406108f33c7` | Вимикач | H1 double rocker, in `devices` (needs decoupled mode to send actions; check) |

### Спальня (port 21085), 7 accessories and 1 button device

| Entity | Home name | Notes |
| --- | --- | --- |
| `climate.bedroom_radiator_valve` | Радіатор | thermostat |
| `switch.bedroom_tv_appliances_socket` | ТВ розетка | `type: outlet` |
| `binary_sensor.0xa4c13858a269b68c_contact` | Вікно | contact, entity_id kept as is |
| `binary_sensor.bedroom_vibration_sensor_vibration` | Вібрація | shows as occupancy |
| `sensor.bedroom_light_sensor_illuminance` | Освітленість | light sensor |
| `sensor.bedroom_temperature_sensor_temperature` | Температура | |
| `sensor.bedroom_temperature_sensor_humidity` | Вологість | |
| device `b3cc0bacf577310bbcc5db2fdc396bb7` | Ручка | smart knob, in `devices` |

Total: 50 accessories and 3 button devices.

### Before pairing

1. Skipped 2026-11-07: `binary_sensor.0xa4c13858a269b68c_contact` keeps its entity_id. It is used by 2 automations, the UI group "Bedroom window opened" and possibly the bedroom Better Thermostat, whose references a rename would not update. The Home app shows the `entity_config` name "Вікно", so the ID never shows. Keep it unchanged after pairing.
2. Find out why the bathroom presence sensor and the corridor floor thermostat went `unavailable` on 2026-11-07 around 15:45 UTC. They can be paired as they are, but they'll show "No response" in the Home app.
3. Decide on bridge `21065` (`binary_sensor.aquarium_temp_low` and `_high`). Moving both into the Дитяча bridge would mean one fewer bridge in the Home app. Leaving them as they are needs no work.

### YAML shape (one bridge shown)

```yaml
homekit:
  - filter:
      include_entities:
        - input_text.homepod_announcement
  - name: HA Ванна
    port: 21080
    mode: bridge
    filter:
      include_entities:
        - switch.bathroom_boiler
        - switch.bathroom_exhaust_fan
        - switch.towel_warmer_plug
        - binary_sensor.bathroom_door_sensor_contact
        - binary_sensor.bathroom_presence_sensor_occupancy
        - binary_sensor.bathroom_water_sensor_water_leak
        - binary_sensor.bathroom_cat_litter_vibration_sensor_vibration
        - sensor.bathroom_temperature_sensor_temperature_2
        - sensor.bathroom_temperature_sensor_humidity_2
    entity_config:
      switch.bathroom_boiler: {name: Бойлер}
      switch.bathroom_exhaust_fan: {name: Витяжка}
      switch.towel_warmer_plug: {name: Рушникосушарка}
      binary_sensor.bathroom_door_sensor_contact: {name: Двері}
      binary_sensor.bathroom_presence_sensor_occupancy: {name: Присутність}
      binary_sensor.bathroom_water_sensor_water_leak: {name: Протікання}
      binary_sensor.bathroom_cat_litter_vibration_sensor_vibration: {name: Котячий лоток}
      sensor.bathroom_temperature_sensor_temperature_2: {name: Температура}
      sensor.bathroom_temperature_sensor_humidity_2: {name: Вологість}
```

The other five bridges follow the same shape. Sockets add `type: outlet` next to `name`, and the three button bridges add `devices: [<device id>]`.

## Done 2026-11-07

- Light helpers (Switch as X): `light.kitchen_worktop_lights_left` and `light.kitchen_worktop_lights_right`, entries `01M6VQSM3CFCX4B9R185ZQ0S7N` and `01M6VQSRS1A98RWJJS66TPZMFX`. The original switches are hidden and keep working.
- `configuration.yaml` on the Beelink: `homekit:` is now a list (the `21063` entry, then the six area bridges). Backup `/config/configuration.yaml.bak-homekit-2026-11-07`, edited with `docker exec homeassistant` over `ssh beelink`.
- HA restarted; all six entries loaded (`HA Ванна:21080` to `HA Спальня:21085`) with the approved names, `type: outlet` on the 4 sockets and linked battery sensors.
- Seen at the time: 7 mains and battery devices were `unavailable` since about 15:45 UTC (bathroom presence, boiler, towel warmer, floor thermostat, USB switch, EcoFlow input, kitchen H1 switch). Not caused by this work; check before testing them in the Home app.

- Step 2.6: all six bridges paired with the Home app on 2026-11-07 (HA storage shows paired clients). mDNS drops the Cyrillic, so the Home app lists them by MAC suffix:

  | Home app name | Bridge |
  | --- | --- |
  | HA 584760 | Ванна |
  | HA 3975B7 | Вітальня |
  | HA B475B6 | Дитяча |
  | HA 6E0201 | Коридор |
  | HA 59F032 | Кухня |
  | HA 8E604B | Спальня |

- Rooms checked by the user in the Home app on 2026-11-07: all correct.

- Step 1.3: backup `/volume1/docker/homebridge-backup-20261107-pre-cutover.tgz` (86 MB) on the NAS.
- Step 3.2 and 3.3 done 2026-11-07 22:05 local time: zigbee2mqtt platform removed from `config.json` (its child bridge was `0E:8A:BC:DE:4A:D8`), `homebridge-z2m` uninstalled, Homebridge restarted. `package.json` now lists only `homebridge` 2.5.0 and `homebridge-unifi-protect` 8.2.0. The z2m bridge no longer advertises over mDNS; Protect child bridge started and found both cameras.

Left: remove the old `homebridge-z2m` bridge from the Home app if it still shows there (step 3.2), press the doorbell to test the HomePod chime (step 3.4). Step 4.1 done 2026-11-07: `restart: always` in the compose file, and applied to the running container with `docker update`, so it holds before the next stack redeploy. Step 4.2 was done in 57b85c7.
