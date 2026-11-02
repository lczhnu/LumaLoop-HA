import time
from homeassistant.components.sensor import SensorEntity,SensorStateClass,SensorDeviceClass
from . import DOMAIN
from .engine import PHASES,SAMPLE_INTERVAL
from .entity import LightingEntity

SENSORS={
    "illuminance":("BH1750 实时照度","mdi:brightness-5"),
    "filtered":("滤波照度","mdi:blur"),
    "phase":("当前太阳时段","mdi:theme-light-dark"),
    "status":("控制状态","mdi:state-machine"),
    "target":("当前目标照度","mdi:brightness-auto"),
    "lux_min":("当前照度下限","mdi:brightness-4"),
    "lux_max":("当前照度上限","mdi:brightness-7"),
    "actual":("灯具反馈亮度","mdi:lightbulb-on-outline"),
    "requested":("PID 请求亮度","mdi:lightbulb-auto-outline"),
    "integral":("积分补偿","mdi:math-integral"),
    "sunrise":("今日日出","mdi:weather-sunset-up"),
    "sunset":("今日日落","mdi:weather-sunset-down"),
    "commands":("本次启动调光命令数","mdi:counter"),
    "sample_age":("照度距上次成功采样","mdi:timer-outline"),
    "calibration_progress":("Calibration progress","mdi:chart-line"),
    "sleep_status":("睡眠时段状态","mdi:power-sleep"),
}


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    async_add_entities([Feedback(hass.data[DOMAIN],"sensor",k,*v) for k,v in SENSORS.items()])


class Feedback(LightingEntity,SensorEntity):
    def __init__(self,*args):
        super().__init__(*args)
        if self.key in ("illuminance","filtered","target","lux_min","lux_max"):
            self._attr_device_class=SensorDeviceClass.ILLUMINANCE
            self._attr_native_unit_of_measurement="lx"
            self._attr_state_class=SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision=1
        elif self.key in ("actual","requested","integral"):
            self._attr_native_unit_of_measurement="%"
            self._attr_state_class=SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision=1
        elif self.key in ("sunrise","sunset"):
            self._attr_device_class=SensorDeviceClass.TIMESTAMP
        elif self.key=="sample_age":
            self._attr_native_unit_of_measurement="s"
        elif self.key=="commands":
            self._attr_native_unit_of_measurement=None

    @property
    def available(self):
        c=self.control
        if self.key in ("illuminance","filtered"):
            return c.fresh()
        if self.key in ("target","lux_min","lux_max","phase","sunrise","sunset"):
            return c.phase is not None
        return True

    @property
    def native_value(self):
        c=self.control
        p=c.phase
        if self.key=="illuminance": return c.lux
        if self.key=="filtered": return round(c.pid.filtered,1) if c.pid.filtered is not None else c.lux
        if self.key=="phase": return PHASES.get(p,"不可用")
        if self.key=="status": return c.status
        if self.key=="calibration_progress": return c.calibration_progress
        if self.key=="sleep_status": return c.sleep_status
        if self.key=="target": return (c.params[p+"_lux_min"]+c.params[p+"_lux_max"])/2 if p else None
        if self.key in ("lux_min","lux_max"): return c.params[p+"_"+self.key] if p else None
        if self.key=="actual": return c.actual
        if self.key=="requested": return c.requested
        if self.key=="integral": return round(c.pid.integral,3)
        if self.key=="sunrise": return c.sunrise
        if self.key=="sunset": return c.sunset
        if self.key=="commands": return c.command_count
        if self.key=="sample_age": return round(time.monotonic()-c.good_time) if c.good_time else None
        return None

    @property
    def extra_state_attributes(self):
        c=self.control
        if self.key=="illuminance":
            return {"raw":c.raw,"saturated":c.raw==65535,"bus":c.bus,"address":hex(c.address),"source_entity":c.illuminance_entity,
                    "sampled_at":c.measured_at.isoformat() if c.measured_at else None,"read_error":c.error,
                    "sample_interval_seconds":SAMPLE_INTERVAL}
        if self.key=="phase":
            return {"today_windows":{PHASES[k]:v for k,v in c.windows.items()},
                    "next_phase":PHASES.get(c.next_phase),
                    "next_boundary":c.next_boundary.isoformat() if c.next_boundary else None,
                    "window_minutes":c.params["twilight"],"timezone":c.hass.config.time_zone,
                    "location_source":"Home Assistant 家庭位置（不使用浏览器定位）"}
        if self.key=="status":
            return {"light_entity":c.light,"enabled":c.enabled,"observe_only":c.observe,
                    "calibration_loaded":c.calibration is not None,
                    "calibration_enabled":c.calibration_enabled,
                    "calibration_active":bool(c.result and c.result.calibrated),
                    "control_method":c.calibration_status,
                    "calibration_source":c.calibration.source if c.calibration else None,
                    "feedforward_brightness_percent":round(c.result.feedforward,2) if c.result and c.result.feedforward is not None else None,
                    "smoothed_feedforward_brightness_percent":round(c.result.smoothed_feedforward,2) if c.result and c.result.smoothed_feedforward is not None else None,
                    "estimated_background_lux":round(c.result.background,2) if c.result and c.result.background is not None else None,
                    "pid_correction_percent":round(c.result.correction,3) if c.result and c.result.correction is not None else None,
                    "last_command":c.last_command,"notice":c.notice,"read_error":c.error,
                    "pending_raw_brightness":c.pending,"consecutive_failures":c.failures,
                    "acknowledgement":"确认的是 HA 亮度回读，不代表灯具物理输出已校准",
                    "policy":"仅重开自动关掉的灯；手动操作优先；重启后 PID 关闭"}
        if self.key=="sleep_status":
            return {"enabled":c.sleep_enabled,"active":c.sleep_active,
                    "start":c.sleep_start.isoformat(),"end":c.sleep_end.isoformat(),"wake":c.sleep_wake,
                    "manual_override":c.sleep_override,"notice":c.sleep_notice,
                    "timezone":c.hass.config.time_zone,
                    "policy":"每段开始仅关一次；手动重新开灯后不再关；睡眠期间暂停 PID；结束不自动开灯"}
        return {}
