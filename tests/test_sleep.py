"""Sleep behavior tests: use real HA classes, no real lamp/API access."""
from datetime import datetime,time,timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).parents[1]))
from test_ha_controller import HAController
from homeassistant.core import Context,Event
from custom_components.lighting_smooth import Controller
from custom_components.lighting_smooth.engine import DEFAULT_LIGHT
from custom_components.lighting_smooth.sleep_schedule import clock,sleep_window

TZ=ZoneInfo('Asia/Shanghai')


class Windows(unittest.TestCase):
    def test_cross_midnight_and_end_exclusive(self):
        for h,active in [(22,False),(23,True),(0,True),(6,True),(7,False),(12,False)]:
            now=datetime(2026,10,31,h,tzinfo=TZ)
            window=sleep_window(now,'23:00','07:00')
            self.assertEqual(window is not None,active)
            if h==0:
                self.assertEqual(window[0].day,30)

    def test_same_day_and_equal_invalid(self):
        now=datetime(2026,10,31,14,tzinfo=TZ)
        self.assertIsNotNone(sleep_window(now,'13:00','15:00'))
        self.assertIsNone(sleep_window(now,'15:00','16:00'))
        with self.assertRaises(ValueError):sleep_window(now,'23:00','23:00')


class SleepController(HAController):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.now=datetime(2026,10,31,23,30,tzinfo=TZ)
        self.clock_patch=patch('custom_components.lighting_smooth.dt_util.now',side_effect=lambda:self.now)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.c.sleep_enabled=True
        self.c.enabled=True
        self.c.observe=False

    async def test_entry_once_and_manual_reopen(self):
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.call_args.args[:2],('light','turn_off'))
        self.hass.services.async_call.assert_awaited_once()
        self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
        old=self.hass.states.get(DEFAULT_LIGHT)
        self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':200,'supported_color_modes':['color_temp']},context=Context())
        self.c.changed(Event('state_changed',{'entity_id':DEFAULT_LIGHT,'old_state':old,'new_state':self.hass.states.get(DEFAULT_LIGHT)}))
        await self.c._tick()
        await self.c._tick()
        self.assertTrue(self.c.sleep_override)
        self.hass.services.async_call.assert_awaited_once()

    async def test_manual_on_before_first_tick_is_preserved(self):
        self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
        old=self.hass.states.get(DEFAULT_LIGHT)
        self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':100,'supported_color_modes':['color_temp']})
        self.c.changed(Event('state_changed',{'entity_id':DEFAULT_LIGHT,'old_state':old,'new_state':self.hass.states.get(DEFAULT_LIGHT)}))
        await self.c._tick()
        self.hass.services.async_call.assert_not_awaited()

    async def test_independent_of_pid_observation_and_sensor_fault(self):
        self.c.enabled=False
        self.c.observe=True
        self.hass.async_add_executor_job.side_effect=OSError('unplug')
        await self.c._tick()
        self.hass.services.async_call.assert_awaited_once()
        self.assertIn('睡眠中',self.c.status)

    async def test_restart_does_not_reclose_manual_lamp(self):
        await self.c.apply_sleep()
        stored={'params':self.c.params,'sleep':{'enabled':True,'start':'23:00:00','end':'07:00:00',
                'processed':self.c.sleep_processed,'manual_override':True}}
        restored=Controller(self.hass,{'bus':1,'address':0x23,'light_entity':DEFAULT_LIGHT})
        restored.store=AsyncMock()
        restored.store.async_load.return_value=stored
        restored.persist=AsyncMock()
        restored.calibration_store.async_load=AsyncMock(return_value=None)
        await restored.load()
        self.hass.services.async_call.reset_mock()
        await restored.apply_sleep()
        self.assertTrue(restored.sleep_override)
        self.hass.services.async_call.assert_not_awaited()

    async def test_sleep_end_does_not_turn_on_and_next_day_rearms(self):
        await self.c.apply_sleep()
        self.hass.services.async_call.reset_mock()
        self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
        self.now+=timedelta(hours=8)
        await self.c._tick()
        self.hass.services.async_call.assert_not_awaited()
        self.now=datetime(2026,11,1,23,30,tzinfo=TZ)
        self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':158,'supported_color_modes':['color_temp']})
        await self.c._tick()
        self.hass.services.async_call.assert_awaited_once()

    async def test_failure_is_not_repeated(self):
        self.hass.services.async_call.side_effect=RuntimeError('offline')
        await self.c.apply_sleep()
        await self.c.apply_sleep()
        self.hass.services.async_call.assert_awaited_once()

    async def test_wake_once_enables_real_pid(self):
        await self.c.apply_sleep()
        self.hass.services.async_call.reset_mock()
        self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
        self.c.sleep_wake=True
        self.c.observe=True
        self.c.enabled=False
        self.now=datetime(2026,11,1,7,0,1,tzinfo=TZ)
        await self.c.apply_sleep()
        self.assertEqual(self.hass.services.async_call.call_args.args[:2],('light','turn_on'))
        self.assertTrue(self.c.enabled)
        self.assertFalse(self.c.observe)
        await self.c.apply_sleep()
        self.hass.services.async_call.assert_awaited_once()

    async def test_wake_does_not_replay_hours_later(self):
        await self.c.apply_sleep()
        self.hass.services.async_call.reset_mock()
        self.c.sleep_wake=True
        self.now=datetime(2026,11,1,10,0,tzinfo=TZ)
        await self.c.apply_sleep()
        self.hass.services.async_call.assert_not_awaited()

    async def test_editing_active_window_preserves_manual_override(self):
        await self.c.apply_sleep()
        self.c.sleep_override=True
        self.hass.services.async_call.reset_mock()
        await self.c.set_sleep_time('sleep_end',time(8))
        await self.c.apply_sleep()
        self.assertTrue(self.c.sleep_override)
        self.hass.services.async_call.assert_not_awaited()


# Avoid running inherited PID tests with the sleep fixture active.
for name in list(HAController.__dict__):
    if name.startswith('test_') and name not in SleepController.__dict__:
        setattr(SleepController,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
