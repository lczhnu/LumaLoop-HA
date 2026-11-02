from homeassistant.components.switch import SwitchEntity
from . import DOMAIN
from .entity import LightingEntity


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    c=hass.data[DOMAIN]
    async_add_entities([ControlSwitch(c,"switch","enabled","启用 PID 自动调光","mdi:lightbulb-auto"),
                        ControlSwitch(c,"switch","observe","仅观察，不调灯具","mdi:eye-outline"),
                        ControlSwitch(c,"switch","calibration_enabled","使用标定曲线控制","mdi:chart-bell-curve-cumulative"),
                        ControlSwitch(c,"switch","sleep_enabled","启用睡眠时段","mdi:power-sleep"),
                        ControlSwitch(c,"switch","sleep_wake","睡眠结束后开灯并启用 PID","mdi:alarm-light")])


class ControlSwitch(LightingEntity,SwitchEntity):
    @property
    def is_on(self):
        return getattr(self.control,self.key)

    async def async_turn_on(self,**kwargs):
        await getattr(self.control,"set_"+self.key)(True)

    async def async_turn_off(self,**kwargs):
        await getattr(self.control,"set_"+self.key)(False)
