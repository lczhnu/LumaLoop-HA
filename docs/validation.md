# Validation / 验证记录

Release scope: **1.0.0**, prepared 2026-11-02.

Local result: **129 tests passed** (including inherited repeats); public export checks passed for **37 UTF-8 files**, and all six YAML examples/action/workflow files parsed successfully.

本地结果：**129 项测试通过**（含继承重复项）；**37 个 UTF-8 文件**通过公开目录检查，六个 YAML 示例 / 操作 / 工作流文件均能解析。

## English

The local regression baseline uses Python 3.13 and the installed Home Assistant 2026.2.3 package. The suite exercises actual HA state/entity classes with mocked I²C and service calls; it does not access a household HA server or operate real hardware. CI repeats the suite on Ubuntu with the same pinned HA version.

Coverage includes PID convergence, output bounds, anti-windup, phase transitions, quantization and optical delay simulations, curve applicability, manual overrides, sensor errors/saturation, readback timeouts/backoff, timing jitter, daylight ownership, sleep across midnight and restart, wake behavior, entity input validation, calibration quality failures, cancellation/restoration, local curve persistence and the complete sweep callback path. Tests use synthetic curves. Inherited test cases are intentionally repeated in several controller contexts; the raw test count is not a count of unique scenarios.

The export checker validates Python syntax, JSON, local documentation links, the public directory layout and patterns for private IPs, tokens, private keys, personal paths, device identifiers and email addresses. A separate allowlisted copy is published: no original Git history or raw operational artifacts are uploaded. Pattern scanning is supplementary to inspecting the exported files, not a universal secret detector.

Not newly verified on physical hardware in this release: ESP32 flashing, I²C voltage/device permissions, Xiaomi model compatibility, photometric accuracy, a full in-room sweep or long-running optical stability. The source derives from a working local controller, but this export adds deployment and calibration paths that require user hardware commissioning. HA versions beyond the pinned baseline are not claimed tested. The integration is intentionally not advertised as HACS-listed or UI-configurable.

## 中文

本地回归基线为 Python 3.13 和已安装的 HA 2026.2.3，使用真实 HA 状态与实体类，替换 I²C 和服务调用，不连接家庭 HA、不操作真实灯具。CI 在 Ubuntu 使用相同版本复测。

覆盖 PID 收敛、步长与上下限、抗积分饱和、太阳时段、量化和光学延迟模拟、标定适用条件、手动优先、传感器异常 / 饱和、回读超时退避、调度抖动、自然光开关归属、跨夜睡眠及重启、结束唤醒、实体传感器校验、标定质量失败、取消恢复、本地存储，以及完整标定回调路径。所有光学曲线均为合成数据。部分基础用例在多个控制场景中继承重复运行，原始用例数不等于独立场景数。

公开目录检查包括 Python 语法、JSON、文档本地链接、目录结构，以及内网地址、令牌、私钥、用户目录、设备标识与邮箱模式。仅上传独立导出的白名单文件，不上传原工作区历史和原始运维资料。模式扫描辅助人工检查，不能识别一切形式的敏感信息。

本次未重新进行实物验收：ESP32 烧录、I²C 电平与权限、不同米家型号兼容性、光学精度、完整房间标定及长时间闭环稳定性。算法源自已有控制工程，但公开版新增的部署和标定路径仍需用户在自己的硬件上调试。不宣称已验证所有后续 HA 版本，也未宣称已上架 HACS 或提供 UI 配置向导。
