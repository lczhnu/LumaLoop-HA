from homeassistant.components.select import SelectEntity
from . import DOMAIN
from .engine import PHASES
from .entity import LightingEntity


async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    c=hass.data[DOMAIN]
    async_add_entities([TargetLight(c,"select","light","控制哪盏灯","mdi:ceiling-light"),
                        EditPhase(c,"select","edit_phase","编辑哪个时段","mdi:theme-light-dark")])


class TargetLight(LightingEntity,SelectEntity):
    @property
    def options(self):
        opts=list(self.control.lamps().values())
        current=self.current_option
        if current not in opts:
            opts.insert(0,current)
        return opts

    @property
    def current_option(self):
        state=self.hass.states.get(self.control.light)
        return self.control.lamp_label(state) if state else self.control.light

    async def async_select_option(self,option):
        await self.control.choose_light(option)


class EditPhase(LightingEntity,SelectEntity):
    _attr_options=list(PHASES.values())

    @property
    def current_option(self):
        return PHASES[self.control.edit_phase]

    async def async_select_option(self,option):
        await self.control.choose_phase(option)
