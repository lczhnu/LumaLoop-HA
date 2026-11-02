import asyncio
import unittest
from unittest.mock import AsyncMock
from custom_components.lighting_smooth.calibration_runner import measure_curve,build_curve
from custom_components.lighting_smooth.calibration import Calibration

ATTRS={"color_mode":"brightness"}


class Sweep(unittest.IsolatedAsyncioTestCase):
    async def test_success_separates_background_and_restores(self):
        level=0
        async def set_level(value):
            nonlocal level
            level=value or 0
        async def read():
            return 10+2*level
        restore=AsyncMock()
        data=await measure_curve("light.example_lamp",ATTRS,set_level,read,restore,
                                 lambda _:None,wait=AsyncMock())
        restore.assert_awaited_once()
        curve=Calibration(data)
        self.assertEqual(curve.forward(50),100)
        self.assertEqual(curve.inverse(100),50)
        self.assertTrue(curve.applicable("light.example_lamp",ATTRS)[0])
        self.assertFalse(curve.applicable("light.other",ATTRS)[0])

    async def test_fault_restores_and_never_returns_curve(self):
        restore=AsyncMock()
        with self.assertRaises(ValueError):
            await measure_curve("light.example_lamp",ATTRS,AsyncMock(),
                                AsyncMock(return_value=float("nan")),restore,
                                lambda _:None,wait=AsyncMock())
        restore.assert_awaited_once()

    async def test_cancellation_restores(self):
        reached=asyncio.Event()
        async def wait(_):
            reached.set()
            await asyncio.Event().wait()
        restore=AsyncMock()
        task=asyncio.create_task(measure_curve("light.example_lamp",ATTRS,AsyncMock(),
             AsyncMock(return_value=10),restore,lambda _:None,wait=wait))
        await reached.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        restore.assert_awaited_once()

    async def test_no_curve_when_restore_fails(self):
        with self.assertRaises(OSError):
            await measure_curve("light.example_lamp",ATTRS,AsyncMock(),
                 AsyncMock(return_value=10),AsyncMock(side_effect=OSError("offline")),
                 lambda _:None,wait=AsyncMock())


class CurveQuality(unittest.TestCase):
    def test_reject_drift_nonmonotonic_and_flat(self):
        for rows,final in [([(1,20),(100,200)],30),
                           ([(1,50),(100,20)],10),
                           ([(1,20),(100,20)],10)]:
            with self.assertRaises(ValueError):
                build_curve("light.example_lamp",ATTRS,10,rows,final)

    def test_small_noise_becomes_monotone(self):
        data=build_curve("light.example_lamp",ATTRS,10,[(1,20),(5,19),(100,200)],10)
        self.assertEqual(data["curve"][1]["lamp_lux"],10)
