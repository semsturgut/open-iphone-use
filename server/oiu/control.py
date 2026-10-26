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

    @property
    def label(self):
        if self.error: return self.error
        health=getattr(self.radio,'startup',{})
        if health.get('error'): return 'Bluetooth: '+health['state']
        return 'Pointer ready' if self.calibration and self.peer else ('Paired; first tap prepares pointer' if self.peer else 'Pair iPhone, then tap screen')

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

    def wait_board(self, timeout=3):
        deadline=time.monotonic()+timeout
        after=time.monotonic()
        previous=None
        while time.monotonic()<deadline:
            stamp,jpeg=self.capture_after(after,max(.001,deadline-time.monotonic()))
            after=stamp
            try: rect=vision.board(jpeg)[1]
            except ValueError:
                previous=None; continue
            if previous and max(abs(a-b) for a,b in zip(previous,rect)) <= .01: return rect
            previous=rect
        raise ValueError('Calibration board did not become visible and stable within 3 seconds. Keep Safari sharing visible; a green status dot alone is not the calibration board')

    def wait_pointer(self, after, expected=None, plausible=None, board_required=True, timeout=3):
        deadline=time.monotonic()+timeout
        previous=None
        reason='Pointer has not appeared'
        while time.monotonic()<deadline:
            stamp,jpeg=self.capture_after(after,max(.001,deadline-time.monotonic()))
            after=stamp
            try: point,rect=vision.pointer(jpeg,board_required=board_required)
            except ValueError as error:
                previous=None; reason=str(error); continue
            if expected is not None and math.dist(point,expected) > .07:
                previous=None; reason='Pointer has not reached the commanded position'; continue
            if plausible is not None and not plausible(point,rect,jpeg):
                previous=None; reason='Pointer position is not plausible for the commanded calibration move'; continue
            if previous is not None and math.dist(previous,point) <= .01:
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
        steps=max(1,math.ceil(max(abs(dx),abs(dy))/16))
        oldx=oldy=0
        for index in range(1,steps+1):
            x,y=round(dx*index/steps),round(dy*index/steps)
            self.packet(2,mouse(x-oldx,y-oldy,buttons=buttons))
            oldx,oldy=x,y
            time.sleep(.008)
        self.units=(self.units[0]+dx,self.units[1]+dy)
        self.motion_at=time.monotonic()

    def click(self, button='left', gated=False, after=None, expected=None, plausible=None, board_required=True):
        if button not in ('left','right','middle'): raise ValueError('Unknown pointer button')
        observation=None
        if gated:
            observation=self.wait_pointer(after if after is not None else time.monotonic(),
                                          expected,plausible,board_required)
        mask={'left':1,'right':2,'middle':4}[button]
        pressed=time.monotonic()
        self.packet(2,mouse(buttons=mask))
        time.sleep(.035)
        self.packet(2,mouse())
        return (*observation[:2],pressed) if observation else None

    def calibrate(self):
        self.open_board(True)
        try:
            self.wait_board()
            initial=vision.image(self.jpeg)
            initial_aspect=initial.width/initial.height
            self.home()
            samples=[]
            for ux,uy in ((64,128),(192,384)):
                self.move(ux-self.units[0],uy-self.units[1])
                def plausible(point,rect,jpeg):
                    current=vision.image(jpeg)
                    if abs(current.width/current.height-initial_aspect)>.03:
                        raise ValueError('Orientation changed during calibration; keep the phone still and prepare again')
                    if not samples:
                        # Broad initial estimate allows iOS acceleration/DPI differences,
                        # while rejecting an old pointer on the far side of the board.
                        nominal=(min(.8,ux/initial.width),min(.8,uy/initial.height))
                        return math.dist(point,nominal)<.4
                    old=samples[-1]
                    dx,dy=point[0]-old[2],point[1]-old[3]
                    return dx>.06 and dy>.06 and 100<=(ux-old[0])/dx<=20000 and 200<=(uy-old[1])/dy<=20000
                point,rect,pressed=self.click(gated=True,after=self.motion_at,plausible=plausible)
                x,y=self.wait_tap(pressed,point,rect)
                samples.append((ux,uy,x,y))
            width,height=((samples[1][axis]-samples[0][axis])/(samples[1][axis+2]-samples[0][axis+2]) for axis in (0,1))
            if not 100<=width<=20000 or not 200<=height<=20000: raise ValueError('Calibration was clamped or inconsistent; keep the pointer board visible')
            pic=vision.image(self.jpeg)
            self.calibration=dict(width=width,height=height,offset_x=samples[0][2]-64/width,
                                  offset_y=samples[0][3]-128/height,aspect=pic.width/pic.height,version=1)
            self.save()
        finally: self.open_board(False)

    def ready(self):
        self.peer=self.pairing.associate(self)
        if self.calibration:
            pic=vision.image(self.jpeg)
            if self.calibration.get('version')!=1 or abs(pic.width/pic.height-self.calibration['aspect'])>.03:
                self.calibration=None
        if not self.calibration: self.calibrate()

    def absolute(self,x,y):
        x,y=fraction(x),fraction(y)
        if not self.calibration: raise ValueError('Run ensure_session with Safari sharing visible before coordinate input')
        if self.peer not in [p['id'] for p in self.radio.peers() if set(p['reports'])=={1,2}]: raise ValueError('Paired iPhone disconnected')
        pic=vision.image(self.jpeg)
        if abs(pic.width/pic.height-self.calibration['aspect'])>.03: raise ValueError('Screen orientation changed; prepare the pointer again')
        c=self.calibration
        ux,uy=round((x-c['offset_x'])*c['width']),round((y-c['offset_y'])*c['height'])
        self.home(); self.move(ux,uy)
        self.wait_pointer(self.motion_at,expected=(x,y),board_required=False)

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
                        peer=self.peer, calibrationReady=bool(self.calibration),captureActive=self.is_fresh(),
                        sharing_active=self.is_fresh(),
                        bluetooth_status='Bluetooth connected' if connected else "Bluetooth not connected — control won't work",
                        screen_share_status='Screen sharing live' if self.is_fresh() else "Screen sharing off — control won't work"),message=self.label)
        self.sending_generation=self.generation
        try:
            self.fresh()
            self.pairing.associate(self)
            if operation in ('prepare_session','pointer_quick_probe'): self.calibration=None
            if operation in ('ensure_session','prepare_session','pointer_quick_probe'): self.ready()
            elif operation=='set_page_board':
                self.open_board(bool(args.get('aim')))
                if args.get('aim'): self.wait_board()
            elif operation=='pointer_home': self.home()
            elif operation=='move_pointer':
                dx,dy=args.get('dx',0),args.get('dy',0)
                if type(dx) is not int or type(dy) is not int or max(abs(dx),abs(dy))>127: raise ValueError('Relative deltas must be -127…127')
                self.move(dx,dy)
                self.wait_pointer(self.motion_at,board_required=False)
            elif operation=='click': self.click(args.get('button','left'))
            elif operation in ('pointer_move','pointer_tap'):
                self.absolute(args['x'],args['y'])
                if operation=='pointer_tap': self.click(gated=True,after=self.motion_at,expected=(args['x'],args['y']),board_required=False)
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
                    part=max(-127,min(127,amount)); self.packet(2,mouse(wheel=-part)); amount-=part; time.sleep(.008)
                self.packet(2,mouse())
            elif operation in ('swipe','advance_feed'):
                direction=args.get('profile','up') if operation=='swipe' else ('up' if args.get('dy',1)==1 else 'down')
                if direction not in ('up','down','left','right'): raise ValueError('Swipe direction must be up/down/left/right')
                if operation=='advance_feed' and args.get('dy',1) not in (-1,1): raise ValueError('Feed dy must be 1 or -1')
                start={'up':(.5,.7),'down':(.5,.3),'left':(.75,.5),'right':(.25,.5)}[direction]
                end={'up':(.5,.3),'down':(.5,.7),'left':(.25,.5),'right':(.75,.5)}[direction]
                self.absolute(*start)
                self.packet(2,mouse(buttons=1)); time.sleep(.05)
                self.move(round((end[0]-start[0])*self.calibration['width']),round((end[1]-start[1])*self.calibration['height']),buttons=1)
                self.packet(2,mouse())
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
            return dict(ok=True,message='Input/preparation completed. Inspect the returned screen to verify the UI outcome.',commandID=str(uuid.uuid4()),
                        frame=dict(frameID=frame_id,captureActive=self.is_fresh(),ageMilliseconds=int((time.monotonic()-frame_at)*1000),
                                   mimeType='image/jpeg',base64=base64.b64encode(jpeg).decode()))
        except (ValueError,OSError) as error:
            self.error=str(error)
            self.open_board(False)
            if self.peer: self.radio.release(self.peer)
            return dict(ok=False,message=str(error))
