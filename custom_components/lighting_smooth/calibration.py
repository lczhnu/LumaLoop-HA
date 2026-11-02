"""Validated, lamp-specific monotone lookup. No external numerical libraries."""
from bisect import bisect_left,bisect_right
import math


class Calibration:
    def __init__(self,data):
        if data.get('schema_version')!=1 or not isinstance(data.get('lamp'),str):
            raise ValueError('标定数据版本或灯具标识无效')
        self.data=data
        self.lamp=data['lamp']
        self.mode=data.get('color_mode')
        self.effect=data.get('effect')
        if self.mode not in ('brightness','color_temp'):
            raise ValueError('Calibration supports brightness or color_temp mode only')
        self.temperatures=tuple(data['color_temp_kelvin'])
        self.source=data.get('source','')
        curve=data['curve']
        self.x=tuple(p['percent'] for p in curve)
        self.y=tuple(p['lamp_lux'] for p in curve)
        values=(*self.temperatures,*self.x,*self.y)
        if (len(curve)<2 or
                any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values) or
                not 0<self.x[0]<self.x[-1]<=100 or self.y[0]<0 or
                any(b<=a for a,b in zip(self.x,self.x[1:])) or
                any(b<a for a,b in zip(self.y,self.y[1:])) or self.y[-1]<=self.y[0]):
            raise ValueError('标定曲线必须有限、亮度递增且照度不下降')

    def applicable(self,entity,attributes):
        if entity!=self.lamp:
            return False,'Lamp differs from the calibrated lamp; using PID'
        if attributes.get('color_mode')!=self.mode or attributes.get('effect')!=self.effect:
            return False,'Color/effect mode changed; using PID'
        temperature=attributes.get('color_temp_kelvin')
        if self.temperatures:
            if (isinstance(temperature,bool) or not isinstance(temperature,(int,float))
                    or not math.isfinite(temperature)
                    or min(abs(temperature-t) for t in self.temperatures)>50):
                return False,'Color temperature changed; using PID'
        elif temperature is not None:
            return False,'Uncalibrated color temperature; using PID'
        return True,'Calibrated feedforward + PID correction'

    def forward(self,percent):
        if percent<=0:return 0.0  # Off is separate from minimum-on brightness.
        if percent<=self.x[0]:return self.y[0]
        if percent>=self.x[-1]:return self.y[-1]
        i=bisect_right(self.x,percent)
        return self.y[i-1]+(self.y[i]-self.y[i-1])*(percent-self.x[i-1])/(self.x[i]-self.x[i-1])

    def inverse(self,lux):
        if lux<=self.y[0]:return self.x[0]
        if lux>=self.y[-1]:return self.x[-1]
        i=bisect_left(self.y,lux)
        return self.x[i-1]+(self.x[i]-self.x[i-1])*(lux-self.y[i-1])/(self.y[i]-self.y[i-1])
