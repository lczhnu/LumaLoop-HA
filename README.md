# LumaLoop HA

[中文说明](README.zh-CN.md) · [Calibration](docs/calibration.md) · [Architecture](docs/architecture.md)

Smooth, adaptive Home Assistant lighting using BH1750 illuminance feedback, bounded PID control, and optional lamp-specific optical calibration. Works with Xiaomi/Mijia lights already exposed as dimmable HA `light` entities and with other compatible lights.

## Goals and capabilities

- Maintain a configurable illuminance **band at the sensor**, with separate sunrise, daytime, sunset, and night settings derived from HA's configured location and timezone.
- Smooth brightness changes with filtering, limited steps, anti-windup and brightness readback checks.
- Respect manual adjustments and manually switched-off lights. Daylight adaptation can switch off at minimum brightness and reopen only a light the controller itself switched off.
- Measure your own lamp's brightness-to-lux curve; combine inverse-curve feedforward with bounded PID correction. A mismatched lamp, color mode, effect or color temperature falls back to ordinary PID.
- Start in observation mode with automatic control off; PID remains off after a restart. Optional sleep schedule and wake control are independent features, disabled by default.

This release controls **one lamp and one sensor per HA instance**. A light group may be used only if it exposes reliable aggregate brightness feedback; calibrate the whole group together. It does not pair Xiaomi devices, replace their HA integration, or guarantee task-plane illuminance when the sensor is elsewhere.

## Hardware

| Item | Requirement |
| --- | --- |
| HA host | Home Assistant with custom integration support; Python 3.13 / HA 2026.2.3 is the pinned regression baseline |
| Light sensor | BH1750 breakout, typically GY-302, I²C address `0x23` or `0x5c` |
| Lamp | Dimmable HA `light` entity with `brightness` feedback; Xiaomi/Mijia integration must be configured first |
| Sensor host | ESP32 dev board (portable path), or Linux Raspberry Pi with accessible `/dev/i2c-1` |
| Wiring | Four female jumper wires; additional ADDR connection if needed |

The control loop itself uses HA services. Whether the Xiaomi connection is local or cloud-based depends on the integration you installed. Cloud latency can require longer control intervals.

## Wiring

Disconnect power before wiring. Use 3.3 V power and logic, and verify the labels on your breakout. Never connect the sensor to lamp mains wiring.

| BH1750 | Raspberry Pi 40-pin header | Classic ESP32 dev board |
| --- | --- | --- |
| VCC / VIN | Pin 1, 3V3 | 3V3 |
| GND | Pin 6, GND | GND |
| SDA | Pin 3, GPIO2 / SDA1 | GPIO21 |
| SCL | Pin 5, GPIO3 / SCL1 | GPIO22 |
| ADDR | GND / board's default low → `0x23` | GND / default low → `0x23` |

ADDR high selects `0x5c`; use the same address in YAML. ESP32-S3/C3 and other boards have different pin assignments. Keep I²C wiring short. Place the sensor at the intended work plane with a fixed orientation, exposed to both daylight and the controlled lamp. Moving it invalidates the optical curve.

## Deployment

### A. HA OS / VM / generic host: ESPHome sensor

1. Wire BH1750 to ESP32 using the table above. In ESPHome, use [examples/esphome-bh1750.yaml](examples/esphome-bh1750.yaml) and provide your own `wifi_ssid`, `wifi_password`, `api_encryption_key` and `ota_password` in ESPHome's `secrets.yaml`. Generate the encryption key in ESPHome. Flash the device and add its ESPHome integration to HA.
2. Confirm the illuminance entity reports in `lx` every second, including unchanged values. The example uses `force_update: true` so freshness checks also work in steady light.
3. Download this repository and copy **only** `custom_components/lighting_smooth` into HA's `/config/custom_components/lighting_smooth`. The existing domain is retained for compatibility; installing a second copy under a different name is unsupported.
4. Merge [examples/configuration.yaml](examples/configuration.yaml) into `/config/configuration.yaml`. Replace `light.example_lamp` and `sensor.example_bh1750_illuminance` with actual entity IDs from Developer Tools → States. Do not copy a second `lighting_smooth:` block.
5. Validate configuration in HA and restart. Confirm the LumaLoop entities and `lighting_smooth.start_calibration` action appear.
6. Create a dashboard and paste [examples/dashboard.yaml](examples/dashboard.yaml) into its raw configuration, or add the entities manually. Most inherited control labels and status messages are Chinese in v1.0; stable entity IDs and this English guide provide the English mapping.

### B. Linux / Raspberry Pi direct I²C

Enable I²C in Raspberry Pi OS (`sudo raspi-config` → Interface Options → I2C) and reboot if needed. Confirm `/dev/i2c-1` exists and the HA process has permission to open it. When using HA Container, map that device into the container; [examples/compose.yaml](examples/compose.yaml) shows the optional mapping and a pinned HA image. The `config` folder sits beside the compose file; copy the integration there before starting it. The example uses Linux host networking and assumes the usual HA onboarding has been completed.

