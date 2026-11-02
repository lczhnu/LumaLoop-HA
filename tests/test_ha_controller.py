"""Runs against the Pi's installed HA package; never controls real lights."""
import asyncio
from datetime import datetime,timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

sys.path.insert(0,str(Path(__file__).parents[1]))
from homeassistant.core import HomeAssistant,Context,Event
from homeassistant.helpers import config_validation as cv
from custom_components.lighting_smooth import Controller,CONFIG_SCHEMA,DOMAIN
from custom_components.lighting_smooth import number,sensor,switch,select,button
from custom_components.lighting_smooth import time as time_platform
from custom_components.lighting_smooth.engine import DEFAULT_LIGHT


class HAController(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.hass=HomeAssistant(self.tmp.name)
        self.hass.config.time_zone='Asia/Shanghai'
        self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':158,'supported_color_modes':['color_temp'],'supported_features':4})
        self.c=Controller(self.hass,{'bus':1,'address':0x23,'light_entity':DEFAULT_LIGHT})
        self.c.persist=AsyncMock()
        self.c.update_sun=lambda:setattr(self.c,'phase','day')
        self.hass.async_add_executor_job=AsyncMock(return_value=(20,24))
        self.service_patch=patch.object(type(self.hass.services),'async_call',new=AsyncMock())
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        self.tmp.cleanup()

    async def test_real_ha_schema_and_all_platforms(self):
        with patch.object(cv,'_async_get_hass_or_none',return_value=self.hass):
            conf=CONFIG_SCHEMA({DOMAIN:{'light_entity':DEFAULT_LIGHT}})
        self.assertEqual(conf[DOMAIN]['address'],0x23)
        self.hass.data[DOMAIN]=self.c
        values=[]
        for platform in (number,sensor,switch,select,button,time_platform):
            await platform.async_setup_platform(self.hass,{},values.extend,{})
        self.assertEqual(len(values),54)
        ids=[e.entity_id for e in values]
        self.assertEqual(len(ids),len(set(ids)))
        for e in values:
            e.hass=self.hass
            self.assertTrue(e.name)
        self.assertEqual(len(self.c.lamps()),1)

    async def test_disabled_observation_and_off_never_send(self):
        await self.c._tick()
        self.assertEqual(self.c.lux,20)
        self.hass.services.async_call.assert_not_awaited()
        self.c.enabled=True
        await self.c._tick()
        self.assertIsNotNone(self.c.requested)
        self.hass.services.async_call.assert_not_awaited()
        self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
        self.c.observe=False
        self.c.last_step=0
        await self.c._tick()
        self.hass.services.async_call.assert_not_awaited()

    async def test_sensor_fault_stops_output(self):
        self.c.enabled=True
        self.c.observe=False
        self.hass.async_add_executor_job.side_effect=OSError('simulated unplug')
        await self.c._tick()
        self.assertIsNone(self.c.lux)
        self.assertFalse(self.c.fresh())
        self.hass.services.async_call.assert_not_awaited()

    async def test_saturation_stops_output(self):
        self.c.enabled=True
        self.c.observe=False
        self.hass.async_add_executor_job.return_value=(54612.5,65535)
        await self.c._tick()
        self.hass.services.async_call.assert_not_awaited()

    async def test_real_command_is_bounded_and_waits_for_ack(self):
        self.c.enabled=True
        self.c.observe=False
        await self.c._tick()
        self.hass.services.async_call.assert_awaited_once()
        args=self.hass.services.async_call.call_args.args
        self.assertEqual(args[:2],('light','turn_on'))
        self.assertEqual(args[2]['entity_id'],DEFAULT_LIGHT)
        self.assertNotIn('transition',args[2])
        self.assertLessEqual(abs(args[2]['brightness']-158),8)
        await self.c._tick()
        self.hass.services.async_call.assert_awaited_once()

    async def test_three_ack_failures_disable(self):
        self.c.enabled=True
        self.c.observe=False
        self.c.failures=2
        self.c.pending=200
        self.c.expected_deadline=0
        await self.c._tick()
        self.assertFalse(self.c.enabled)
        self.hass.services.async_call.assert_not_awaited()

    async def test_mode_and_target_changes_disable(self):
        self.c.enabled=True
        await self.c.set_observe(False)
        self.assertFalse(self.c.enabled)
        self.c.enabled=True
        await self.c.choose_light(self.c.lamps()[DEFAULT_LIGHT])
        self.assertFalse(self.c.enabled)
        self.assertFalse(self.c.observe)

    async def test_manual_brightness_pauses(self):
        self.c.enabled=True
        self.c.observe=False
        old=self.hass.states.get(DEFAULT_LIGHT)
        self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':180,'supported_color_modes':['color_temp']},context=Context())
        new=self.hass.states.get(DEFAULT_LIGHT)
        self.c.changed(Event('state_changed',{'entity_id':DEFAULT_LIGHT,'old_state':old,'new_state':new}))
        self.assertGreater(self.c.pause_until,0)
        await self.c._tick()
        self.hass.services.async_call.assert_not_awaited()

    async def test_eastern_timezone_uses_todays_sunrise(self):
        from astral import LocationInfo
        from astral.location import Location
        from zoneinfo import ZoneInfo
        location=Location(LocationInfo('','','Asia/Shanghai',35.0,135.0))
        now=datetime(2026,10,31,12,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch('custom_components.lighting_smooth.get_astral_location',return_value=(location,0)),patch('custom_components.lighting_smooth.dt_util.now',return_value=now):
            Controller.update_sun(self.c)
        self.assertEqual(self.c.phase,'day')
        self.assertEqual(self.c.sunrise.date(),now.date())
        self.assertLess(self.c.sunrise,now)
        self.assertGreater(self.c.sunset,now)


if __name__=='__main__':unittest.main(verbosity=2)
