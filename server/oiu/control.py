"""Portable per-device HID lifecycle, safe board calibration and explicit Bluetooth peer binding."""
from collections import deque
import json
import base64
import math
import os
from pathlib import Path
import threading
import time
import uuid
import logging
from statistics import median

from .hid import mouse, keyboard, chord, stroke, fraction
from .core import CAPTURE_FPS
from . import vision
from . import calibration as mapping


VERIFY_BUDGET = 2.5
CALIBRATION_BUDGET = 55.0
# Good observed fits were .0042–.0056; .01 allows roughly twice that error.
GOOD_FIT_RMS = .01
# Three times the tap tolerance and the maximum residual accepted by fit().
SUSPECT_MISS = .03
log = logging.getLogger(__name__)


class ObservationUnavailable(ValueError):
    """A fresh, stable pointer could not be measured; this is not an accuracy miss."""

# A feed drag only needs a roughly centered start and ~40% of the screen height
# of travel; these gains mirror the calibration test fixtures for a known screen.
DRAG_FALLBACK = dict(width=600, height=1000, offset_x=0, offset_y=0)


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file: json.dump(value, file)
    os.replace(temporary, path)


def tap_geometry(value):
    # Capture pages can only contribute bounded numeric geometry, never arbitrary log text.
    if not isinstance(value, dict): return None
    result = {}
    for name, keys in (('client', ('x','y')), ('board', ('left','top','width','height')),
                       ('visualViewport', ('offsetLeft','offsetTop','width','height','scale'))):
        item = value.get(name)
        if not isinstance(item, dict):
            result[name] = None
            continue
        result[name] = {key: item[key] for key in keys if type(item.get(key)) in (int,float)
                        and math.isfinite(item[key]) and abs(item[key]) <= 100000}
    return result

class Pairing:
    def __init__(self, radio):
        self.radio = radio
        self.engines = []
        self.lock = threading.Lock()

    def associate(self, engine):
        # The owner chose this exact Bluetooth peer before receiving a capture link.
        # Never probe/rebind a different phone, and never hold a global action lock.
        if engine.closed: raise ValueError('Device removed or control closed')
        if not engine.peer:
            raise ValueError('Bluetooth binding required. Remove this unbound record and use new --bt to select a connected iPhone')
        peers = self.radio.peers()
        if not any(p['id'] == engine.peer and set(p['reports']) == {1, 2} for p in peers):
            raise ValueError('Bluetooth HID connection required for this phone; reconnect it in AssistiveTouch')
        return engine.peer


_UNSET_PEER = object()


