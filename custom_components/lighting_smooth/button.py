from homeassistant.components.button import ButtonEntity
from . import DOMAIN
from .entity import LightingEntity


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    async_add_entities([Reset(hass.data[DOMAIN],"button","reset","重置积分并解除暂停","mdi:restart")])


class Reset(LightingEntity,ButtonEntity):
    async def async_press(self):
        async with self.control.lock:
            self.control.pause_until=0
            self.control.retry_after=0
            self.control.pending=None
            self.control.failures=0
            self.control.reset()
            self.control.notice="积分已重置，下一周期重新跟踪当前亮度"
        self.control.publish()
