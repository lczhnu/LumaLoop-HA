"""Regression tests for command/readback cadence, using simulated lamps only."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from test_ha_controller import HAController
from homeassistant.core import Context,Event
from custom_components.lighting_smooth.engine import DEFAULT_LIGHT
from custom_components.lighting_smooth import number,sensor


class Timing(HAController):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.now=1000.0
        self.clock=patch('custom_components.lighting_smooth.monotonic_time',
                         SimpleNamespace(monotonic=lambda:self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def activate(self):
        self.c.enabled=True
        self.c.observe=False
        self.c.params.update(interval=5,ki=0.01)

    def lamp(self,brightness,context=None):
        self.hass.states.async_set(DEFAULT_LIGHT,'on',
            {'brightness':brightness,'supported_color_modes':['color_temp']},
            context=context)

    async def test_polling_ack_continues_same_tick(self):
        self.activate()
        await self.c._tick()
        first=self.c.pending
        self.assertIsNotNone(first)
        self.lamp(first)
        self.now+=5
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,2)
        self.assertEqual(self.c.last_step,1005)

    async def test_context_ack_clears_pending_immediately(self):
        self.activate()
        await self.c._tick()
        old=self.hass.states.get(DEFAULT_LIGHT)
        self.lamp(self.c.pending,Context(id=self.c.command_context))
        self.c.changed(Event('state_changed',{'entity_id':DEFAULT_LIGHT,
            'old_state':old,'new_state':self.hass.states.get(DEFAULT_LIGHT)}))
        self.assertIsNone(self.c.pending)
        self.assertEqual(self.c.pause_until,0)

    async def test_missing_ack_does_not_send_again(self):
        self.activate()
        await self.c._tick()
        self.now+=5
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,1)

    async def test_early_ack_keeps_interval(self):
        self.activate()
        await self.c._tick()
        self.lamp(self.c.pending)
        self.now+=2
        await self.c._tick()
        self.assertIsNone(self.c.pending)
        self.assertEqual(self.hass.services.async_call.await_count,1)
        self.now=1005
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,2)

    async def test_jitter_does_not_double_interval(self):
        self.activate()
        for now in (1000,1004.999,1010.012,1014.999,1020):
            self.now=now
            if self.c.pending is not None:self.lamp(self.c.pending)
            await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,5)
        self.assertAlmostEqual(self.c.next_step,1025)

    async def test_long_stall_skips_missed_ticks_without_burst(self):
        self.activate()
        await self.c._tick()
        self.lamp(self.c.pending)
        self.now=1062
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,2)
        self.assertEqual(self.c.next_step,1067)
        self.lamp(self.c.pending)
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,2)

    async def test_reset_clears_cadence(self):
        self.activate()
        await self.c._tick()
        self.c.reset()
        self.assertEqual(self.c.last_step,0)
        self.assertEqual(self.c.next_step,0)

    async def test_late_callback_near_following_deadline_never_bursts(self):
        self.activate()
        await self.c._tick()
        self.lamp(self.c.pending)
        self.now=1009.99
        await self.c._tick()
        self.lamp(self.c.pending)
        await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,2)

    async def test_one_second_parameter_and_entity_metadata(self):
        await self.c.set_param('interval',1)
        entities=[]
        self.hass.data['lighting_smooth']=self.c
        await number.async_setup_platform(self.hass,{},entities.extend,{})
        interval=next(e for e in entities if e.key=='interval')
        self.assertEqual(interval.native_min_value,1)
        self.assertEqual(interval.native_step,1)
        self.assertEqual(interval.native_value,1)
        entities=[]
        await sensor.async_setup_platform(self.hass,{},entities.extend,{})
        lux=next(e for e in entities if e.key=='illuminance')
        self.assertEqual(lux.extra_state_attributes['sample_interval_seconds'],1)
        with self.assertRaises(Exception):await self.c.set_param('interval',0)

    async def test_one_second_cadence_with_new_sensor_samples(self):
        self.activate()
        await self.c.set_param('interval',1)
        self.hass.async_add_executor_job.side_effect=[(20+n,24+n) for n in range(6)]
        for now in (1000,1000.999,1002.012,1002.999,1004,1005):
            self.now=now
            if self.c.pending is not None:self.lamp(self.c.pending)
            await self.c._tick()
        self.assertEqual(self.hass.async_add_executor_job.await_count,6)
        self.assertEqual(self.hass.services.async_call.await_count,6)
        self.assertEqual(self.c.lux,25)
        self.assertAlmostEqual(self.c.next_step,1006)

    async def test_one_second_sampling_preserves_five_second_control(self):
        self.activate()
        for now in range(1000,1006):
            self.now=now
            if self.c.pending is not None:self.lamp(self.c.pending)
            await self.c._tick()
        self.assertEqual(self.hass.async_add_executor_job.await_count,6)
        self.assertEqual(self.hass.services.async_call.await_count,2)

    async def test_one_second_mode_still_waits_for_ack(self):
        self.activate()
        await self.c.set_param('interval',1)
        await self.c._tick()
        for now in (1001,1002,1003):
            self.now=now
            await self.c._tick()
        self.assertEqual(self.hass.services.async_call.await_count,1)


if __name__=='__main__':unittest.main(verbosity=2)