Use the **commented direct-I²C alternative** in the configuration example and omit `illuminance_entity`. No extra Python I²C library is required. This path uses Linux `fcntl` in an executor and cannot run on a Windows HA host. For HA OS, follow its own I²C instructions rather than Raspberry Pi OS commands; ESPHome is the simpler portable route.

## First run

1. Verify HA's location/timezone and that the lamp accepts brightness commands with correct feedback. Disable conflicting lighting automations.
2. Leave `switch.lighting_smooth_observe` on. Switch your lamp on manually, then turn on `switch.lighting_smooth_enabled` to inspect calculations without sending PID commands.
3. Set per-phase lux bands using `number.lighting_smooth_day_lux_min` / `..._max` and the corresponding `sunrise`, `sunset`, `night` entities. Set the lower bound before lowering the upper bound to avoid invalid intermediate ranges.
4. Optionally perform [lamp calibration](docs/calibration.md). Turn on `switch.lighting_smooth_calibration_enabled` afterward.
5. Turn observation off, then explicitly enable PID again: changing observation mode stops the controller. Start with the default 10 s interval and maximum 3 percentage-point brightness step. The loop samples every second; it does not necessarily send commands every second.

| Entity suffix under `lighting_smooth_` | Meaning |
| --- | --- |
| `switch.enabled` / `switch.observe` | Enable controller / calculate only (actual entity IDs put `switch.` first) |
| `select.light` | Target light; changing it stops control |
| `sensor.illuminance`, `filtered`, `actual`, `requested` | Measured/filter lux and actual/requested brightness |
| `sensor.status` | Status with feedforward, background, command and error attributes |
| `sensor.calibration_progress` | Calibration stage / failure information |
| `number.kp`, `ki`, `kd`, `interval`, `max_step`, `filter_tau` | PID gains, cadence, slew limit, filter time constant |
| `switch.sleep_enabled`, `time.sleep_start`, `time.sleep_end` | Optional sleep window: independent of PID/observation |
| `switch.sleep_wake` | Explicit opt-in to turn on and enable control when sleep ends |

For example, the full illuminance ID is `sensor.lighting_smooth_illuminance`. HA may keep different entity IDs if a previous integration occupied them; use the IDs in your registry.

## Behavior and limitations

- Light state feedback acknowledges HA's reported brightness, not physical light output. Unconfirmed commands back off; repeated failures stop automatic control.
- A failed/stale/saturated sensor pauses PID. Direct BH1750 saturates at raw `65535`; entity input must be finite, fresh within 20 s, in lux and below `54612 lx`.
- Manual brightness changes pause PID (default 120 s). Manually switching off does not create permission to switch on again. Daylight probing/reopening applies only to controller-owned off events.
- Sleep, when explicitly enabled, attempts one off command per window even if PID is disabled or in observation mode. Manual reopening is respected during that window. Wake control is a separate opt-in and can enable real control.
- Calibration operates the lamp from off through 100%; perform it only when the room can tolerate this. Restore is best effort: outages/process termination can prevent it. No automatic PID resume follows a sweep.
- One curve is stored locally; a new successful sweep replaces it. Calibrate again after moving the sensor, changing lamp/scene, furniture or fixed color temperature. RGB/HS scenes and dynamic effects are not supported for calibration.
- No UI config flow or HACS store listing is included; installation is a documented folder copy plus YAML edit. No universal gains or brightness curve can be correct for every room.

## Development and privacy

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python tools/check_public_tree.py
```

Tests use synthetic optical curves and mocked HA services. They do not operate a real lamp. See [validation notes](docs/validation.md) for the tested scope.

Only allowlisted source, examples, tests and documentation belong in this repository. No original deployment scripts, screenshots, backups, measured household curves, device inventory, HA tokens, Wi-Fi credentials, SSH endpoints, family names or home coordinates are distributed. Runtime settings and the calibrated entity ID remain in HA's local `.storage/lighting_smooth*` files; do not upload them when reporting an issue. The integration sends no telemetry. The public GitHub owner in project URLs and the existing MIT copyright notice identify the publisher.

MIT license. See [LICENSE](LICENSE).

## References

- [Home Assistant YAML integration loading](https://contributors.home-assistant.io/core/integrations/yaml/)
- [HA OS Raspberry Pi I²C](https://contributors.home-assistant.io/os/operating-system/raspberry-pi/)
- [ESPHome BH1750 sensor](https://esphome.io/components/sensor/bh1750/)
- [Adafruit BH1750 wiring](https://learn.adafruit.com/adafruit-bh1750-ambient-light-sensor/python-circuitpython)
