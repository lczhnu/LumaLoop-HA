from homeassistant.helpers.entity import Entity


class LightingEntity(Entity):
    _attr_should_poll=False

    def __init__(self,controller,platform,key,name,icon):
        self.control=controller
        self.key=key
        self.entity_id=f"{platform}.lighting_smooth_{key}"
        self._attr_unique_id=f"lighting_smooth_{key}"
        self._attr_name=f"照明 {name}"
        self._attr_icon=icon

    async def async_added_to_hass(self):
        self.control.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda:self.control.listeners.discard(self.async_write_ha_state))
