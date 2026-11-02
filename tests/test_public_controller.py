import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock,patch
from test_ha_controller import HAController
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from custom_components.lighting_smooth import Controller,CONFIG_SCHEMA,DOMAIN


class PublicController(HAController):
    async def test_configuration_requires_explicit_lamp(self):
        import voluptuous as vol
        with self.assertRaises(vol.Invalid):
            CONFIG_SCHEMA({DOMAIN:{}})

    async def test_entity_sensor_rejects_missing_nonfinite_wrong_units_and_stale(self):
        self.c.illuminance_entity="sensor.example_lux"
        for value,unit in [("unknown","lx"),("nan","lx"),("10","W"),("54613","lx")]:
            self.hass.states.async_set(self.c.illuminance_entity,value,{"unit_of_measurement":unit})
            with self.assertRaises(ValueError):
                await self.c.read_illuminance()
        self.hass.states.async_set(self.c.illuminance_entity,"10",{"unit_of_measurement":"lx"})
        self.assertEqual(await self.c.read_illuminance(),(10,None))
        with patch("custom_components.lighting_smooth.dt_util.utcnow",return_value=dt_util.utcnow()+timedelta(seconds=30)):
            with self.assertRaises(ValueError):
                await self.c.read_illuminance()

    async def test_calibration_excludes_tick_and_target_change(self):
        self.c.calibrating=True
        await self.c.tick()
        self.hass.async_add_executor_job.assert_not_awaited()
        with self.assertRaises(HomeAssistantError):
            await self.c.choose_light(self.c.lamps()[self.c.light])
        with self.assertRaises(HomeAssistantError):
            await self.c.set_enabled(True)
        with self.assertRaises(HomeAssistantError):
            await self.c.set_sleep_enabled(True)

    async def test_calibration_persists_after_restore_and_leaves_pid_off(self):
        self.hass.states.async_set(self.c.light,"on",{
            "brightness":100,"supported_color_modes":["brightness"],"color_mode":"brightness"})
        self.c.calibration_store.async_save=AsyncMock()
        data={"schema_version":1,"lamp":self.c.light,"color_mode":"brightness",
              "color_temp_kelvin":[],"curve":[{"percent":1,"lamp_lux":2},{"percent":100,"lamp_lux":200}]}
        with patch("custom_components.lighting_smooth.measure_curve",new=AsyncMock(return_value=data)):
            await self.c.start_calibration()
            await self.c.calibration_task
        self.assertFalse(self.c.enabled)
        self.assertFalse(self.c.calibrating)
        self.c.calibration_store.async_save.assert_awaited_once_with(data)
        self.assertIsNotNone(self.c.calibration)

    async def test_bad_curve_keeps_old_model_and_storage(self):
        self.hass.states.async_set(self.c.light,"on",{
            "brightness":100,"supported_color_modes":["brightness"],"color_mode":"brightness"})
        self.c.calibration_store.async_save=AsyncMock()
        sentinel=object()
        self.c.calibration=sentinel
        with patch("custom_components.lighting_smooth.measure_curve",new=AsyncMock(side_effect=ValueError("drift"))):
            await self.c.start_calibration()
            await self.c.calibration_task
        self.assertIs(self.c.calibration,sentinel)
        self.c.calibration_store.async_save.assert_not_awaited()

    async def test_actual_sweep_callbacks_restore_original_brightness(self):
        self.hass.states.async_set(self.c.light,"on",{
            "brightness":100,"supported_color_modes":["brightness"],"color_mode":"brightness"})
        async def service(domain,action,data,**kwargs):
            current=self.hass.states.get(self.c.light)
            attrs=dict(current.attributes)
            if "brightness" in data:
                attrs["brightness"]=data["brightness"]
            self.hass.states.async_set(self.c.light,"on" if action=="turn_on" else "off",attrs)
        self.hass.services.async_call.side_effect=service
        async def read(*args):
            state=self.hass.states.get(self.c.light)
            return (10+(state.attributes["brightness"] if state.state=="on" else 0),100)
        self.hass.async_add_executor_job.side_effect=read
        self.c.calibration_store.async_save=AsyncMock()
        with patch("custom_components.lighting_smooth.calibration_runner.asyncio.sleep",new=AsyncMock()):
            # Explicit wait injection is necessary because measure_curve binds its default.
            from custom_components.lighting_smooth.calibration_runner import measure_curve
            async def fast(*args,**kwargs):
                return await measure_curve(*args,**kwargs,wait=AsyncMock())
            with patch("custom_components.lighting_smooth.measure_curve",new=fast):
                await self.c.start_calibration()
                await self.c.calibration_task
        self.c.calibration_store.async_save.assert_awaited_once()
        self.assertEqual(self.hass.states.get(self.c.light).attributes["brightness"],100)
        self.assertEqual(self.hass.states.get(self.c.light).state,"on")
        self.assertFalse(self.c.enabled)
        self.assertFalse(self.c.calibrating)

    async def test_cancel_before_task_starts_releases_calibration(self):
        self.hass.states.async_set(self.c.light,"on",{
            "brightness":100,"supported_color_modes":["brightness"],"color_mode":"brightness"})
        with patch.object(self.hass,"async_create_background_task",
                          side_effect=lambda coro,name:asyncio.create_task(coro)):
            await self.c.start_calibration()
        self.c.calibration_task.cancel()
        try:
            await self.c.calibration_task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        self.assertFalse(self.c.calibrating)
        self.assertFalse(self.c.enabled)
        self.hass.services.async_call.assert_not_awaited()
