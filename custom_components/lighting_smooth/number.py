from homeassistant.components.number import NumberEntity,NumberMode
from . import DOMAIN
from .engine import PARAMS
from .entity import LightingEntity


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    async_add_entities([Parameter(hass.data[DOMAIN],key,spec) for key,spec in PARAMS.items()])


class Parameter(LightingEntity,NumberEntity):
    def __init__(self,controller,key,spec):
        name,low,high,step,unit=spec
        super().__init__(controller,"number",key,name,"mdi:tune-vertical")
        self._attr_native_min_value=low
        self._attr_native_max_value=high
        self._attr_native_step=step
        self._attr_native_unit_of_measurement=unit
        self._attr_mode=NumberMode.BOX

    @property
    def native_value(self):
        return self.control.params[self.key]

    async def async_set_native_value(self,value):
        await self.control.set_param(self.key,value)
