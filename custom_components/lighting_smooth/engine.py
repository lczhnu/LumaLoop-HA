"""Pure controller math, independently testable without HA or real lamps."""
from dataclasses import dataclass
from datetime import timedelta
import math

PHASES = {"sunrise": "日出", "day": "白天", "sunset": "日落", "night": "黑夜"}
DEFAULT_LIGHT = "light.example_lamp"
SAMPLE_INTERVAL = 1.0
DEFAULTS = {
    "kp": 0.12, "ki": 0.001, "kd": 0.0,
    "interval": 10.0, "max_step": 3.0, "filter_tau": 10.0,
    "auto_off_margin": 15.0, "auto_off_delay": 60.0, "auto_on_delay": 10.0,
    "auto_settle": 10.0, "manual_pause": 120.0, "twilight": 30.0,
    "sunrise_lux_min": 100.0, "sunrise_lux_max": 200.0,
    "day_lux_min": 150.0, "day_lux_max": 300.0,
    "sunset_lux_min": 100.0, "sunset_lux_max": 200.0,
    "night_lux_min": 30.0, "night_lux_max": 80.0,
}
for phase in PHASES:
    DEFAULTS[phase + "_brightness_min"] = 1.0
    DEFAULTS[phase + "_brightness_max"] = 100.0

# name, minimum, maximum, step, unit
PARAMS = {
    "auto_off_margin": ("自然光关灯 · 超上限余量", 0.0, 1000.0, 0.1, "lx"),
    "auto_off_delay": ("自然光关灯 · 持续时间", 5.0, 1800.0, 1.0, "s"),
    "auto_on_delay": ("照度不足开灯 · 持续时间", 5.0, 300.0, 1.0, "s"),
    "auto_settle": ("开关灯后测量等待", 5.0, 120.0, 1.0, "s"),
    "kp": ("比例 Kp", 0.0, 2.0, 0.01, None),
    "ki": ("积分 Ki", 0.0, 0.05, 0.0001, None),
    "kd": ("微分 Kd", 0.0, 10.0, 0.01, None),
    "interval": ("调节间隔", SAMPLE_INTERVAL, 120.0, 1.0, "s"),
    "max_step": ("每次最大亮度变化", 1.0, 15.0, 0.5, "%"),
    "filter_tau": ("照度滤波时间常数", 0.0, 120.0, 1.0, "s"),
    "manual_pause": ("手动调灯后暂停", 30.0, 1800.0, 10.0, "s"),
    "twilight": ("日出日落前后窗口", 5.0, 120.0, 5.0, "min"),
}
for phase, label in PHASES.items():
    for suffix, name, unit, minimum, maximum, step in (
        ("lux_min", "照度下限", "lx", 0, 10000, 1),
        ("lux_max", "照度上限", "lx", 1, 10000, 1),
        ("brightness_min", "灯具亮度下限", "%", 1, 100, 1),
        ("brightness_max", "灯具亮度上限", "%", 1, 100, 1),
    ):
        PARAMS[phase + "_" + suffix] = (label + " · " + name, minimum, maximum, step, unit)


def validated(params):
    if set(params) != set(DEFAULTS):
        raise ValueError("参数不完整")
    for key, (_, low, high, _, _) in PARAMS.items():
        v = params[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f"{PARAMS[key][0]}超出范围")
    for phase in PHASES:
        if params[phase + "_lux_min"] >= params[phase + "_lux_max"]:
            raise ValueError(PHASES[phase] + "照度下限必须小于上限")
        if params[phase + "_brightness_min"] > params[phase + "_brightness_max"]:
            raise ValueError(PHASES[phase] + "灯具亮度下限不能大于上限")
    return dict(params)


def phase_at(now, sunrise, sunset, minutes):
    """Windows are centred on today's local astronomical sunrise/sunset."""
    if sunrise is None or sunset is None:
        return None
    window = timedelta(minutes=minutes)
    candidates = [(abs((now - event).total_seconds()), key) for event, key in
                  ((sunrise, "sunrise"), (sunset, "sunset")) if event-window <= now < event+window]
    if candidates:
        return min(candidates)[1]
    return "day" if sunrise <= now < sunset else "night"


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class Result:
    output: float
    filtered: float
    error: float
    integral: float
    in_band: bool
    limited: bool
    calibrated: bool = False
    feedforward: float | None = None
    background: float | None = None
    correction: float | None = None
    smoothed_feedforward: float | None = None


