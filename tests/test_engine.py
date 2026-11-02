import importlib.util
from pathlib import Path
import sys
import unittest
from datetime import datetime,timedelta,timezone

path=Path(__file__).parents[1]/'custom_components/lighting_smooth/engine.py'
spec=importlib.util.spec_from_file_location('lighting_engine',path)
eng=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=eng
spec.loader.exec_module(eng)


class ControllerMath(unittest.TestCase):
    def test_valid_ranges(self):
        for params in ({**eng.DEFAULTS,'day_lux_min':400},
                       {**eng.DEFAULTS,'night_brightness_min':100,'night_brightness_max':50},
                       {**eng.DEFAULTS,'kp':float('nan')}):
            with self.assertRaises(ValueError):eng.validated(params)

    def test_all_solar_boundaries(self):
        rise=datetime(2026,10,31,6,tzinfo=timezone.utc)
        setting=rise+timedelta(hours=12)
        cases=[(-31,'night'),(-30,'sunrise'),(0,'sunrise'),(29,'sunrise'),(30,'day'),
               (689,'day'),(690,'sunset'),(720,'sunset'),(749,'sunset'),(750,'night')]
        for minutes,key in cases:
            self.assertEqual(eng.phase_at(rise+timedelta(minutes=minutes),rise,setting,30),key)
        self.assertIsNone(eng.phase_at(rise,None,setting,30))

    def test_bumpless_and_slew(self):
        pid=eng.PID()
        actual=62
        for _ in range(15):
            result=pid.step(10,actual,10,eng.DEFAULTS,'day')
            self.assertLessEqual(abs(result.output-actual),3.001)
            self.assertGreaterEqual(result.output,actual)
            actual=result.output
        self.assertGreater(actual,62)

    def test_no_deadlock_at_large_error(self):
        pid=eng.PID()
        params={**eng.DEFAULTS,'day_lux_min':1000,'day_lux_max':2000,'ki':0.05}
        result=pid.step(0,20,10,params,'day')
        self.assertGreater(result.output,20)
        self.assertLessEqual(result.output,23)

    def test_hold_band_and_no_integral_windup(self):
        pid=eng.PID()
        for _ in range(500):
            result=pid.step(0,100,10,eng.DEFAULTS,'day')
        self.assertEqual(result.output,100)
        self.assertAlmostEqual(result.integral,0)
        pid.reset()
        for _ in range(10):
            result=pid.step(220,62,10,eng.DEFAULTS,'day')
            self.assertEqual(result.output,62)
            self.assertTrue(result.in_band)

    def test_decrease_in_bright_room_and_reset(self):
        pid=eng.PID()
        result=pid.step(1000,62,10,eng.DEFAULTS,'day')
        self.assertLess(result.output,62)
        self.assertGreaterEqual(result.output,59)
        pid.reset()
        self.assertIsNone(pid.filtered)
        self.assertEqual(pid.integral,0)

    def test_phase_output_limit_is_smooth(self):
        params={**eng.DEFAULTS,'night_brightness_max':20}
        result=eng.PID().step(40,80,10,params,'night')
        self.assertEqual(result.output,77)

    def test_closed_loop_converges(self):
        pid=eng.PID()
        actual=10
        for _ in range(400):
            # Deterministic positive-gain room with daylight and a lagged lamp.
            lux=30+3*actual
            result=pid.step(lux,actual,10,eng.DEFAULTS,'day')
            actual=result.output
        self.assertTrue(150<=30+3*actual<=300,(actual,30+3*actual))


if __name__=='__main__':unittest.main(verbosity=2)
