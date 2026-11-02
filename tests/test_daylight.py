from test_ha_controller import HAController
from unittest.mock import AsyncMock
from custom_components.lighting_smooth.engine import DEFAULT_LIGHT
import unittest
class Daylight(HAController):
 async def test_daylight_cycle(self):
  c=self.c;c.enabled=True;c.observe=False;c.phase='day'
  c.params.update(day_lux_min=80,day_lux_max=85)
  self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':3,'supported_color_modes':['color_temp']})
  lamp=self.hass.states.get(DEFAULT_LIGHT);c.lux=101
  await c.auto_daylight(lamp,100)
  await c.auto_daylight(lamp,159)
  self.hass.services.async_call.assert_not_awaited()
  await c.auto_daylight(lamp,160)
  self.assertTrue(c.auto_owned)
  self.assertEqual(self.hass.services.async_call.call_args.args[1],'turn_off')
  self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
  lamp=self.hass.states.get(DEFAULT_LIGHT)
  await c.auto_daylight(lamp,165)
  c.lux=88
  await c.auto_daylight(lamp,175)
  self.assertIsNone(c.auto_expected)
  c.lux=79
  await c.auto_daylight(lamp,180)
  await c.auto_daylight(lamp,190)
  self.assertEqual(self.hass.services.async_call.call_args.args[1],'turn_on')
 async def test_high_brightness_does_not_shut_off(self):
  c=self.c;c.observe=False;c.phase='day';c.lux=1000
  for t in (100,200):await c.auto_daylight(self.hass.states.get(DEFAULT_LIGHT),t)
  self.hass.services.async_call.assert_not_awaited()
 async def test_failed_probe_reopens(self):
  c=self.c;c.observe=False;c.phase='day';c.auto_owned=True;c.auto_check=True;c.lux=20
  self.hass.states.async_set(DEFAULT_LIGHT,'off',{'supported_color_modes':['color_temp']})
  await c.auto_daylight(self.hass.states.get(DEFAULT_LIGHT),100)
  self.assertEqual(c.auto_retry,400)
  self.assertEqual(self.hass.services.async_call.call_args.args[1],'turn_on')
 async def test_threshold_interruption(self):
  c=self.c;c.observe=False;c.phase='day'
  self.hass.states.async_set(DEFAULT_LIGHT,'on',{'brightness':3,'supported_color_modes':['color_temp']})
  lamp=self.hass.states.get(DEFAULT_LIGHT)
  c.lux=1000;await c.auto_daylight(lamp,100)
  c.lux=0;await c.auto_daylight(lamp,150)
  c.lux=1000;await c.auto_daylight(lamp,160)
  await c.auto_daylight(lamp,200)
  self.hass.services.async_call.assert_not_awaited()
if __name__=='__main__':unittest.main(verbosity=2)
