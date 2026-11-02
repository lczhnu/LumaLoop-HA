from homeassistant.components.time import TimeEntity
from . import DOMAIN
from .entity import LightingEntity


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    c=hass.data[DOMAIN]
    async_add_entities([SleepTime(c,"time","sleep_start","睡眠开始时间","mdi:sleep"),
                        SleepTime(c,"time","sleep_end","睡眠结束时间","mdi:weather-sunset-up")])


class SleepTime(LightingEntity,TimeEntity):
    @property
    def native_value(self):
        return getattr(self.control,self.key)

    async def async_set_value(self,value):
        await self.control.set_sleep_time(self.key,value)
