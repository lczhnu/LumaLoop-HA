"""BH1750 feedback and bounded, opt-in room lighting PID control."""
import asyncio
from datetime import datetime,timedelta
import logging
import math
import time as monotonic_time

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import Context, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_location
from homeassistant.util import dt as dt_util

from .engine import DEFAULT_LIGHT, DEFAULTS, PARAMS, PHASES, SAMPLE_INTERVAL, PID, phase_at, validated
from .calibration import Calibration
from .calibration_runner import measure_curve
from .bh1750_io import read_bh1750
from .sleep_schedule import clock,sleep_window

DOMAIN="lighting_smooth"
_LOGGER=logging.getLogger(__name__)
CONFIG_SCHEMA=vol.Schema({vol.Optional(DOMAIN):vol.Schema({
    vol.Optional("bus",default=1):vol.All(vol.Coerce(int),vol.Range(min=0,max=32)),
    vol.Optional("address",default=0x23):vol.In([0x23,0x5c]),
    vol.Required("light_entity"):cv.entity_id,
    vol.Optional("illuminance_entity"):cv.entity_id,
})},extra=vol.ALLOW_EXTRA)


async def async_setup(hass,config):
    if DOMAIN not in config:
        return True
    controller=Controller(hass,config[DOMAIN])
    await controller.load()
    controller.register_calibration_services()
    hass.data[DOMAIN]=controller
    for platform in ("sensor","number","switch","select","button","time"):
        hass.async_create_task(discovery.async_load_platform(hass,platform,DOMAIN,{},config))

    @callback
    def start(_event=None):
        controller.unsubs.append(async_track_time_interval(hass,controller.tick,timedelta(seconds=SAMPLE_INTERVAL)))
        controller.unsubs.append(hass.bus.async_listen(EVENT_STATE_CHANGED,controller.changed))
        hass.async_create_task(controller.tick())

    @callback
    def stop(_event):
        controller.enabled=False
        if controller.calibration_task:
            controller.calibration_task.cancel()
        for unsub in controller.unsubs:
            unsub()
        controller.unsubs.clear()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP,stop)
    if hass.is_running:
        start()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED,start)
    return True


def dimmable(state):
    if not state or not state.entity_id.startswith("light."):
        return False
    modes=state.attributes.get("supported_color_modes",[])
    return bool(set(modes)-{"onoff","unknown"})


