# Lamp calibration / 灯具标定

## English

Calibration measures the **installed lamp and sensor together**, including room reflections and placement. It is not a factory lumen rating or a sensor-accuracy calibration.

1. Fix the sensor in its final location. Close curtains or work at night; keep all other lights and furnishings unchanged. Disable competing automations and dynamic/night effects. Choose a fixed color temperature or brightness-only mode. Turn the lamp on once so HA has valid brightness and color-mode feedback.
2. Disable both sleep and wake switches. Calibration rejects these modes to prevent sleep commands from competing with the sweep. Automatic PID will be stopped, regardless of observation mode.
3. Open Developer Tools → Actions and run:

```yaml
action: lighting_smooth.start_calibration
data:
  settle: 5
  samples: 3
  max_drift: 3
```

The action starts a background task and returns immediately. **It will switch the lamp off, then through 1, 5, 10, 20, 35, 50, 65, 80 and 100%, then off again.** Each stage waits for brightness feedback (up to about 10 s), settles for `settle` seconds and takes `samples` readings one second apart. With defaults, allow roughly 1.5–3 minutes; slow devices take longer. Monitor `sensor.lighting_smooth_calibration_progress`.

The median of the initial off samples is background lux. Lamp contribution equals total lux minus that background. The final off measurement must be within `max_drift` lux of the initial background; each sample group must also have a spread no larger than that tolerance. Significant downward curve steps are rejected. Small downward noise within tolerance is flattened using a cumulative maximum. A flat curve is rejected. These checks cannot detect every possible mid-sweep daylight change; stable background is still required.

For entity-backed sensors, the sweep requires **new reports**, not repeated reads of an old state. Report at least once per second; the ESPHome example enables `force_update`. Missing updates, sensor saturation, invalid values, missing lamp feedback or a color-mode/effect change fail the run.

On completion, failure or cancellation, the task attempts to restore original power and brightness. It does not change color temperature or effects. A service error during restoration fails the run and prevents replacing the previous model. Restoration is not physically verified and cannot be guaranteed during HA shutdown, disconnection or power loss: inspect the lamp if interrupted. Do not repeatedly cancel while restoration is in progress.

Cancel using:

```yaml
action: lighting_smooth.cancel_calibration
data: {}
```

Only after measurement **and restoration** succeed is a validated curve saved to HA's `.storage/lighting_smooth_calibration`. The old curve remains intact on failure. A successful run replaces the single stored curve; it does not automatically enable calibration or PID. Enable `switch.lighting_smooth_calibration_enabled`, turn observation off if you want real control, and explicitly enable PID.

The curve applies only to the calibrated light entity and matching color/effect mode, with ±50 K tolerance for a calibrated color temperature. Otherwise the controller uses ordinary PID. Moving the sensor or changing the room invalidates the physical model even though entity matching still passes. Recalibrate whenever those conditions change.

Brightness uses HA's integer 1–255 scale; percentage commands are quantized. Lux is measured at the sensor. Minimum-on output is distinct from off; the inverse lookup saturates at the lowest measured on-level rather than inventing an unmeasured low-light response. This release stores no downloadable household calibration example; tests use a synthetic plant.

## 中文

标定对象是 **安装后的灯具、传感器与空间整体关系**，不是灯具出厂流明测试，也不是 BH1750 精度校准。

1. 固定传感器，夜间或拉好窗帘后进行。其他灯具和家具保持不变，关闭冲突自动化及动态 / 夜灯特效。选择固定色温或纯亮度模式，先开灯一次，确保 HA 已有有效亮度和颜色模式反馈。
2. 关闭睡眠与结束唤醒开关，避免其与标定冲突。开始标定会停止 PID，即使之前处于观察模式。
3. 在开发者工具 → 操作中调用上面的 `lighting_smooth.start_calibration`，也可以使用示例仪表盘中的标定按钮。

默认依次测量关灯本底、1%、5%、10%、20%、35%、50%、65%、80%、100%，最后再次关灯检测本底。**此过程会实际开关灯并升至全亮。** 每档等待 HA 亮度反馈（约最多 10 秒），再稳定 5 秒，取 3 个间隔 1 秒的样本。通常预留约 1.5–3 分钟，慢设备可能更久。操作立即返回，后台进度在 `sensor.lighting_smooth_calibration_progress` 查看。

取初始关灯样本中位数作为本底，灯具贡献等于总照度减本底。末尾本底漂移及每档样本极差不得超过 `max_drift`（默认 3 lx）。明显下降的曲线拒绝保存，小幅噪声下降按累计最大值展平，全平曲线也拒绝。这些检查不能识别所有中途自然光变化，因此仍应主动保持环境稳定。

使用 HA 照度实体时，必须收到新的上报，不能重复读取旧状态。建议每秒上报，ESPHome 示例已经设置 `force_update`。无新上报、读数饱和 / 非法、灯具无亮度回读、颜色 / 特效模式发生变化时，标定失败。

完成、失败或取消均会尝试恢复标定前的开关与亮度，不改变色温和特效。恢复调用异常会令本次失败，不覆盖旧曲线。恢复只检查服务调用，不能保证物理灯具已恢复；HA 关闭、网络中断或断电时需人工检查。恢复过程中不要重复点击取消。

取消调用上面的 `lighting_smooth.cancel_calibration`。只有测量、恢复和曲线校验全部成功，才覆盖本地 `.storage/lighting_smooth_calibration`；失败时保留旧曲线。成功不会自动打开“使用标定曲线”或 PID 开关，需要用户自行启用。

曲线仅用于对应灯具实体、颜色模式、特效及固定色温（容差 ±50 K），条件不符回退普通 PID。位置和空间变化无法靠实体 ID 自动识别，需自行重新标定。每个 HA 实例仅保存一条曲线，新的成功标定替换旧曲线。

HA 亮度只有 1–255 整数档，百分比存在量化误差；测量结果仅代表传感器处照度。关灯与最低开灯输出分开处理，反查不会虚构未测量的低亮度连续响应。仓库不提供家庭实测曲线，测试曲线为人工合成。