class PID:
    """Bumpless biased PID, derivative on measurement and anti-windup.

    The acceptable lux band is a hold band. Outside it the midpoint is the
    target; within it the integrator is reset and lamp brightness is held.
    Both slew and physical output bounds prevent integral windup.
    """
    def __init__(self):
        self.filtered = None
        self.previous = None
        self.integral = 0.0
        self.bias = None
        self.model_filtered = None
        self.feedforward_filtered = None

    def reset(self):
        self.filtered = self.previous = self.bias = None
        self.integral = 0.0
        self.model_filtered = None
        self.feedforward_filtered = None

    def step(self, lux, actual, dt, params, phase, calibration=None):
        vals = [lux, actual, dt]
        if not all(math.isfinite(v) for v in vals) or lux < 0 or dt <= 0:
            raise ValueError("无效反馈")
        dt = min(dt, 120.0)
        lo, hi = params[phase+"_lux_min"], params[phase+"_lux_max"]
        bmin, bmax = params[phase+"_brightness_min"], params[phase+"_brightness_max"]
        tau = params["filter_tau"]
        alpha = 1.0 if tau == 0 else dt/(tau+dt)
        self.filtered = lux if self.filtered is None else self.filtered+alpha*(lux-self.filtered)
        rate = 0.0 if self.previous is None else (self.filtered-self.previous)/dt
        self.previous = self.filtered
        target = (lo+hi)/2.0
        error = target-self.filtered
        pterm = params["kp"]*error
        dterm = -params["kd"]*rate
        if calibration is not None:
            return self.calibrated_step(actual,dt,params,lo,hi,bmin,bmax,alpha,error,pterm,dterm,calibration)
        if self.bias is None:
            self.bias = actual-pterm-dterm  # first output starts at actual
        in_band = lo <= self.filtered <= hi
        if in_band:
            self.integral = 0.0
            self.bias = actual-pterm-dterm
            desired = actual
        else:
            candidate = self.integral+params["ki"]*error*dt
            proposal = self.bias+pterm+candidate+dterm
            self.integral = clamp(candidate,-100,100)
            desired = self.bias+pterm+self.integral+dterm
        # Hard brightness limits are approached smoothly, even after phase change.
        bounded = clamp(desired,bmin,bmax)
        output = clamp(bounded, max(1,actual-params["max_step"]), min(100,actual+params["max_step"]))
        # Track the limited output instead of accumulating unreachable demand.
        # This still permits movement for large errors / high integral gains.
        if not in_band and ((desired>output and error>0) or (desired<output and error<0)):
            self.integral=clamp(output-self.bias-pterm-dterm,-100,100)
        return Result(round(output,3),self.filtered,error,self.integral,in_band,
                      abs(output-desired)>0.05 or abs(bounded-desired)>0.05)

    def calibrated_step(self,actual,dt,params,lo,hi,bmin,bmax,alpha,error,pterm,dterm,curve):
        # Filter the model contribution with the SAME filter as total lux.
        # Subtracting current unfiltered lamp output from lagged total lux
        # would mistake our own brightness changes for changing daylight.
        contribution=curve.forward(actual)
        self.model_filtered=(contribution if self.model_filtered is None else
                             self.model_filtered+alpha*(contribution-self.model_filtered))
        background=self.filtered-self.model_filtered
        target=(lo+hi)/2
        required=target-background
        feedforward=curve.inverse(required)
        # Smooth the absolute feedforward state, not an increment from actual.
        # This damps short feedback/optical delays while still accumulating
        # sub-command-quantum changes until the actuator can realize them.
        if self.feedforward_filtered is None:self.feedforward_filtered=actual
        self.feedforward_filtered+=dt/(2.0+dt)*(feedforward-self.feedforward_filtered)
        # Residual PID may move the predicted illuminance by at most a quarter
        # of the allowed band. The absolute curve supplies the main correction.
        # Inverse lookup handles flat regions without dividing by zero gain.
        allowance=(hi-lo)/4
        correction_low=curve.inverse(required-allowance)-feedforward
        correction_high=curve.inverse(required+allowance)-feedforward
        in_band=lo<=self.filtered<=hi
        candidate=clamp(self.integral+params['ki']*error*dt,-100,100)
        raw_correction=pterm+candidate+dterm
        correction=clamp(raw_correction,correction_low,correction_high)
        if in_band:
            self.integral=0.0
            correction=0.0
            desired=actual
        else:
            desired=self.feedforward_filtered+correction
        bounded=clamp(desired,bmin,bmax)
        output=clamp(bounded,max(1,actual-params['max_step']),min(100,actual+params['max_step']))
        # Conditional integration: do not wind up against correction, slew or
        # physical bounds, and do not back-calculate the feedforward ramp into I.
        if not in_band:
            blocked=((raw_correction>correction and error>0) or
                     (raw_correction<correction and error<0) or
                     (desired>output and error>0) or (desired<output and error<0))
            if not blocked:self.integral=candidate
        return Result(round(output,3),self.filtered,error,self.integral,in_band,
                      abs(output-desired)>0.05 or (not in_band and abs(raw_correction-correction)>0.05),
                      True,feedforward,background,correction,self.feedforward_filtered)