class Controller:
    def __init__(self,hass,config):
        self.hass=hass
        self.bus=config["bus"]
        self.address=config["address"]
        self.light=config["light_entity"]
        self.illuminance_entity=config.get("illuminance_entity")
        self.calibration_task=None
        self.calibrating=False
        self.calibration_progress="Idle"
        self.calibration_store=Store(hass,1,DOMAIN+"_calibration")
        self.params=dict(DEFAULTS)
        self.enabled=False
        self.observe=True
        self.edit_phase="day"
        self.listeners=set()
        self.unsubs=[]
        self.store=Store(hass,1,DOMAIN)
        self.lock=asyncio.Lock()
        self.storage_lock=asyncio.Lock()
        self.pid=PID()
        self.calibration=None
        self.calibration_enabled=False
        self.calibration_active=False
        self.calibration_status="标定不可用，使用普通 PID"
        self.update_calibration_status()
        self.lux=None
        self.raw=None
        self.measured_at=None
        self.good_time=0.0
        self.error="等待首次读取"
        self.status="自动调光已关闭"
        self.phase=None
        self.sunrise=None
        self.sunset=None
        self.windows={}
        self.next_phase=None
        self.next_boundary=None
        self.requested=None
        self.actual=None
        self.last_step=0.0
        self.next_step=0.0
        self.last_sent=0.0
        self.pending=None
        self.expected_deadline=0.0
        self.pause_until=0.0
        self.retry_after=0.0
        self.failures=0
        self.command_count=0
        self.last_command="本次启动尚未发送设备命令"
        self.result=None
        self.notice="先选择灯具并观察，再开启实控"
        self.command_context=None
        self.last_log=0.0
        self.sleep_enabled=False
        self.sleep_start=clock("23:00:00")
        self.sleep_end=clock("07:00:00")
        self.sleep_active=False
        self.sleep_processed=""
        self.sleep_override=False
        self.sleep_status="睡眠时段未启用"
        self.sleep_notice=""
        self.sleep_task=None
        self.sleep_wake=False
        self.wake_processed=""
        self.auto_owned=False
        self.auto_since=None
        self.auto_low_since=None
        self.auto_expected=None
        self.auto_deadline=0.0
        self.auto_settled=0.0
        self.auto_check=False
        self.auto_retry=0.0

    async def load(self):
        curve=await self.calibration_store.async_load()
        if curve:
            try:
                self.calibration=Calibration(curve)
            except (ValueError,KeyError,TypeError):
                _LOGGER.warning("Invalid local calibration; using ordinary PID")
        self.update_calibration_status()
        data=await self.store.async_load()
        if not data:
            return
        try:
            self.params=validated({**DEFAULTS,**data.get("params",{})})
        except (ValueError,TypeError):
            _LOGGER.warning("保存的照明参数无效，使用默认值")
        light=data.get("light")
        if isinstance(light,str) and light.startswith("light."):
            self.light=light
        if data.get("edit_phase") in PHASES:
            self.edit_phase=data["edit_phase"]
        self.observe=bool(data.get("observe",True))
        self.calibration_enabled=bool(data.get("calibration_enabled",False))
        self.update_calibration_status()
        sleep=data.get("sleep",{})
        try:
            start,end=clock(sleep.get("start","23:00:00")),clock(sleep.get("end","07:00:00"))
            if start==end:
                raise ValueError("开始与结束相同")
            self.sleep_start,self.sleep_end=start,end
            self.sleep_enabled=bool(sleep.get("enabled",False))
            self.sleep_processed=str(sleep.get("processed",""))
            self.sleep_override=bool(sleep.get("manual_override",False))
            self.sleep_notice=str(sleep.get("notice",""))
            self.sleep_wake=bool(sleep.get("wake",False))
            self.wake_processed=str(sleep.get("wake_processed",""))
        except (ValueError,TypeError):
            _LOGGER.warning("保存的睡眠时段无效，保持未启用")
        # Parameters survive restart; real control requires an explicit new start.
        self.enabled=False

    async def persist(self):
        async with self.storage_lock:
            await self.store.async_save({"params":self.params,"light":self.light,
                                        "edit_phase":self.edit_phase,"observe":self.observe,
                                        "calibration_enabled":self.calibration_enabled,
                                        "sleep":{"enabled":self.sleep_enabled,"start":self.sleep_start.isoformat(),
                                                 "end":self.sleep_end.isoformat(),"processed":self.sleep_processed,
                                                 "manual_override":self.sleep_override,"notice":self.sleep_notice,
                                                 "wake":self.sleep_wake,"wake_processed":self.wake_processed}})

    async def read_illuminance(self):
        if not self.illuminance_entity:
            return await self.hass.async_add_executor_job(read_bh1750,self.bus,self.address)
        state=self.hass.states.get(self.illuminance_entity)
        if not state or state.attributes.get("unit_of_measurement") not in ("lx","lux"):
            raise ValueError("Select a sensor reporting illuminance in lx")
        if (dt_util.utcnow()-state.last_reported).total_seconds()>20:
            raise ValueError("Illuminance sensor has stopped reporting (over 20 seconds)")
        value=float(state.state)
        if not math.isfinite(value) or not 0<=value<54612:
            raise ValueError("Invalid or saturated illuminance")
        return value,None

    def register_calibration_services(self):
        async def start(call):
            await self.start_calibration(**call.data)

        async def cancel(call):
            if self.calibration_task and not self.calibration_task.done():
                if not self.calibration_task.cancelling():
                    self.calibration_task.cancel()
                try:
                    await self.calibration_task
                except asyncio.CancelledError:
                    pass

        schema=vol.Schema({
            vol.Optional("settle",default=5):vol.All(vol.Coerce(float),vol.Range(min=2,max=60)),
            vol.Optional("samples",default=3):vol.All(vol.Coerce(int),vol.Range(min=2,max=15)),
            vol.Optional("max_drift",default=3):vol.All(vol.Coerce(float),vol.Range(min=0.1,max=50)),
        })
        self.hass.services.async_register(DOMAIN,"start_calibration",start,schema=schema)
        self.hass.services.async_register(DOMAIN,"cancel_calibration",cancel,schema=vol.Schema({}))

    async def start_calibration(self,settle=5,samples=3,max_drift=3):
        async with self.lock:
            if self.calibrating:
                raise HomeAssistantError("Calibration is already running")
            state=self.hass.states.get(self.light)
            if not dimmable(state) or state.state not in ("on","off"):
                raise HomeAssistantError("Select an available dimmable lamp")
            attrs=dict(state.attributes)
            if attrs.get("color_mode") not in ("brightness","color_temp"):
                raise HomeAssistantError("Turn the lamp on and choose brightness or color temperature mode first")
            if attrs.get("effect") not in (None,"off"):
                raise HomeAssistantError("Disable dynamic/night effects before calibration")
            if not isinstance(attrs.get("brightness"),int) or not 1<=attrs["brightness"]<=255:
                raise HomeAssistantError("Turn the lamp on once to obtain valid brightness feedback")
            if self.sleep_enabled or self.sleep_wake:
                raise HomeAssistantError("Disable sleep schedule and wake control before calibration")
            await self.read_illuminance()
            self.enabled=False
            self.calibrating=True
            self.pending=None
            self.auto_owned=False
            self.auto_expected=None
            self.auto_check=False
            self.reset()
            lamp=self.light
            was_on=state.state=="on"
            last_report=None
            expected_level=None

            def progress(message):
                self.calibration_progress=message
                self.publish()

            async def set_level(level):
                nonlocal last_report,expected_level
                expected_level=level
                service="turn_off" if level is None else "turn_on"
                data={"entity_id":lamp}
                if level is not None:
                    data["brightness"]=max(1,round(level*255/100))
                await asyncio.wait_for(self.hass.services.async_call("light",service,data,blocking=True),15)
                # Wait for HA feedback; a successful service response is not an acknowledgement.
                for _ in range(40):
                    current=self.hass.states.get(lamp)
                    if current and ((level is None and current.state=="off") or
                        (level is not None and current.state=="on" and
                         abs(current.attributes.get("brightness",-1000)-data["brightness"])<=1)):
                        if level is not None:
                            if any(current.attributes.get(k)!=attrs.get(k) for k in
                                   ("color_mode","color_temp_kelvin","effect")):
                                raise ValueError("Lamp color/effect changed during calibration")
                        last_report=dt_util.utcnow()
                        return
                    await asyncio.sleep(0.25)
                raise ValueError("Lamp did not confirm calibration brightness")

            async def read_lux():
                nonlocal last_report
                if self.illuminance_entity:
                    for _ in range(40):
                        sensor=self.hass.states.get(self.illuminance_entity)
                        if sensor and sensor.last_reported>last_report:
                            last_report=sensor.last_reported
                            break
                        await asyncio.sleep(0.25)
                    else:
                        raise ValueError("No new illuminance report during calibration")
                current=self.hass.states.get(lamp)
                if not current or current.state!=("off" if expected_level is None else "on"):
                    raise ValueError("Lamp power changed during calibration")
                if expected_level is not None:
                    expected=max(1,round(expected_level*255/100))
                    if (abs(current.attributes.get("brightness",-1000)-expected)>1 or
                        any(current.attributes.get(k)!=attrs.get(k) for k in
                            ("color_mode","color_temp_kelvin","effect"))):
                        raise ValueError("Lamp brightness/color changed during calibration")
                value,raw=await self.read_illuminance()
                if raw==65535:
                    raise ValueError("BH1750 saturated")
                return value

            async def restore():
                data={"entity_id":lamp}
                if was_on:
                    data["brightness"]=attrs["brightness"]
                await asyncio.wait_for(self.hass.services.async_call("light","turn_on" if was_on else "turn_off",data,blocking=True),15)

            async def run():
                try:
                    data=await measure_curve(lamp,attrs,set_level,read_lux,restore,progress,
                                             settle=settle,samples=samples,max_drift=max_drift)
                    curve=Calibration(data)
                    await self.calibration_store.async_save(data)
                    self.calibration=curve
                    progress("Complete; curve saved locally. Enable calibration switch to use it.")
                except asyncio.CancelledError:
                    progress("Cancelled; restoration attempted; PID remains off")
                    raise
                except Exception as exc:
                    progress(f"Failed: {exc}. Check lamp state; previous curve retained.")
                    _LOGGER.warning("Calibration failed: %s",exc)
                finally:
                    self.calibrating=False
                    self.reset()
                    self.update_calibration_status()
                    self.publish()

            self.calibration_task=self.hass.async_create_background_task(run(),"LumaLoop calibration")
            def completed(task):
                # A task cancelled before its first instruction cannot enter run's finally.
                if self.calibrating and task.cancelled():
                    self.calibrating=False
                    self.reset()
                    progress("Cancelled before sweep; PID remains off")
            self.calibration_task.add_done_callback(completed)

    def update_calibration_status(self):
        self.calibration_status=("标定模式关闭，使用普通 PID" if not self.calibration_enabled else
                                 "标定已加载，等待灯具开启及闭环计算" if self.calibration else
                                 "标定不可用，使用普通 PID")

    async def set_calibration_enabled(self,value):
        async with self.lock:
            self.calibration_enabled=bool(value)
            # Keep an outstanding command's readback, manual pause and power
            # ownership intact; only reinitialize the mathematical controller.
            self.reset()
            self.calibration_active=False
            self.update_calibration_status()
            self.notice="控制模式已切换；从当前亮度重新跟踪"
            await self.persist()
        self.publish()

    async def set_sleep_wake(self,value):
        if value and self.calibrating:
            raise HomeAssistantError("Cancel calibration before enabling sleep controls")
        async with self.lock:
            self.sleep_wake=bool(value)
            await self.persist()
        self.publish()

    def in_sleep(self):
        return sleep_window(dt_util.now(),self.sleep_start,self.sleep_end) if self.sleep_enabled else None

    async def set_sleep_enabled(self,value):
        if value and self.calibrating:
            raise HomeAssistantError("Cancel calibration before enabling sleep controls")
        async with self.lock:
            if value and not self.sleep_enabled:
                self.sleep_processed=""
                self.sleep_override=False
            self.sleep_enabled=bool(value)
            self.reset()
            await self.persist()
            await self.apply_sleep()
        self.publish()

    async def set_sleep_time(self,key,value):
        try:
            value=clock(value)
        except (ValueError,TypeError) as exc:
            raise HomeAssistantError("请输入有效的本地时间") from exc
        if key not in ("sleep_start","sleep_end"):
            raise HomeAssistantError("未知睡眠时间设置")
        other=self.sleep_end if key=="sleep_start" else self.sleep_start
        if value==other:
            raise HomeAssistantError("睡眠开始和结束时间不能相同")
        async with self.lock:
            previous=self.in_sleep()
            previous_key=f"{previous[0].isoformat()}|{previous[1].isoformat()}|{self.light}" if previous else None
            setattr(self,key,value)
            current=self.in_sleep()
            if current and previous_key==self.sleep_processed:
                # Editing an already processed occurrence must not close a
                # manually reopened lamp for a second time.
                self.sleep_processed=f"{current[0].isoformat()}|{current[1].isoformat()}|{self.light}"
            await self.persist()
            await self.apply_sleep()
        self.publish()

    async def apply_sleep(self):
        """One off attempt per window; manual re-on always wins thereafter.

        Mark the occurrence before sending the command so a restart cannot
        close a manually reopened lamp a second time. Never retries an off
        command later in the same occurrence.
        """
        window=self.in_sleep()
        active=window is not None
        if active!=self.sleep_active:
            self.reset()
        self.sleep_active=active
        if not active:
            if self.sleep_enabled and self.sleep_wake and self.sleep_processed and self.wake_processed!=self.sleep_processed:
                try:
                    _,end,entity=self.sleep_processed.split("|",2)
                    elapsed=(dt_util.now()-datetime.fromisoformat(end)).total_seconds()
                except (ValueError,TypeError):
                    elapsed=-1
                    entity=""
                if 0<=elapsed<=300 and entity==self.light:
                    self.wake_processed=self.sleep_processed
                    await self.persist()
                    light=self.hass.states.get(self.light)
                    if light and light.state in ("on","off"):
                        try:
                            context=Context()
                            self.command_context=context.id
                            if light.state=="off":
                                await asyncio.wait_for(self.hass.services.async_call("light","turn_on",{"entity_id":self.light},blocking=True,context=context),timeout=15)
                            self.observe=False
                            self.enabled=True
                            self.pause_until=0
                            self.pending=None
                            self.reset()
                            self.sleep_notice="睡眠结束：已请求开灯并启用 PID；等待灯具及传感器反馈"
                            await self.persist()
                        except Exception as exc:
                            self.sleep_notice=f"睡眠结束开灯失败：{type(exc).__name__}"
                    else:
                        self.sleep_notice="睡眠结束时灯具不可用，未开灯"
                    await self.persist()
            self.sleep_status="睡眠时段未启用" if not self.sleep_enabled else "等待睡眠时段"
            return False
        key=f"{window[0].isoformat()}|{window[1].isoformat()}|{self.light}"
        if key!=self.sleep_processed:
            self.sleep_processed=key
            self.sleep_override=False
            self.pending=None
            self.pause_until=0
            self.reset()
            light=self.hass.states.get(self.light)
            self.sleep_notice="本次入睡处理已登记"
            await self.persist()
            if light and light.state=="on":
                context=Context()
                self.command_context=context.id
                try:
                    await asyncio.wait_for(self.hass.services.async_call(
                        "light","turn_off",{"entity_id":self.light},blocking=True,context=context),timeout=15)
                    self.command_count+=1
                    self.last_command=f"{dt_util.now():%H:%M:%S} 睡眠开始 → 关灯（仅一次）"
                    self.sleep_notice="已发送本次睡眠关灯命令；以灯具回读为准"
                except Exception as exc:
                    self.sleep_notice=f"本次睡眠关灯失败：{type(exc).__name__}；不会反复关灯"
                    _LOGGER.warning("睡眠开始关灯失败: %s",exc)
            elif light and light.state=="off":
                self.sleep_notice="进入睡眠时灯已关闭，无需发命令"
            else:
                self.sleep_notice="进入睡眠时灯具不可用；本次不补发关灯"
            await self.persist()
        self.sleep_status="睡眠中 · 手动开灯优先，不再自动关灯" if self.sleep_override else "睡眠中 · 已处理入睡关灯，暂停 PID"
        self.status=self.sleep_status
        return True

    async def save_sleep_override(self):
        await self.persist()
        self.publish()

    @callback
    def publish(self):
        for listener in tuple(self.listeners):
            listener()

    def lamps(self):
        states=sorted((s for s in self.hass.states.async_all("light") if dimmable(s)),
                      key=lambda s:(s.entity_id!=self.light,s.attributes.get("friendly_name",s.entity_id)))
        return {s.entity_id:self.lamp_label(s) for s in states}

    @staticmethod
    def lamp_label(state):
        return f"{state.attributes.get('friendly_name',state.entity_id)} [{state.entity_id}]"

    def reset(self):
        self.pid.reset()
        self.result=None
        self.requested=None
        self.last_step=0.0
        self.next_step=0.0
        self.auto_since=None
        self.auto_low_since=None

    async def set_param(self,key,value):
        candidate={**self.params,key:float(value)}
        try:
            self.params=validated(candidate)
        except (ValueError,TypeError) as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.reset()
        self.notice="参数已保存；PID 从当前亮度重新跟踪"
        await self.persist()
        self.publish()

    async def choose_light(self,label):
        if self.calibrating:
            raise HomeAssistantError("Cancel calibration before changing the lamp")
        match=[entity for entity,name in self.lamps().items() if name==label]
        if not match:
            raise HomeAssistantError("未找到可调光灯具")
        async with self.lock:
            self.enabled=False
            self.light=match[0]
            self.auto_owned=False
            self.auto_expected=None
            self.auto_check=False
            self.pending=None
            self.pause_until=0
            self.reset()
            self.status="灯具已切换，自动控制已停止"
            await self.persist()
        self.publish()

    async def choose_phase(self,label):
        self.edit_phase=next(k for k,v in PHASES.items() if v==label)
        await self.persist()
        self.publish()

    async def set_enabled(self,value):
        if value and self.calibrating:
            raise HomeAssistantError("Calibration is running")
        async with self.lock:
            if value:
                state=self.hass.states.get(self.light)
                if not dimmable(state) or state.state not in ("on","off"):
                    raise HomeAssistantError("所选灯具离线或不支持调光")
                if not self.fresh() or self.raw==65535:
                    raise HomeAssistantError("光照传感器无有效读数，暂不能开启")
                self.update_sun()
                if self.phase is None:
                    raise HomeAssistantError("位置/日出日落数据不可用")
            self.enabled=bool(value)
            self.auto_owned=False
            self.auto_expected=None
            self.auto_check=False
            self.pending=None
            self.pause_until=0
            self.failures=0
            self.retry_after=0
            self.reset()
            self.status=("仅观察，等待计算" if self.observe else "等待下一次闭环调节") if value else "自动调光已关闭；保留灯具当前状态"
        self.publish()

    async def set_observe(self,value):
        async with self.lock:
            self.enabled=False
            self.observe=bool(value)
            self.auto_owned=False
            self.auto_expected=None
            self.auto_check=False
            self.pending=None
            self.reset()
            self.status="模式已切换，自动控制已停止"
            await self.persist()
        self.publish()

    def fresh(self):
        return self.lux is not None and monotonic_time.monotonic()-self.good_time<=20

    def update_sun(self):
        now=dt_util.now()
        old=self.phase
        try:
            location,elevation=get_astral_location(self.hass)
            # Use the location's LOCAL calendar date. UTC-mode Astral events
            # can put an eastern timezone's morning sunrise on tomorrow.
            try:
                self.sunrise=location.sunrise(now.date(),local=True,observer_elevation=elevation)
                self.sunset=location.sunset(now.date(),local=True,observer_elevation=elevation)
            except ValueError:
                self.sunrise=self.sunset=None
            self.phase=phase_at(now,self.sunrise,self.sunset,self.params["twilight"])
            self.windows={}
            self.next_boundary=None
            self.next_phase=None
            if self.phase:
                window=timedelta(minutes=self.params["twilight"])
                r,s=self.sunrise,self.sunset
                self.windows={"sunrise":f"{(r-window):%H:%M}–{(r+window):%H:%M}",
                              "day":f"{(r+window):%H:%M}–{(s-window):%H:%M}",
                              "sunset":f"{(s-window):%H:%M}–{(s+window):%H:%M}",
                              "night":f"{(s+window):%H:%M}–次日日出窗口开始"}
                boundaries=[(r-window,"sunrise"),(r+window,"day"),(s-window,"sunset"),(s+window,"night")]
                upcoming=[(dt,key) for dt,key in boundaries if dt>now]
                if not upcoming:
                    tomorrow=location.sunrise(now.date()+timedelta(days=1),local=True,observer_elevation=elevation)
                    if tomorrow:
                        upcoming=[(dt_util.as_local(tomorrow)-window,"sunrise")]
                if upcoming:
                    self.next_boundary,self.next_phase=min(upcoming)
        except Exception as exc:
            self.phase=None
            self.notice=f"太阳时段计算失败：{type(exc).__name__}"
        if old!=self.phase:
            self.reset()

    @callback
    def changed(self,event):
        if self.calibrating:
            return
        entity=event.data.get("entity_id","")
        if entity!=self.light:
            # Refresh selection when light availability or registration changes.
            if entity.startswith("light.") and (not event.data.get("old_state") or not event.data.get("new_state")):
                self.publish()
            return
        old,new=event.data.get("old_state"),event.data.get("new_state")
        window=self.in_sleep()
        if window:
            if new and new.state=="on" and (not old or old.state!="on"):
                self.sleep_processed=f"{window[0].isoformat()}|{window[1].isoformat()}|{self.light}"
                self.sleep_override=True
                self.sleep_notice="已保留睡眠期间的手动开灯；本时段不再自动关灯"
                self.reset()
                self.sleep_status=self.status="睡眠中 · 手动开灯优先，不再自动关灯"
                if not self.sleep_task or self.sleep_task.done():
                    self.sleep_task=self.hass.async_create_task(self.save_sleep_override())
                self.publish()
            return
        if not new or new.state not in ("on","off"):
            self.pending=None
            self.reset()
            self.publish()
            return
        if not old or not self.enabled or self.observe:
            self.publish()
            return
        before,after=old.attributes.get("brightness"),new.attributes.get("brightness")
        if before==after and old.state==new.state:
            return
        if self.auto_expected and new.state==self.auto_expected and monotonic_time.monotonic()<=self.auto_deadline:
            return
        if self.pending is not None and new.state=="on" and isinstance(after,(int,float)) and abs(after-self.pending)<=1:
            self.pending=None
            self.failures=0
            return
        if self.command_context and new.context.id==self.command_context:
            return
        # Manual light operations take priority over automation.
        self.auto_owned=False
        self.auto_expected=None
        self.auto_check=False
        self.pause_until=monotonic_time.monotonic()+self.params["manual_pause"]
        self.pending=None
        self.reset()
        self.status="手动关灯，保持关闭" if new.state=="off" else "检测到手动调灯，临时暂停"
        self.publish()

    async def auto_power(self,on,now):
        """Set expected state before the service call, including contextless echoes."""
        self.reset()
        self.pending=None
        self.auto_expected="on" if on else "off"
        self.auto_deadline=now+30
        self.auto_settled=0
        self.auto_owned=not on
        self.auto_check=not on
        context=Context()
        self.command_context=context.id
        data={"entity_id":self.light}
        if on:
            data["brightness"]=max(1,round(self.params[self.phase+"_brightness_min"]*255/100))
        try:
            await asyncio.wait_for(self.hass.services.async_call("light","turn_on" if on else "turn_off",data,blocking=True,context=context),15)
            self.command_count+=1
            self.last_command=f"{dt_util.now():%H:%M:%S} 自然光控制 → {'开灯' if on else '关灯'}"
            self.status="等待开关灯回读及照度稳定"
        except Exception as exc:
            self.auto_expected=None
            self.auto_owned=False
            self.auto_check=False
            self.enabled=False
            self.status=f"自动开关灯失败：{type(exc).__name__}；已停止控制"

    async def auto_daylight(self,light,now):
        if self.observe:
            self.auto_since=self.auto_low_since=None
            return False
        if self.auto_expected:
            if light.state!=self.auto_expected:
                if now>=self.auto_deadline:
                    self.auto_expected=None
                    self.auto_owned=False
                    self.auto_check=False
                    self.enabled=False
                    self.status="开关灯回读超时；已停止控制"
                else:
                    self.status="等待开关灯状态回读"
                return True
            if not self.auto_settled:
                self.auto_settled=now+self.params["auto_settle"]
            if now<self.auto_settled:
                self.status="开关灯已确认，等待照度稳定"
                return True
            self.auto_expected=None
            self.reset()
        lo=self.params[self.phase+"_lux_min"]
        hi=self.params[self.phase+"_lux_max"]
        if self.auto_owned and light.state=="off":
            if self.auto_check:
                self.auto_check=False
                if self.lux<=lo:
                    self.auto_retry=now+300
                    await self.auto_power(True,now)
                    self.status="关灯后照度不足，已请求开灯；5 分钟内不再试关"
                    return True
            if self.lux<lo:
                if self.auto_low_since is None:
                    self.auto_low_since=now
                if now-self.auto_low_since>=self.params["auto_on_delay"]:
                    await self.auto_power(True,now)
                    return True
                self.status="自然光不足，开灯确认计时中"
            else:
                self.auto_low_since=None
                self.status="自然光充足，自动关灯待机"
            return True
        # Only a lamp at its configured floor can be judged for daylight shutdown.
        floor=max(1,round(self.params[self.phase+"_brightness_min"]*255/100))
        brightness=light.attributes.get("brightness")
        eligible=(light.state=="on" and isinstance(brightness,(int,float)) and brightness<=floor+1
                  and self.pending is None and now>=self.auto_retry
                  and self.lux>hi+self.params["auto_off_margin"])
        if not eligible:
            self.auto_since=None
            return False
        if self.auto_since is None:
            self.auto_since=now
        elapsed=now-self.auto_since
        if elapsed>=self.params["auto_off_delay"]:
            await self.auto_power(False,now)
        else:
            self.status=f"自然光关灯确认：{elapsed:.0f}/{self.params['auto_off_delay']:.0f} 秒"
        return True

    async def tick(self,_now=None):
        if self.calibrating or self.lock.locked():
            return
        async with self.lock:
            await self._tick()

    async def _tick(self):
        now=monotonic_time.monotonic()
        # Sleep entry runs before I2C and independently of PID/observe switches.
        sleeping=await self.apply_sleep()
        try:
            self.lux,self.raw=await self.read_illuminance()
            self.good_time=monotonic_time.monotonic()
            self.measured_at=dt_util.utcnow()
            self.error=""
        except Exception as exc:
            self.error=f"{type(exc).__name__}: {exc}"
            self.lux=None
            self.reset()
            self.status=self.sleep_status if sleeping else "传感器读取失败，已暂停调光"
            if now-self.last_log>60:
                _LOGGER.warning("BH1750 读取失败: %s",self.error)
                self.last_log=now
            self.publish()
            return
        self.update_sun()
        light=self.hass.states.get(self.light)
        brightness=light.attributes.get("brightness") if light else None
        self.actual=round(brightness/255*100,2) if isinstance(brightness,(int,float)) and math.isfinite(brightness) else None
        if sleeping:
            self.auto_owned=False
            self.auto_expected=None
            self.auto_check=False
            self.status=self.sleep_status
            self.publish()
            return
        if not self.enabled:
            self.status="自动调光已关闭；实时照度持续更新"
            self.publish()
            return
        if not self.fresh() or self.raw==65535:
            self.status="照度过期或达到量程上限，暂停调光"
            self.reset()
        elif self.phase is None:
            self.status="太阳时段不可用，暂停调光"
            self.reset()
        elif not dimmable(light) or light.state=="unavailable":
            self.status="灯具离线或不支持调光，暂停"
            self.reset()
        elif now<self.pause_until:
            self.status=f"手动操作优先，暂停 {math.ceil(self.pause_until-now)} 秒"
        elif now<self.retry_after:
            self.status="设备命令退避，等待后重试"
        elif await self.auto_daylight(light,now):
            pass
        elif light.state!="on":
            self.status="灯具已关闭；不自动开灯"
            self.reset()
        elif self.actual is None:
            self.status="灯具没有有效亮度反馈，暂停"
            self.reset()
        elif now<self.pause_until:
            self.status=f"手动操作优先，暂停 {math.ceil(self.pause_until-now)} 秒"
        elif now<self.retry_after:
            self.status="设备命令退避，等待后重试"
        elif self.pending is not None and abs(brightness-self.pending)>1:
            if now>self.expected_deadline:
                self.pending=None
                self.failures+=1
                self.reset()
                self.retry_after=now+min(120,15*2**min(self.failures,3))
                self.status="未收到目标亮度回读，暂停并退避"
                if self.failures>=3:
                    self.enabled=False
                    self.status="连续三次未确认亮度，已停止自动调光"
        else:
            # A confirmed readback must not consume an otherwise eligible tick.
            # The event listener may already have acknowledged it; this also
            # covers polling-only updates and updates with unchanged brightness.
            if self.pending is not None:
                self.pending=None
                self.failures=0
            await self.pid_tick(now,light)
        self.publish()

    async def pid_tick(self,now,light):
        brightness=light.attributes["brightness"]
        # Keep the deadline anchored to the cadence instead of the previous
        # callback's jitter. Allow at most 50 ms early arrival on the timer;
        # otherwise a 4.999 s difference can accidentally skip a full tick.
        if self.last_step==0 or now+0.05>=self.next_step:
            dt=self.params["interval"] if not self.last_step else now-self.last_step
            interval=self.params["interval"]
            deadline=self.next_step
            next_deadline=deadline+interval
            self.next_step=(next_deadline if deadline and next_deadline>now+0.05 else now+interval)
            self.last_step=now
            active,reason=((False,"标定模式关闭，使用普通 PID") if not self.calibration_enabled else
                           self.calibration.applicable(self.light,light.attributes) if self.calibration else
                           (False,"标定不可用，使用普通 PID"))
            if active!=self.calibration_active:
                self.pid.reset()
                self.calibration_active=active
            self.calibration_status=reason
            result=self.pid.step(self.lux,self.actual,dt,self.params,self.phase,
                                 self.calibration if active else None)
            self.result=result
            requested=max(1,min(255,round(result.output*255/100)))
            raw_step=max(1,math.floor(self.params["max_step"]*255/100))
            requested=max(1,min(255,max(brightness-raw_step,min(brightness+raw_step,requested))))
            self.requested=round(requested/255*100,2)
            within=self.params[self.phase+"_brightness_min"]<=self.actual<=self.params[self.phase+"_brightness_max"]
            if self.observe:
                self.status="仅观察：在照度区间内，保持" if result.in_band else "仅观察：计算完成，不发灯具命令"
            elif abs(requested-brightness)<2:
                direction="低于下限" if result.filtered<self.params[self.phase+"_lux_min"] else "高于上限"
                self.status=("照度已在区间内，保持亮度" if result.in_band and within else
                             f"滤波照度{direction}；本轮亮度差不足发令步长")
            else:
                context=Context()
                self.command_context=context.id
                self.pending=requested
                self.expected_deadline=now+max(30,self.params["interval"]*2)
                data={"entity_id":self.light,"brightness":requested}
                # Use transition only when the actual lamp advertises support.
                if int(light.attributes.get("supported_features",0)) & 32:
                    data["transition"]=min(5,self.params["interval"]/2)
                try:
                    await self.hass.services.async_call("light","turn_on",data,blocking=True,context=context)
                    self.last_sent=now
                    self.command_count+=1
                    self.last_command=f"{dt_util.now():%H:%M:%S} → {self.requested:.1f}%（等待 HA 回读）"
                    self.status="已平滑调节，等待亮度回读"
                except Exception as exc:
                    self.pending=None
                    self.failures+=1
                    self.reset()
                    self.retry_after=now+min(120,15*2**min(self.failures,3))
                    self.status=f"灯具调用失败：{type(exc).__name__}"
                    _LOGGER.warning("照明命令失败: %s",exc)
                    if self.failures>=3:
                        self.enabled=False
                        self.status="连续三次命令失败，已停止自动调光"
