"""Static lookup and closed-loop regression using measured lamp curve."""
import importlib.util
import json
from pathlib import Path
import unittest
from test_engine import eng

path=Path(__file__).parents[1]/'custom_components/lighting_smooth/calibration.py'
spec=importlib.util.spec_from_file_location('lighting_calibration',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Calibration=module.Calibration


def fixture():
    # Deliberately synthetic plant; no household measurements.
    return {'schema_version':1,'lamp':'light.example_lamp',
            'color_temp_kelvin':[4000],'color_mode':'color_temp',
            'curve':[{'percent':x,'lamp_lux':12+1.8*x} for x in range(1,101)]}


class CalibratedMath(unittest.TestCase):
    def setUp(self):
        self.curve=Calibration(fixture())
        self.params={**eng.DEFAULTS,'kp':2,'ki':0.01,'kd':1,'interval':1,'max_step':3,'filter_tau':5,'day_lux_min':80,'day_lux_max':85}

    def test_exact_measured_points_and_inverse_plateaus(self):
        for x,y in zip(self.curve.x,self.curve.y):
            self.assertAlmostEqual(self.curve.forward(x),y)
            self.assertLessEqual(self.curve.inverse(y),x+1e-8)
            self.assertAlmostEqual(self.curve.forward(self.curve.inverse(y)),y)
        self.assertEqual(self.curve.forward(0),0)
        self.assertEqual(self.curve.inverse(0),self.curve.x[0])
        self.assertEqual(self.curve.inverse(1e6),100)

    def test_reject_nonmonotone_nan_and_duplicate_brightness(self):
        original=fixture()
        for edit in ('negative','nan','duplicate'):
            data=json.loads(json.dumps(original))
            if edit=='negative':data['curve'][5]['lamp_lux']=-1
            elif edit=='nan':data['curve'][5]['lamp_lux']=float('nan')
            else:data['curve'][5]['percent']=data['curve'][4]['percent']
            with self.assertRaises(ValueError):Calibration(data)

    def test_only_matching_lamp_temperature_and_daylight_mode(self):
        self.assertTrue(self.curve.applicable(self.curve.lamp,{'color_temp_kelvin':4000,'color_mode':'color_temp'})[0])
        for lamp,attrs in [(self.curve.lamp,{}),('light.other',{'color_temp_kelvin':4000,'color_mode':'color_temp'}),
                           (self.curve.lamp,{'color_temp_kelvin':5000}),
                           (self.curve.lamp,{'color_temp_kelvin':4000,'effect':'月光（夜间）模式'})]:
            self.assertFalse(self.curve.applicable(lamp,attrs)[0])

    def test_feedforward_is_absolute_inverse_and_slew_limited(self):
        r=eng.PID().step(10+self.curve.forward(20),20,1,self.params,'day',self.curve)
        self.assertTrue(r.calibrated)
        self.assertAlmostEqual(r.background,10)
        self.assertAlmostEqual(r.feedforward,self.curve.inverse(72.5))
        self.assertEqual(r.output,23)

    def test_model_and_total_filters_cancel_own_brightness_changes(self):
        pid=eng.PID()
        for actual in [20,23,26,29,40,60,65,70,65,40,20]:
            r=pid.step(10+self.curve.forward(actual),actual,1,self.params,'day',self.curve)
            self.assertAlmostEqual(r.background,10)

    def test_hold_band_and_correction_budget(self):
        pid=eng.PID()
        actual=self.curve.inverse(72.5)
        r=pid.step(82.5,actual,1,self.params,'day',self.curve)
        self.assertTrue(r.in_band)
        self.assertAlmostEqual(r.output,actual,places=3)
        self.assertEqual(r.integral,0)
        r=eng.PID().step(20,40,1,self.params,'day',self.curve)
        required=82.5-r.background
        self.assertLessEqual(r.feedforward+r.correction,self.curve.inverse(required+1.25)+1e-8)

    def test_saturation_no_windup_and_low_plateau_no_division(self):
        for ambient,actual in [(0,1.17647),(1000,1.17647)]:
            pid=eng.PID()
            for _ in range(300):
                r=pid.step(ambient+self.curve.forward(actual),actual,1,self.params,'day',self.curve)
                self.assertLessEqual(abs(r.output-actual),3.001)
                actual=r.output
            self.assertLessEqual(abs(r.integral),1)
            self.assertGreaterEqual(actual,1)
            self.assertLessEqual(actual,100)

    def test_delayed_quantized_plant_settles_across_working_points(self):
        for delay in (0,1,2,3):
            for ambient in (5,10,40,60):
                for initial in (2,50,95):
                    with self.subTest(delay=delay,ambient=ambient,initial=initial):
                        pid=eng.PID();actual=float(initial);history=[actual]*(delay+1);last=[]
                        for tick in range(300):
                            physical=history.pop(0)
                            lux=round((ambient+self.curve.forward(physical))*1.2)/1.2
                            r=pid.step(lux,actual,1,self.params,'day',self.curve)
                            proposed=round(r.output*255/100)
                            if abs(proposed-round(actual*255/100))>=2:actual=proposed/255*100
                            history.append(actual)
                            if tick>=240:last.append(lux)
                        self.assertTrue(all(79.9<=v<=85.1 for v in last),(min(last),max(last),actual))

    def test_daylight_steps_and_ramps_remain_bounded_and_recover(self):
        pid=eng.PID();actual=40.;history=[actual]*3;last=[]
        for tick in range(600):
            ambient=10 if tick<100 else 40 if tick<200 else 40-(tick-200)*.15 if tick<400 else 10
            physical=history.pop(0)
            lux=round((ambient+self.curve.forward(physical))*1.2)/1.2
            r=pid.step(lux,actual,1,self.params,'day',self.curve)
            self.assertLessEqual(abs(r.output-actual),3.001)
            proposed=round(r.output*255/100)
            if abs(proposed-round(actual*255/100))>=2:actual=proposed/255*100
            history.append(actual)
            if tick>=540:last.append(lux)
        self.assertTrue(all(79.9<=v<=85.1 for v in last),(min(last),max(last)))


if __name__=='__main__':unittest.main(verbosity=2)
