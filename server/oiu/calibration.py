"""Fit fixed-cadence HID units to measured whole-screen coordinates."""
import math

VERSION = 3
MOTION = 'vertical-horizontal-16units-8ms-home-each'
TOLERANCE = .01


def fit(samples, aspect):
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError('Invalid screen aspect for calibration')
    if len(samples) < 5:
        raise ValueError('Calibration needs at least five measured points')
    result = dict(version=VERSION, motion=MOTION, aspect=aspect, samples=[list(s) for s in samples])
    errors = []
    for axis, name, minimum in ((0, 'width', 100), (1, 'height', 200)):
        units = [s[axis] for s in samples]
        positions = [s[axis+2] for s in samples]
        if not all(math.isfinite(v) for v in units+positions):
            raise ValueError('Non-finite calibration measurement')
        if max(positions)-min(positions) < .2:
            raise ValueError('Calibration points are clamped or too close together')
        mean_u, mean_p = sum(units)/len(units), sum(positions)/len(positions)
        variance = sum((u-mean_u)**2 for u in units)
        if variance == 0: raise ValueError('Calibration has no unit span')
        slope = sum((u-mean_u)*(p-mean_p) for u,p in zip(units,positions))/variance
        if slope <= 0 or not minimum <= 1/slope <= 20000:
            raise ValueError('Calibration gain is clamped or inconsistent')
        offset = mean_p-slope*mean_u
        residuals = [p-(slope*u+offset) for u,p in zip(units,positions)]
        rms = math.sqrt(sum(e*e for e in residuals)/len(residuals))
        if rms > .015 or max(map(abs,residuals)) > .03:
            raise ValueError('Calibration motion is inconsistent. Keep AssistiveTouch tracking speed, Zoom and orientation unchanged, then prepare again')
        result[name] = 1/slope
        result['offset_'+('x' if axis == 0 else 'y')] = offset
        errors.append(rms)
    result['fit_rms'] = errors
    return result
