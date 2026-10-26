"""Portable per-device HID lifecycle, safe board calibration and automatic peer binding."""
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
        with self.lock:
            peers = [p['id'] for p in self.radio.peers() if set(p['reports']) == {1,2}]
            if engine.peer in peers: return engine.peer
            if engine.peer:
                # Re-pairing may change the OS peer identity. Re-associate from live boards,
                # never require the owner to find/copy an internal UUID.
                engine.peer=None; engine.calibration=None
            claimed = {e.peer for e in self.engines if e.peer and e.is_fresh()}
            candidates = [p for p in peers if p not in claimed]
            if not candidates: raise ValueError('Pair this host as Open iPhone Use in Settings > Accessibility > Touch > AssistiveTouch > Devices > Bluetooth Devices; allow the host Bluetooth permission prompt')
            active = [e for e in self.engines if not e.peer and e.is_fresh()]
            if len(candidates) == 1 and len(active) == 1:
                engine.bind(candidates[0]); return engine.peer
            if len(active) != len(candidates):
                raise ValueError('Keep every newly paired phone sharing its Safari page for automatic association, or pair/share new phones one at a time')
            # All candidate screens must show our board before any association movement.
            epoch = uuid.uuid4().hex
            for e in active:
                e.sending_generation=e.generation
                e.open_board(True, pair_epoch=epoch)
            for e in active: e.wait_board()
            for peer in candidates:
                if peer in {e.peer for e in self.engines if e.peer and e.is_fresh()}: continue
                pending = [e for e in active if not e.peer]
                before = {}
                for e in pending:
                    try: before[e] = vision.pointer(e.jpeg)[0]
                    except ValueError: before[e] = None
                    e.hover = None
                started = time.monotonic()
                try:
                    for _ in range(8): self.radio.send(peer, 2, mouse(dx=-127))
                    for _ in range(8): self.radio.send(peer, 2, mouse(dy=-127))
                    for _ in range(8): self.radio.send(peer, 2, mouse(dx=8,dy=16)); time.sleep(.008)
                    deadline = time.monotonic()+5
                    matched = None
                    while time.monotonic() < deadline:
                        matches = [e for e in pending if e.hover and e.hover[0] >= started and e.hover[1] == epoch]
                        if not matches:
                            for e in pending:
                                if e.at < started: continue
                                try: point = vision.pointer(e.jpeg)[0]
                                except ValueError: continue
                                old = before[e]
                                if old is None or math.dist(point,old) > .02: matches.append(e)
                        if len(matches) == 1: matched=matches[0]; break
                        time.sleep(.1)
                    if matched is None: raise ValueError('Automatic pairing was ambiguous. Keep the standard grey pointer and all new Safari sharing boards visible; pairing one new phone at a time avoids ambiguity')
                    matched.bind(peer)
                finally: self.radio.release(peer)
            if not engine.peer: raise ValueError('No matching Bluetooth peer found for this capture')
            for e in active:
                if e is not engine: e.open_board(False)
            return engine.peer