class Control:
    def __init__(self, pairing, directory, peer=_UNSET_PEER):
        self.pairing, self.radio = pairing, pairing.radio
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = self.directory/'calibration.json'
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.peer = saved.get('peer') if peer is _UNSET_PEER else peer
        self.calibration = saved.get('calibration') if saved.get('peer') == self.peer else None
        self.condition = threading.Condition()
        self.input_lock = threading.Lock()
        self.jpeg, self.at, self.frame_id = b'', 0, None
        self.board_open, self.board_epoch, self.pair_epoch = False, 1, ''
        self.taps = deque(maxlen=64)
        self.hover = None
        self.frame_intervals = deque(maxlen=20)
        self.observation_frames = 0
        self.observation_wake_remaining = None
        self.debug_lock = threading.Lock()
        self.debug_queue = deque(maxlen=30)
        self.debug_thread = None
        self.generation, self.sending_generation = 0, 0
        self.units = (0,0)
        self.motion_at = 0
        self.error = None
        self.closed = False
        self.calibration_deadline = None
        pairing.engines.append(self)

    def calibration_compatible(self):
        return bool(self.calibration and self.calibration.get('version')==mapping.VERSION and
                    self.calibration.get('motion')==mapping.MOTION)

    @property
    def label(self):
        if self.error: return self.error
        health=getattr(self.radio,'startup',{})
        if health.get('error'): return 'Bluetooth: '+health['state']
        return 'Pointer ready' if self.calibration_compatible() and self.peer else ('Paired; first tap prepares pointer' if self.peer else 'Pair iPhone, then tap screen')

    def save(self):
        with self.condition:
            if self.closed: raise ValueError('Device removed or control closed')
            atomic_json(self.path, dict(peer=self.peer, calibration=self.calibration))
    def bind(self, peer):
        if self.peer and self.peer != peer: raise ValueError('A device cannot switch Bluetooth phones; remove and add it again')
        with self.pairing.lock:
            if any(e is not self and e.peer == peer and not e.closed for e in self.pairing.engines):
                raise ValueError('Bluetooth phone already bound to another device')
            self.peer=peer; self.calibration=None; self.save()
    def is_fresh(self): return bool(not self.closed and self.at and time.monotonic()-self.at <= 5)

    def ingest(self, jpeg):
        with self.condition:
            if self.closed: raise ValueError('Device removed or control closed')
            now = time.monotonic()
            if self.at and now > self.at: self.frame_intervals.append(now-self.at)
            self.jpeg, self.at, self.frame_id = jpeg, now, str(uuid.uuid4())
            self.condition.notify_all()
            return dict(frameID=self.frame_id, fps=CAPTURE_FPS, boardOpen=self.board_open,
                        boardEpoch=self.board_epoch, pairEpoch=self.pair_epoch)

    def open_board(self, value, pair_epoch=''):
        with self.condition:
            self.board_open=value; self.board_epoch+=1; self.pair_epoch=pair_epoch

    def fresh(self):
        if self.generation != self.sending_generation: raise ValueError('Input canceled')
        if not self.is_fresh(): raise ValueError('Screen sharing stopped or capture is stale; input released')
        if self.calibration_deadline is not None and time.monotonic() >= self.calibration_deadline:
            raise ValueError('Calibration time budget exhausted')

    def packet(self, report, payload):
        with self.input_lock:
            self.fresh()
            if not self.peer: raise ValueError('No paired iPhone associated with this screen')
            self.radio.send(self.peer, report, payload)

    def capture_after(self, after, timeout=3):
        deadline=time.monotonic()+timeout
        if self.calibration_deadline is not None: deadline=min(deadline,self.calibration_deadline)
        with self.condition:
            while True:
                self.fresh()
                if self.at > after: return self.at, self.jpeg
                remaining=deadline-time.monotonic()
                if remaining <= 0: raise ObservationUnavailable('No fresh screen arrived while waiting for the shared screen to catch up')
                self.condition.wait(min(.1,remaining))

    def wait_frame(self, after, timeout=5):
        return self.capture_after(after,timeout)[1]

    def wait_board(self, timeout=6):
        deadline=time.monotonic()+timeout
        after=time.monotonic()
        previous=None
        while time.monotonic()<deadline:
            try: stamp,jpeg=self.capture_after(after,max(.001,deadline-time.monotonic()))
            except ValueError:
                self.fresh()  # Cancellation/stale streams retain their own actionable error.
                break
            after=stamp
            try: rect=vision.board(jpeg)[1]
            except ValueError:
                previous=None; continue
            if previous and max(abs(a-b) for a,b in zip(previous,rect)) <= .003: return rect
            previous=rect
        raise ValueError('Calibration board did not become visible and stable within the calibration timeout. Keep Safari sharing in front, unlock the phone and use the green-bordered board; charging icons/status dots are not the board')

    def wait_pointer(self, after, expected=None, plausible=None, board_required=True, timeout=4, tolerance=mapping.TOLERANCE, reference=None, aspect=None, exclude=None):
        deadline=time.monotonic()+timeout
        previous=None
        stable_since=None
        reason='Pointer has not appeared'
        self.observation_frames = 0
        self.last_observation_method = None
        wakes = 0
        last_stamp = None
        while time.monotonic()<deadline:
            remaining=deadline-time.monotonic()
            try: stamp,jpeg=self.capture_after(after,max(.001,min(.75,remaining)))
            except ObservationUnavailable:
                self.fresh()
                if time.monotonic() >= deadline: break
                # Background Safari can produce one frame per second or slower.
                # Refresh the auto-hiding pointer while waiting for that frame.
                if wakes < 3 and (self.observation_wake_remaining is None or self.observation_wake_remaining > 0):
                    self.wake_pointer(-1 if expected and expected[0] > .5 else 1)
                    wakes += 1
                    if self.observation_wake_remaining is not None: self.observation_wake_remaining -= 1
                    after=self.motion_at
                continue
            after=stamp
            frame_interval = stamp-last_stamp if last_stamp is not None else None
            last_stamp=stamp
            self.observation_frames += 1
            if aspect is not None:
                pic=vision.image(jpeg)
                if abs(pic.width/pic.height-aspect)>.03:
                    raise ValueError('Screen orientation changed; prepare the pointer again')
            method='direct'
            try: point,rect=vision.pointer(jpeg,board_required=board_required,expected=expected,reference=reference)
            except ValueError as error:
                try:
                    if board_required or reference is None or expected is None: raise error
                    point,rect=vision.pointer_difference(jpeg,reference,expected,exclude=exclude)
                    method='difference'
                except ValueError:
                    previous=None; reason=str(error)
                    if wakes < 3 and (self.observation_wake_remaining is None or self.observation_wake_remaining > 0) and time.monotonic()-self.motion_at >= .75 and time.monotonic() < deadline:
                        self.wake_pointer(-1 if expected and expected[0] > .5 else 1)
                        wakes += 1
                        if self.observation_wake_remaining is not None: self.observation_wake_remaining -= 1
                        after=self.motion_at
                    continue
            if expected is not None and math.dist(point,expected) > tolerance:
                previous=None; reason='Pointer has not reached the commanded position'; continue
            if plausible is not None and not plausible(point,rect,jpeg):
                previous=None; reason='Pointer position is not plausible for the commanded calibration move'; continue
            recent=list(self.frame_intervals)[-5:]
            slow=(recent and median(recent)>1/3) or (frame_interval is not None and frame_interval>1/3)
            if slow and not board_required:
                self.last_observation_method=method
                return point,rect,stamp
            if previous is None or math.dist(previous,point)>.003:
                stable_since=stamp
            elif stable_since is not None and stamp-stable_since>=.12:
                self.last_observation_method=method
                return point,rect,stamp
            previous=point
        message='Timed out waiting for one stable grey AssistiveTouch pointer in two fresh frames. '+reason
        self.save_debug(message,expected=expected,reference=reference)
        raise ObservationUnavailable(message)

    def wait_tap(self, pressed, point, rect, timeout=3):
        deadline=time.monotonic()+timeout
        if self.calibration_deadline is not None: deadline=min(deadline,self.calibration_deadline)
        with self.condition:
            while time.monotonic()<deadline:
                self.fresh()
                for stamp,x,y,geometry in self.taps:
                    actual=(rect[0]+x*rect[2],rect[1]+y*rect[3])
                    if stamp>=pressed and math.dist(actual,point)<=.06:
                        self.last_tap_diagnostic = dict(event_fraction=[x,y], mapped_event=list(actual), board_rect=list(rect), dom=geometry, event_minus_pointer=[actual[i]-point[i] for i in (0,1)])
                        return actual
                self.condition.wait(min(.1,max(.001,deadline-time.monotonic())))
        raise ValueError('Safari did not return matching calibration tap feedback within 3 seconds; no coordinate mapping was saved')

    def home(self):
        self.packet(2,mouse())
        for _ in range(20): self.packet(2,mouse(dx=-127)); time.sleep(.008)
        for _ in range(20): self.packet(2,mouse(dy=-127)); time.sleep(.008)
        self.units=(0,0)
        self.motion_at=time.monotonic()

    def move(self, dx,dy, buttons=0):
        if type(dx) is not int or type(dy) is not int or max(abs(dx),abs(dy))>20000:
            raise ValueError('Pointer motion must be integer, bounded to 20000 units')
        # Match native PointerMotion.calibratedPlan: fixed-size, vertical then horizontal.
        # A diagonal's per-axis report size used to depend on the OTHER axis, changing
        # iOS acceleration with the target's angle and invalidating a single line fit.
        for horizontal,total in ((False,dy),(True,dx)):
            remaining=total
            while remaining:
                amount=max(-16,min(16,remaining))
                self.packet(2,mouse(amount if horizontal else 0,0 if horizontal else amount,buttons=buttons))
                remaining-=amount
                time.sleep(.008)
        self.units=(self.units[0]+dx,self.units[1]+dy)
        self.motion_at=time.monotonic()

    def click(self, button='left', gated=False, after=None, expected=None, plausible=None, board_required=True, reference=None):
        if button not in ('left','right','middle'): raise ValueError('Unknown pointer button')
        observation=None
        if gated:
            observation=self.wait_pointer(after if after is not None else time.monotonic(),
                                          expected,plausible,board_required,reference=reference)
        mask={'left':1,'right':2,'middle':4}[button]
        pressed=time.monotonic()
        self.packet(2,mouse(buttons=mask))
        time.sleep(.035)
        self.packet(2,mouse())
        return (*observation[:2],pressed) if observation else None

    def click_current(self, button='left'):
        point=None
        self.wake_pointer()
        try: point,_,_=self.wait_pointer(self.motion_at,board_required=False,timeout=self.observation_budget())
        except ObservationUnavailable:
            if not self.calibration or not self.trusted_calibration(self.calibration):
                raise ValueError('Pointer is hidden and calibration is not trusted. Keep sharing visible and run ensure_session; no click was sent.')
        self.click(button)
        return point

    def drag_calibration(self):
        """Drags tolerate imprecision: any stored mapping beats desktop-scale defaults."""
        if self.calibration: return self.calibration
        if self.path.exists():
            try: saved=json.loads(self.path.read_text())
            except (OSError,ValueError): saved={}
            if saved.get('peer')==self.peer and isinstance(saved.get('calibration'),dict):
                return saved['calibration']
        return DRAG_FALLBACK

    def spotlight(self, name):
        # Bring an app to the front through Spotlight typing (cmd+space, name, enter).
        payload=chord('cmd+space')
        self.packet(1,payload); time.sleep(.024); self.packet(1,keyboard()); time.sleep(.6)
        for character in name:
            modifier,usage=stroke(character)
            self.packet(1,keyboard(modifier,usage)); time.sleep(.024); self.packet(1,keyboard()); time.sleep(.024)
        modifier,usage=stroke('\n')
        self.packet(1,keyboard(modifier,usage)); time.sleep(.024); self.packet(1,keyboard()); time.sleep(.5)

    def open_calibration_board(self):
        """Open the board and wait for it; pull the Safari sharing tab forward if absent."""
        self.open_board(True)
        try: self.wait_board(timeout=3)
        except ValueError:
            self.fresh()
            self.spotlight('Safari')
        for attempt in range(2):
            try: return self.wait_board()
            except ValueError:
                self.fresh()
                if attempt: raise
                self.open_board(True)

    def calibrate(self):
        candidate=None
        self.calibration_deadline=time.monotonic()+CALIBRATION_BUDGET
        try:
            rect=self.open_calibration_board()
            initial=vision.image(self.jpeg)
            aspect=initial.width/initial.height
            for measurement in range(2):
                self.check_motion_state(aspect)
                try:
                    samples=[]
                    diagnostics=[]

                    def measure(ux,uy,target=None):
                        self.check_motion_state(aspect)
                        self.home(); self.move(ux,uy)
                        def plausible(point,rect,jpeg):
                            current=vision.image(jpeg)
                            if abs(current.width/current.height-aspect)>.03:
                                raise ValueError('Orientation changed during calibration; prepare again with Safari in front')
                            # Never click the Back control or outside the safe white board interior.
                            safe=vision.safe_calibration_point(point,rect)
                            if samples:
                                safe=safe and math.dist(point,samples[-1][2:])>.04
                            if target is None and samples:
                                safe=safe and point[0]-samples[-1][2]>.04 and point[1]-samples[-1][3]>.04
                            return safe and (target is None or math.dist(point,target)<.18)
                        for attempt in range(2):
                            try:
                                point,visible,pressed=self.click(gated=True,after=self.motion_at,plausible=plausible)
                                break
                            except ObservationUnavailable:
                                self.check_motion_state(aspect)
                                if attempt: raise
                                self.wake_pointer()
                        self.last_tap_diagnostic = None
                        actual=self.wait_tap(pressed,point,visible)
                        diagnostic=self.last_tap_diagnostic or dict(event_fraction=None, mapped_event=list(actual), board_rect=list(visible), dom=None, event_minus_pointer=[actual[i]-point[i] for i in (0,1)])
                        diagnostic.update(device_id=self.directory.name, units=[ux,uy], pointer=list(point))
                        diagnostics.append(diagnostic)
                        log.warning('calibration_sample %s', json.dumps(diagnostic, separators=(',',':')))
                        samples.append((ux,uy,*point))

                    # Seed the scale, then measure all four board quadrants and its center.
                    measure(64,128)
                    measure(160,320)
                    spans=[];offsets=[]
                    for axis,minimum in ((0,100),(1,200)):
                        delta=samples[1][axis+2]-samples[0][axis+2]
                        if delta<=.04: raise ValueError('Calibration seed was clamped; check tracking speed and standard pointer size')
                        span=(samples[1][axis]-samples[0][axis])/delta
                        if not minimum<=span<=20000: raise ValueError('Calibration seed scale is invalid; check AssistiveTouch settings')
                        spans.append(span);offsets.append(samples[0][axis+2]-samples[0][axis]/span)
                    for x,y in ((.22,.22),(.78,.22),(.22,.78),(.78,.78),(.5,.5)):
                        target=(rect[0]+x*rect[2],rect[1]+y*rect[3])
                        ux,uy=(round((target[a]-offsets[a])*spans[a]) for a in (0,1))
                        measure(ux,uy,target)
                    fitted=mapping.fit(samples,aspect)
                    deltas=[d['event_minus_pointer'] for d in diagnostics]
                    fitted['event_pointer_delta'] = dict(mean=[sum(d[i] for d in deltas)/len(deltas) for i in (0,1)], max_abs=[max(abs(d[i]) for d in deltas) for i in (0,1)], count=len(deltas))
                    fitted['frame_size'] = list(vision.dimensions(self.jpeg))
                    log.warning('calibration_summary %s', json.dumps(fitted['event_pointer_delta'], separators=(',',':')))
                except ValueError as error:
                    self.check_motion_state(aspect)
                    retryable=any(word in str(error).lower() for word in ('inconsistent','clamped'))
                    if isinstance(error,ObservationUnavailable):
                        if candidate is None: raise
                    elif retryable:
                        if measurement==0: continue
                        if candidate is None: raise
                    else: raise
                else:
                    if candidate is None or max(fitted['fit_rms'])<max(candidate['fit_rms']):
                        candidate=fitted
                    if max(candidate['fit_rms'])<=GOOD_FIT_RMS: break
            self.check_motion_state(aspect)
        except (ValueError,OSError) as error:
            # An in-flight attempt never destroys a previously stored mapping.
            raise ValueError('Calibration failed; the stored mapping is untouched. Keep Safari sharing visible, use the standard grey AssistiveTouch pointer, keep tracking speed/Zoom fixed, and prepare again. '+str(error)) from error
        else:
            candidate['suspect']=False
            self.calibration=candidate
            self.save()
        finally:
            self.calibration_deadline=None
            self.open_board(False)

    def ready(self):
        self.peer=self.pairing.associate(self)
        if self.calibration:
            pic=vision.image(self.jpeg)
            if self.calibration.get('version')!=mapping.VERSION or self.calibration.get('motion')!=mapping.MOTION or abs(pic.width/pic.height-self.calibration['aspect'])>.03:
                self.calibration=None
        if self.calibration and self.calibration.get('suspect'):
            if self.calibration.get('suspect_recalibration_attempted'):
                raise ValueError('Automatic recalibration already attempted; keep Safari sharing visible and explicitly calibrate again')
            self.calibration['suspect_recalibration_attempted']=True
            self.save()
            self.calibrate()
        elif not self.calibration: self.calibrate()

    def check_motion_state(self, aspect):
        """Safety failures must never be treated as a retryable observation timeout."""
        self.fresh()
        if self.peer not in [p['id'] for p in self.radio.peers() if set(p['reports'])=={1,2}]:
            raise ValueError('Paired iPhone disconnected')
        pic=vision.image(self.jpeg)
        if abs(pic.width/pic.height-aspect)>.03:
            raise ValueError('Screen orientation changed; prepare the pointer again')

    def wake_pointer(self, direction=1):
        # Net zero; move inward first when the target is near the right edge.
        self.move(3*direction, 0)
        self.move(-3*direction, 0)

    def landing_offset(self, c):
        if c.get('landing_frame_size') != list(vision.dimensions(self.jpeg)):
            c.pop('landing_samples', None)
            c.pop('landing_frame_size', None)
            return (0.,0.)
        samples=c.get('landing_samples', [])
        if len(samples)<2: return (0.,0.)
        center=[median(s[i] for s in samples) for i in (0,1)]
        agreeing=[s for s in samples if math.dist(s,center)<=.01]
        if len(agreeing)<2: return (0.,0.)
        offset=[median(s[i] for s in agreeing) for i in (0,1)]
        magnitude=math.hypot(*offset)
        if magnitude>.05: offset=[v*.05/magnitude for v in offset]
        return tuple(offset)

    def save_debug(self, reason, expected=None, landing=None, reference=None):
        """Schedule bounded best-effort diagnostics without waiting for disk IO."""
        name=str(time.time_ns())+'-'+uuid.uuid4().hex[:8]
        def write():
            while True:
                with self.debug_lock:
                    if not self.debug_queue:
                        self.debug_thread=None
                        return
                    name,jpeg,target,reason,expected,landing,reference=self.debug_queue.popleft()
                try:
                    directory=self.directory/'debug'
                    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                    paths=[]
                    for suffix,data in (('frame',jpeg),('reference',reference)):
                        if data:
                            path=directory/(name+'-'+suffix+'.jpg'); path.write_bytes(data); paths.append(str(path))
                    meta=directory/(name+'.json')
                    meta.write_text(json.dumps(dict(target=target,expected=expected,landing=landing,reason=reason,frames=paths)))
                    files=sorted(directory.iterdir(),key=lambda p:p.name,reverse=True)
                    for old in files[30:]: old.unlink(missing_ok=True)
                    log.warning('pointer_debug %s',meta)
                except Exception as error:
                    log.warning('pointer_debug unavailable: %s',type(error).__name__)
        try:
            with self.debug_lock:
                self.debug_queue.append((name,self.jpeg,getattr(self,'debug_target',None),reason,expected,landing,reference))
                if self.debug_thread is None:
                    self.debug_thread=threading.Thread(target=write,daemon=True)
                    self.debug_thread.start()
            return str(self.directory/'debug'/(name+'.json'))
        except Exception:
            return None

    def observation_budget(self, minimum=VERIFY_BUDGET):
        recent = list(self.frame_intervals)[-5:]
        cadence = max(median(recent), recent[-1]) if recent else 1/30
        return min(3.2, max(minimum, cadence * 2.5 + .2))

    def trusted_calibration(self, c):
        return bool(c.get('version') == mapping.VERSION and c.get('motion') == mapping.MOTION
                    and not c.get('suspect') and len(c.get('fit_rms', [])) == 2
                    and max(c['fit_rms']) <= GOOD_FIT_RMS
                    and c.get('frame_size') == list(vision.dimensions(self.jpeg)))

    def absolute(self,x,y,calibration=None,verify_budget=VERIFY_BUDGET):
        x,y=fraction(x),fraction(y)
        c=calibration or self.calibration
        if c is None or c.get('version')!=mapping.VERSION or c.get('motion')!=mapping.MOTION:
            raise ValueError('Pointer calibration is missing or incompatible; run ensure_session with Safari sharing visible to calibrate, then retry')
        started=time.monotonic()
        old_frame=c.get('landing_frame_size')
        offset=self.landing_offset(c) if calibration is None else (0.,0.)
        if calibration is None and old_frame is not None and 'landing_frame_size' not in c:
            self.save()
        self.check_motion_state(c['aspect'])
        ux,uy=round((max(0,min(1,x-offset[0]))-c['offset_x'])*c['width']),round((max(0,min(1,y-offset[1]))-c['offset_y'])*c['height'])
        self.debug_target=[x,y]
        self.home()
        self.wake_pointer()
        # Reference has its own small budget. Post-move observation always gets its
        # own budget, including on a throttled background Safari capture.
        try: _,reference=self.capture_after(self.motion_at,timeout=min(.4,self.observation_budget()/3))
        except ObservationUnavailable: reference=self.jpeg
        self.check_motion_state(c['aspect'])
        self.move(ux,uy)
        moved_at=time.monotonic()
        deadline=moved_at+self.observation_budget(verify_budget)
        point=None; first=None; last_seen=None; corrections=[]; frames=0; predicted=None; method=None
        reason='pointer not observed'
        self.observation_wake_remaining=3
        try:
            point,_,_=self.wait_pointer(self.motion_at,expected=(x,y), tolerance=.18,
                                       board_required=False,aspect=c['aspect'],
                                       timeout=max(.001,deadline-time.monotonic()),reference=reference)
            frames+=self.observation_frames
            first=last_seen=point
            method=self.last_observation_method if hasattr(self,'last_observation_method') else 'direct'
            for _ in range(2):
                if math.dist(point,(x,y))<=mapping.TOLERANCE or time.monotonic()>=deadline: break
                dx,dy=round((x-point[0])*c['width']),round((y-point[1])*c['height'])
                if dx==dy==0: break
                corrections.append([dx,dy])
                predicted=[point[0]+dx/c['width'],point[1]+dy/c['height']]
                correction_reference=self.jpeg
                self.move(dx,dy)
                point=None
                point,_,_=self.wait_pointer(self.motion_at,expected=(x,y),tolerance=.18,
                                            board_required=False,aspect=c['aspect'],
                                            timeout=max(.001,deadline-time.monotonic()), reference=correction_reference,
                                            exclude=last_seen)
                frames+=self.observation_frames
                last_seen=point
                method=self.last_observation_method if hasattr(self,'last_observation_method') else 'direct'
                predicted=None
        except ObservationUnavailable as error:
            frames+=self.observation_frames
            reason=str(error)
            self.save_debug(reason,expected=(x,y),landing=last_seen,reference=correction_reference if corrections else reference)
        else:
            reason='measured landing distance %.2f%% of screen' % (100*math.dist(point,(x,y)))
        finally:
            self.observation_wake_remaining=None
        self.check_motion_state(c['aspect'])
        verified=bool(point and math.dist(point,(x,y))<=mapping.TOLERANCE)
        if calibration is None and first is not None:
            # Correct for the offset already applied so samples estimate the underlying bias.
            commanded=(max(0,min(1,x-offset[0])),max(0,min(1,y-offset[1])))
            errors=[first[i]-commanded[i] for i in (0,1)]
            c.setdefault('landing_samples',[]).append(errors)
            c['landing_samples']=c['landing_samples'][-8:]
            c['landing_frame_size']=list(vision.dimensions(self.jpeg))
            if point is not None and corrections and math.dist(point,(x,y))>SUSPECT_MISS:
                c['suspect']=True
            self.save()
        self.verification_reason=reason
        self.first_landing_distance=math.dist(first,(x,y)) if first else None
        self.observation = dict(target=[x,y], units=[ux,uy], first_landing=first, final_landing=point, click_sent=False,
                                corrections=corrections, verified=verified, observation_reason=reason,
                                landing='observed' if point else ('predicted' if predicted and math.dist(predicted,(x,y))<=mapping.TOLERANCE else 'unknown'),
                                predicted_landing=predicted, observation_method=method, landing_offset=list(offset),
                                landing_error=[point[0]-x,point[1]-y] if point else None,
                                frames_seen=frames, timings=dict(total_seconds=round(time.monotonic()-started,3),
                                observation_seconds=round(time.monotonic()-moved_at,3)))
        return point,verified

    def stop_capture(self):
        self.generation+=1
        self.at=0
        with self.condition: self.condition.notify_all()
        with self.input_lock:
            if self.peer: self.radio.release(self.peer)
        return dict(ok=True,message='Input canceled and released for this phone')

    def close(self):
        with self.condition:
            self.closed = True
            self.generation += 1
            self.at = 0
            self.board_open = False
            self.condition.notify_all()
        # Invalidate any shared association probe before waiting for its lock.
        with self.pairing.lock:
            if self in self.pairing.engines: self.pairing.engines.remove(self)
            with self.input_lock:
                peer, self.peer = self.peer, None
                with self.condition:
                    self.calibration = None
                    self.jpeg, self.frame_id = b'', None
                    self.taps.clear(); self.hover = None
                if peer: self.radio.release(peer)

    def remove(self):
        try:
            self.close()
        finally:
            self.path.unlink(missing_ok=True)

    def request(self,operation,**args):
        if self.closed: raise ValueError('Device removed or control closed')
        if operation=='capture_tap':
            with self.condition:
                if self.closed: raise ValueError('Device removed or control closed')
                self.taps.append((time.monotonic(),fraction(args['x']),fraction(args['y']),tap_geometry(args.get('geometry'))))
                self.condition.notify_all()
            return dict(ok=True,message='Calibration feedback received')
        if operation=='capture_pointer':
            if not isinstance(args.get('epoch'),str) or len(args['epoch'])>64: raise ValueError('Invalid pairing epoch')
            with self.condition:
                if self.closed: raise ValueError('Device removed or control closed')
                self.hover=(time.monotonic(),args.get('epoch'))
            return dict(ok=True)
        if operation=='stop_control': return self.stop_capture()
        if operation=='get_device_status':
            error = ''
            try: peers = self.radio.peers()
            except (OSError, ValueError) as failure: peers, error = [], str(failure)
            connected = any(p['id'] == self.peer and set(p['reports']) == {1, 2} for p in peers)
            return dict(ok=True,status=dict(connected=connected, bluetooth_connected=connected, bluetooth_error=error,
                        peer=self.peer, calibrationReady=self.calibration_compatible(),captureActive=self.is_fresh(),
                        sharing_active=self.is_fresh(),
                        bluetooth_status='Bluetooth connected' if connected else "Bluetooth not connected — control won't work",
                        screen_share_status='Screen sharing live' if self.is_fresh() else "Screen sharing off — control won't work"),message=self.label)
        self.sending_generation=self.generation
        if operation in ('pointer_tap','pointer_move'):
            self.observation = dict(target=[args.get('x'),args.get('y')], units=None, first_landing=None, final_landing=None, corrections=[], verified=False, click_sent=False, observation_reason='move not completed', landing_error=None, frames_seen=0, timings={})
        try:
            self.fresh()
            self.pairing.associate(self)
            message=None
            if operation in ('ensure_session','prepare_session','pointer_quick_probe'):
                return_app=args.get('return_app')
                if return_app is not None:
                    if not isinstance(return_app,str) or not 1<=len(return_app.encode())<=80:
                        raise ValueError('return_app must be an app name of at most 80 ASCII bytes')
                    [stroke(c) for c in return_app]  # Validate before touching the phone.
            if operation=='pointer_quick_probe':
                message='Pointer ready; calibration is reused automatically' if self.calibration_compatible() else 'Pointer not calibrated; run ensure_session on the Safari sharing tab to calibrate'
            elif operation in ('ensure_session','prepare_session'):
                self.ready()
                if return_app is not None: self.spotlight(return_app)
            elif operation=='set_page_board':
                self.open_board(bool(args.get('aim')))
                if args.get('aim'): self.wait_board()
            elif operation=='pointer_home': self.home()
            elif operation=='move_pointer':
                dx,dy=args.get('dx',0),args.get('dy',0)
                if type(dx) is not int or type(dy) is not int or max(abs(dx),abs(dy))>127: raise ValueError('Relative deltas must be -127…127')
                self.move(dx,dy)
                try: point,_,_=self.wait_pointer(self.motion_at,board_required=False,timeout=1)
                except ObservationUnavailable: point=None
                message='Pointer moved to (%.3f,%.3f)'%point if point else 'Pointer moved; the pointer was not visible to verify'
            elif operation=='click':
                point=self.click_current(args.get('button','left'))
                message='Clicked at (%.3f,%.3f)'%point if point else 'Clicked at the current pointer; the pointer was not visible to verify'
            elif operation in ('pointer_move','pointer_tap'):
                point,verified=self.absolute(args['x'],args['y'])
                target='(%s,%s)'%(args['x'],args['y'])
                if operation=='pointer_tap':
                    prediction=self.observation['predicted_landing']
                    if point is not None and math.dist(point,(args['x'],args['y']))>SUSPECT_MISS:
                        raise ValueError('Observed pointer landing is more than 3% from the target; no click was sent. '+self.verification_reason)
                    if point is None and prediction is not None and math.dist(prediction,(args['x'],args['y']))>mapping.TOLERANCE:
                        raise ValueError('Pointer vanished after correction and its predicted landing is more than 1% from the target; no click was sent. '+self.verification_reason)
                    if point is None and prediction is None and (self.observation['first_landing'] is not None or not self.trusted_calibration(self.calibration)):
                        raise ValueError('Pointer landing is unverified and calibration is not safe for a blind tap. Keep sharing visible, use the standard pointer and run ensure_session to recalibrate; no click was sent. '+self.verification_reason)
                    self.observation['click_sent'] = None  # A failed transport may leave delivery uncertain.
                    self.click()
                    self.observation['click_sent'] = True
                    if verified: message='Tapped at '+target+'; pointer position visually verified'
                    elif self.observation['landing']=='predicted': message='Tapped at '+target+' using a predicted landing; final pointer position was not visually verified'
                    elif point is None: message='Tapped at '+target+' using calibration only; pointer was not visible to verify'
                    else: message='Tapped at '+target+'; pointer read (%.3f,%.3f) and was not within 1%% of the target'%point
                elif verified: message='Pointer is at '+target+'; position visually verified'
                elif point is None: message='Pointer moved using calibration only; the pointer was not visible to verify'
                else: message='Pointer read (%.3f,%.3f) near the target; position not verified'%point
                log.warning('pointer_action %s', json.dumps(dict(operation=operation, **self.observation), separators=(',',':')))
                if self.calibration.get('suspect'):
                    message+='; calibration marked for automatic recalibration on next ensure_session'
            elif operation=='type_text':
                text=args['text']
                if not isinstance(text,str) or len(text.encode())>500: raise ValueError('Text must contain at most 500 ASCII bytes')
                strokes=[stroke(c) for c in text]  # Validate all text before any report.
                for modifier,usage in strokes:
                    self.packet(1,keyboard(modifier,usage)); time.sleep(.024)
                    self.packet(1,keyboard()); time.sleep(.024)
            elif operation=='send_key':
                payload=chord(args['key']); self.packet(1,payload); time.sleep(.024); self.packet(1,keyboard())
            elif operation=='scroll':
                amount=args.get('dy',0)
                if type(amount) is not int or abs(amount)>1200: raise ValueError('Scroll dy must be an integer -1200…1200')
                while amount:
                    part=max(-127,min(127,amount)); self.packet(2,mouse(wheel=part)); amount-=part; time.sleep(.008)
                self.packet(2,mouse())
            elif operation in ('swipe','advance_feed'):
                direction=args.get('profile','up') if operation=='swipe' else ('up' if args.get('dy',1)==1 else 'down')
                if direction not in ('up','down','left','right'): raise ValueError('Swipe direction must be up/down/left/right')
                if operation=='advance_feed' and args.get('dy',1) not in (-1,1): raise ValueError('Feed dy must be 1 or -1')
                start={'up':(.5,.7),'down':(.5,.3),'left':(.75,.5),'right':(.25,.5)}[direction]
                end={'up':(.5,.3),'down':(.5,.7),'left':(.25,.5),'right':(.75,.5)}[direction]
                # Drags never wait for the pointer to be found on screen: press, drag and
                # release open-loop. A stored mapping (even an older profile, reloaded
                # from disk) is used when present, otherwise conservative fixture-scale
                # gains apply.
                c=self.drag_calibration()
                self.home()
                self.move(round((start[0]-c['offset_x'])*c['width']),round((start[1]-c['offset_y'])*c['height']))
                self.packet(2,mouse(buttons=1)); time.sleep(.05)
                self.move(round((end[0]-start[0])*c['width']),round((end[1]-start[1])*c['height']),buttons=1)
                self.packet(2,mouse())
                message='Dragged '+direction+(', always verify the next item in the returned frame' if operation=='advance_feed' else '')
            else: raise ValueError('Unknown control operation')
            ended=time.monotonic()
            if operation in ('swipe','advance_feed','scroll'):
                time.sleep(.2)
                self.wait_frame(time.monotonic())
            elif operation!='set_page_board': self.wait_frame(ended)
            if operation in ('ensure_session','prepare_session','pointer_quick_probe'):
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    try: vision.board(self.jpeg)
                    except ValueError: break
                    self.wait_frame(self.at)
            self.error=None
            with self.condition:
                jpeg,frame_id,frame_at=self.jpeg,self.frame_id,self.at
            result = dict(ok=True,message=message or 'Input/preparation completed. Inspect the returned screen to verify the UI outcome.',inputEndedAt=ended,
                        commandID=str(uuid.uuid4()),
                        frame=dict(frameID=frame_id,captureActive=self.is_fresh(),ageMilliseconds=int((time.monotonic()-frame_at)*1000),
                                   mimeType='image/jpeg',width=vision.dimensions(jpeg)[0],height=vision.dimensions(jpeg)[1],base64=base64.b64encode(jpeg).decode()))
            if operation in ('pointer_tap','pointer_move'): result.update(self.observation)
            if operation in ('swipe','advance_feed','scroll'):
                result.update(settled=False, settle_hint='UI may still be animating; take a fresh screenshot before targeting.')
            return result
        except (ValueError,OSError) as error:
            self.error=str(error)
            self.open_board(False)
            if self.peer: self.radio.release(self.peer)
            result=dict(ok=False,message=str(error))
            if operation in ('pointer_tap','pointer_move') and hasattr(self,'observation'):
                if not self.observation.get('click_sent'):
                    self.debug_target=self.observation.get('target')
                    self.save_debug(str(error),expected=self.observation.get('target'),landing=self.observation.get('final_landing'))
                result.update(self.observation)
                if result.get('click_sent') is None: result['message'] += ' A click may have been sent; inspect the screen before retrying.'
                elif result.get('click_sent'): result['message'] += ' A click was sent; inspect the screen before retrying.'
                log.warning('pointer_action_failed %s', json.dumps(dict(operation=operation, **self.observation), separators=(',',':')))
            return result
