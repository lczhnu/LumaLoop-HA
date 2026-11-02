"""Hardware-independent, cancellable optical calibration sweep."""
import asyncio
import math
from statistics import median


def build_curve(lamp, attributes, background, observations, final_background,
                max_drift=3.0):
    """Reject changing ambient light and significant non-monotonic response."""
    if abs(final_background-background) > max_drift:
        raise ValueError("Ambient light changed; darken the room and retry")
    points=[]
    previous=0.0
    for percent, total in observations:
        contribution=total-background
        if contribution < -max_drift or contribution < previous-max_drift:
            raise ValueError("Non-monotonic response; check other automations and sensor position")
        contribution=max(previous, contribution, 0.0)
        points.append({"percent":percent,"lamp_lux":contribution})
        previous=contribution
    from .calibration import Calibration
    data={"schema_version":1,"lamp":lamp,"source":"Local optical calibration",
          "color_temp_kelvin":[attributes["color_temp_kelvin"]]
              if attributes.get("color_temp_kelvin") is not None else [],
          "color_mode":attributes.get("color_mode"),"effect":attributes.get("effect"),
          "background_lux":background,"final_background_lux":final_background,
          "curve":points}
    Calibration(data)
    return data


async def measure_curve(lamp, attributes, set_level, read_lux, restore, progress,
                        levels=(1,5,10,20,35,50,65,80,100), settle=5,
                        samples=3, max_drift=3.0, wait=asyncio.sleep):
    """Callbacks own device access. Always attempt restoration, including cancel.

    set_level(None) means off. read_lux must return a fresh, unsaturated value.
    This actively operates the lamp and must only be called by an explicit action.
    """
    async def sample():
        values=[]
        for _ in range(samples):
            value=await read_lux()
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError("Invalid illuminance sample")
            values.append(value)
            await wait(1)
        if max(values)-min(values)>max_drift:
            raise ValueError("Unstable samples; keep ambient lighting constant")
        return median(values)

    try:
        progress("Measuring background (lamp off)")
        await set_level(None)
        await wait(settle)
        background=await sample()
        observations=[]
        for level in levels:
            progress(f"Measuring {level}%")
            await set_level(level)
            await wait(settle)
            observations.append((level,await sample()))
        progress("Checking background drift")
        await set_level(None)
        await wait(settle)
        return build_curve(lamp,attributes,background,observations,await sample(),max_drift)
    finally:
        await restore()