class Control:
    def __init__(self, pairing, directory):
        self.pairing, self.radio = pairing, pairing.radio
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = self.directory/'calibration.json'
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.peer, self.calibration = saved.get('peer'), saved.get('calibration')
        self.condition = threading.Condition()
        self.jpeg, self.at, self.frame_id = b'', 0, None
        self.board_open, self.board_epoch, self.pair_epoch = False, 1, ''
        self.taps = deque(maxlen=64)
        self.hover = None
        self.generation, self.sending_generation = 0, 0
        self.units = (0,0)
        self.error = None
        pairing.engines.append(self)

    @property
    def label(self):
        if self.error: return self.error
        health=getattr(self.radio,'startup',{})
        if health.get('error'): return 'Bluetooth: '+health['state']
        return 'Pointer ready' if self.calibration and self.peer else ('Paired; prepare pointer' if self.peer else 'Pair iPhone, then Prepare')

    def save(self): atomic_json(self.path, dict(peer=self.peer, calibration=self.calibration))
    def bind(self, peer):
        for other in self.pairing.engines:
            if other is not self and other.peer==peer:
                if other.is_fresh(): raise ValueError('This phone is already associated with another active screen share')
                other.peer=None; other.calibration=None; other.save()
        self.peer=peer; self.calibration=None; self.save()
    def is_fresh(self): return bool(self.at and time.monotonic()-self.at <= 5)

    def ingest(self, jpeg):
        with self.condition:
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
        self.fresh()
        if not self.peer: raise ValueError('No paired iPhone associated with this screen')
        self.radio.send(self.peer, report, payload)

    def wait_frame(self, after, timeout=5):
        deadline=time.monotonic()+timeout
        with self.condition:
            while self.at <= after:
                self.fresh()
                remaining=deadline-time.monotonic()
                if remaining <= 0: raise ValueError('No fresh screen arrived after input')
                self.condition.wait(min(.2,remaining))
        return self.jpeg

    def wait_board(self):
        started=time.monotonic()
        deadline=started+12
        while time.monotonic()<deadline:
            if self.generation != self.sending_generation: raise ValueError('Preparation canceled')
            if self.at>started:
                try: return vision.board(self.jpeg)[1]
                except ValueError: pass
            time.sleep(.1)
        raise ValueError('Keep Safari sharing visible and dismiss overlays so the green-bordered calibration board can be measured')

    def home(self):
        self.packet(2,mouse())
        for _ in range(8): self.packet(2,mouse(dx=-127)); time.sleep(.008)
        for _ in range(8): self.packet(2,mouse(dy=-127)); time.sleep(.008)
        self.units=(0,0)

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

    def click(self, button='left', gated=False):
        if button not in ('left','right','middle'): raise ValueError('Unknown pointer button')
        if gated:
            # A live board and a single unambiguous pointer are mandatory before calibration presses.
            self.fresh(); vision.pointer(self.jpeg)
        mask={'left':1,'right':2,'middle':4}[button]
        self.packet(2,mouse(buttons=mask))
        time.sleep(.035)
        self.packet(2,mouse())

    def calibrate(self):
        self.open_board(True)
        try:
            rect=self.wait_board()
            initial=vision.image(self.jpeg)
            initial_aspect=initial.width/initial.height
            self.home()
            samples=[]
            for ux,uy in ((64,128),(192,384)):
                self.move(ux-self.units[0],uy-self.units[1])
                moved=time.monotonic()
                self.wait_frame(moved)
                point,rect=vision.pointer(self.jpeg)
                current=vision.image(self.jpeg)
                if abs(current.width/current.height-initial_aspect)>.03: raise ValueError('Orientation changed during calibration; keep the phone still and prepare again')
                pressed=time.monotonic()
                self.click(gated=True)
                deadline=time.monotonic()+4
                tap=None
                with self.condition:
                    while tap is None and time.monotonic()<deadline:
                        self.fresh()
                        tap=next((t for t in self.taps if t[0]>=pressed),None)
                        if tap is None: self.condition.wait(.1)
                if tap is None: raise ValueError('Safari did not report the calibration click; no coordinate mapping was saved')
                x,y=rect[0]+tap[1]*rect[2],rect[1]+tap[2]*rect[3]
                if math.dist((x,y),point)>.06: raise ValueError('Calibration feedback does not match the visible pointer')
                samples.append((ux,uy,x,y))
            width,height=((samples[1][axis]-samples[0][axis])/(samples[1][axis+2]-samples[0][axis+2]) if samples[1][axis+2]-samples[0][axis+2]>.06 else 0 for axis in (0,1))
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
        if not self.calibration: raise ValueError('Run Prepare/ensure_session with Safari sharing visible before coordinate input')
        if self.peer not in [p['id'] for p in self.radio.peers() if set(p['reports'])=={1,2}]: raise ValueError('Paired iPhone disconnected')
        pic=vision.image(self.jpeg)
        if abs(pic.width/pic.height-self.calibration['aspect'])>.03: raise ValueError('Screen orientation changed; prepare the pointer again')
        c=self.calibration
        ux,uy=round((x-c['offset_x'])*c['width']),round((y-c['offset_y'])*c['height'])
        self.home(); self.move(ux,uy)

    def stop_capture(self):
        self.generation+=1
        self.at=0
        with self.condition: self.condition.notify_all()
        if self.peer: self.radio.release(self.peer)
        return dict(ok=True,message='Input canceled and released for this phone')

    def close(self):
        if self.peer: self.radio.release(self.peer)

    def request(self,operation,**args):
        if operation=='capture_tap':
            with self.condition:
                self.taps.append((time.monotonic(),fraction(args['x']),fraction(args['y'])))
                self.condition.notify_all()
            return dict(ok=True,message='Calibration feedback received')
        if operation=='capture_pointer':
            if not isinstance(args.get('epoch'),str) or len(args['epoch'])>64: raise ValueError('Invalid pairing epoch')
            self.hover=(time.monotonic(),args.get('epoch'))
            return dict(ok=True)
        if operation=='stop_control': return self.stop_capture()
        if operation=='get_device_status':
            peers=self.radio.peers()
            return dict(ok=True,status=dict(connected=self.peer in [p['id'] for p in peers],peer=self.peer,
                        calibrationReady=bool(self.calibration),captureActive=self.is_fresh(),peers=peers),message=self.label)
        self.sending_generation=self.generation
        try:
            self.fresh()
            if operation in ('prepare_session','pointer_quick_probe'): self.calibration=None
            if operation in ('ensure_session','prepare_session','pointer_quick_probe'): self.ready()
            elif operation=='set_page_board': self.open_board(bool(args.get('aim')))
            elif operation=='pointer_home': self.home()
            elif operation=='move_pointer':
                dx,dy=args.get('dx',0),args.get('dy',0)
                if type(dx) is not int or type(dy) is not int or max(abs(dx),abs(dy))>127: raise ValueError('Relative deltas must be -127…127')
                self.move(dx,dy)
            elif operation=='click': self.click(args.get('button','left'))
            elif operation in ('pointer_move','pointer_tap'):
                self.absolute(args['x'],args['y'])
                if operation=='pointer_tap': self.click()
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
