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

from .hid import mouse, keyboard, chord, stroke, fraction
from . import vision
from . import calibration as mapping


VERIFY_BUDGET = 2.0
# A feed drag only needs a roughly centered start and ~40% of the screen height
# of travel; these gains mirror the calibration test fixtures for a known screen.
DRAG_FALLBACK = dict(width=600, height=1000, offset_x=0, offset_y=0)


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file: json.dump(value, file)
    os.replace(temporary, path)


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
        self.generation, self.sending_generation = 0, 0
        self.units = (0,0)
        self.error = None
        self.closed = False
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
            self.jpeg, self.at, self.frame_id = jpeg, time.monotonic(), str(uuid.uuid4())
            self.condition.notify_all()
            return dict(frameID=self.frame_id, fps=15, boardOpen=self.board_open,
                        boardEpoch=self.board_epoch, pairEpoch=self.pair_epoch)

    def open_board(self, value, pair_epoch=''):
        with self.condition:
            self.board_open=value; self.board_epoch+=1; self.pair_epoch=pair_epoch

    def fresh(self):
        if self.generation != self.sending_generation: raise ValueError('Input canceled')
        if not self.is_fresh(): raise ValueError('Screen sharing stopped or capture is stale; input released')

    def packet(self, report, payload):
        with self.input_lock:
            self.fresh()
            if not self.peer: raise ValueError('No paired iPhone associated with this screen')
            self.radio.send(self.peer, report, payload)

    def capture_after(self, after, timeout=3):
        deadline=time.monotonic()+timeout
        with self.condition:
            while True:
                self.fresh()
                if self.at > after: return self.at, self.jpeg
                remaining=deadline-time.monotonic()
                if remaining <= 0: raise ValueError('No fresh screen arrived while waiting for the shared screen to catch up')
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

    def wait_pointer(self, after, expected=None, plausible=None, board_required=True, timeout=4, tolerance=mapping.TOLERANCE, reference=None):
        deadline=time.monotonic()+timeout
        previous=None
        stable_since=None
        reason='Pointer has not appeared'
        while time.monotonic()<deadline:
            try: stamp,jpeg=self.capture_after(after,max(.001,deadline-time.monotonic()))
            except ValueError:
                self.fresh()  # Cancellation/stale streams retain their own actionable error.
                break
            after=stamp
            try: point,rect=vision.pointer(jpeg,board_required=board_required,expected=expected,reference=reference)
            except ValueError as error:
                previous=None; reason=str(error); continue
            if expected is not None and math.dist(point,expected) > tolerance:
                previous=None; reason='Pointer has not reached the commanded position'; continue
            if plausible is not None and not plausible(point,rect,jpeg):
                previous=None; reason='Pointer position is not plausible for the commanded calibration move'; continue
            if previous is None or math.dist(previous,point)>.003:
                stable_since=stamp
            elif stable_since is not None and stamp-stable_since>=.12:
                return point,rect,stamp
            previous=point
        raise ValueError('Timed out waiting for one stable grey AssistiveTouch pointer in two fresh frames; no click was sent. '+reason)

    def wait_tap(self, pressed, point, rect, timeout=3):
        deadline=time.monotonic()+timeout
        with self.condition:
            while time.monotonic()<deadline:
                self.fresh()
                for stamp,x,y in self.taps:
                    actual=(rect[0]+x*rect[2],rect[1]+y*rect[3])
                    if stamp>=pressed and math.dist(actual,point)<=.06: return actual
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
        # Locate the pointer only to report where the click lands; screen sharing
        # lags and video apps hide the pointer, so never refuse a click here.
        point=None
        try: point,_,_=self.wait_pointer(time.monotonic(),board_required=False,timeout=1)
        except ValueError: pass
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
        try:
            rect=self.open_calibration_board()
            initial=vision.image(self.jpeg)
            aspect=initial.width/initial.height
            samples=[]

            def measure(ux,uy,target=None):
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
                point,visible,pressed=self.click(gated=True,after=self.motion_at,plausible=plausible)
                actual=self.wait_tap(pressed,point,visible)
                samples.append((ux,uy,*actual))

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
            candidate=mapping.fit(samples,aspect)
            # A separate held-out target must converge too; a fitted line alone is not proof.
            target=(rect[0]+.5*rect[2],rect[1]+.38*rect[3])
            _,verified=self.absolute(*target,calibration=candidate)
            if not verified: raise ValueError('the held-out calibration target did not land within 1% of the screen; check that the green-bordered board is fully visible and AssistiveTouch tracking speed/Zoom are unchanged')
        except (ValueError,OSError) as error:
            # An in-flight attempt never destroys a previously stored mapping.
            raise ValueError('Calibration failed; the stored mapping is untouched. Keep Safari sharing visible, use the standard grey AssistiveTouch pointer, keep tracking speed/Zoom fixed, and prepare again. '+str(error)) from error
        else:
            self.calibration=candidate
            self.save()
        finally:
            self.open_board(False)

    def ready(self):
        self.peer=self.pairing.associate(self)
        if self.calibration:
            pic=vision.image(self.jpeg)
            if self.calibration.get('version')!=mapping.VERSION or self.calibration.get('motion')!=mapping.MOTION or abs(pic.width/pic.height-self.calibration['aspect'])>.03:
                self.calibration=None
        if not self.calibration: self.calibrate()

    def absolute(self,x,y,calibration=None):
        """Open-loop calibrated move; visual verification is bounded and best-effort."""
        x,y=fraction(x),fraction(y)
        c=calibration or self.calibration
        if c is None or c.get('version')!=mapping.VERSION or c.get('motion')!=mapping.MOTION:
            raise ValueError('Pointer calibration is missing or incompatible; run ensure_session with Safari sharing visible to calibrate, then retry')
        if self.peer not in [p['id'] for p in self.radio.peers() if set(p['reports'])=={1,2}]: raise ValueError('Paired iPhone disconnected')
        pic=vision.image(self.jpeg)
        if abs(pic.width/pic.height-c['aspect'])>.03: raise ValueError('Screen orientation changed; prepare the pointer again')
        ux,uy=round((x-c['offset_x'])*c['width']),round((y-c['offset_y'])*c['height'])
        self.home()
        deadline=time.monotonic()+VERIFY_BUDGET
        try: stamp,reference=self.capture_after(time.monotonic(),timeout=max(.001,deadline-time.monotonic()))
        except ValueError: stamp,reference=time.monotonic(),None
        self.move(ux,uy)
        point=None
        try:
            point,_,_=self.wait_pointer(stamp,expected=(x,y),board_required=False,tolerance=.18,
                                        timeout=max(.001,deadline-time.monotonic()),reference=reference)
            for _ in range(2):
                if math.dist(point,(x,y))<=mapping.TOLERANCE: return point,True
                if time.monotonic()>=deadline: break
                dx,dy=round((x-point[0])*c['width']),round((y-point[1])*c['height'])
                if dx==dy==0: break
                self.move(dx,dy)
                point,_,_=self.wait_pointer(self.motion_at,expected=(x,y),board_required=False,tolerance=.18,
                                            timeout=max(.001,deadline-time.monotonic()))
        except ValueError: pass  # Pointer invisible or verification budget spent; keep the open-loop landing spot.
        return point, bool(point and math.dist(point,(x,y))<=mapping.TOLERANCE)

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
                self.taps.append((time.monotonic(),fraction(args['x']),fraction(args['y'])))
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
                except ValueError: point=None
                message='Pointer moved to (%.3f,%.3f)'%point if point else 'Pointer moved; the pointer was not visible to verify'
            elif operation=='click':
                point=self.click_current(args.get('button','left'))
                message='Clicked at (%.3f,%.3f)'%point if point else 'Clicked at the current pointer; the pointer was not visible to verify'
            elif operation in ('pointer_move','pointer_tap'):
                point,verified=self.absolute(args['x'],args['y'])
                target='(%s,%s)'%(args['x'],args['y'])
                if operation=='pointer_tap':
                    self.click()
                    if verified: message='Tapped at '+target+'; pointer position visually verified'
                    elif point is None: message='Tapped at '+target+' using calibration only; pointer was not visible to verify'
                    else: message='Tapped at '+target+'; pointer read (%.3f,%.3f) and was not within 1%% of the target'%point
                elif verified: message='Pointer is at '+target+'; position visually verified'
                elif point is None: message='Pointer moved using calibration only; the pointer was not visible to verify'
                else: message='Pointer read (%.3f,%.3f) near the target; position not verified'%point
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
            if operation!='set_page_board': self.wait_frame(ended)
            if operation in ('ensure_session','prepare_session','pointer_quick_probe'):
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    try: vision.board(self.jpeg)
                    except ValueError: break
                    self.wait_frame(self.at)
            self.error=None
            with self.condition:
                jpeg,frame_id,frame_at=self.jpeg,self.frame_id,self.at
            return dict(ok=True,message=message or 'Input/preparation completed. Inspect the returned screen to verify the UI outcome.',inputEndedAt=ended,
                        commandID=str(uuid.uuid4()),
                        frame=dict(frameID=frame_id,captureActive=self.is_fresh(),ageMilliseconds=int((time.monotonic()-frame_at)*1000),
                                   mimeType='image/jpeg',base64=base64.b64encode(jpeg).decode()))
        except (ValueError,OSError) as error:
            self.error=str(error)
            self.open_board(False)
            if self.peer: self.radio.release(self.peer)
            return dict(ok=False,message=str(error))
