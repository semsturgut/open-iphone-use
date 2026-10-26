import asyncio
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw

from oiu.control import Control,Pairing
from oiu.hid import mouse,keyboard,chord,stroke,REPORT_MAP
from oiu.bootstrap import certificates
from oiu.registrations import configure
from oiu import vision


def screen(x=150,y=250,show_board=True):
    pic=Image.new('RGB',(600,1000),'white' if show_board else 'black')
    draw=ImageDraw.Draw(pic)
    if show_board: draw.rectangle((0,50,599,950),outline=(0,224,122),width=4)
    if x>12 and y>62 and y<938: draw.ellipse((x-10,y-10,x+10,y+10),fill=(140,140,140))
    output=BytesIO(); pic.save(output,format='JPEG',quality=95)
    return output.getvalue()


class Radio:
    def __init__(self,peers=('phone-a',)):
        self.positions={peer:[0,0] for peer in peers}
        self.engines={}
        self.calls=[]
    def peers(self): return [dict(id=peer,reports=[1,2]) for peer in self.positions]
    def send(self,peer,report,payload):
        self.calls.append((peer,report,payload))
        if peer not in self.positions: raise OSError('Disconnected')
        if report==2:
            signed=lambda v:v if v<128 else v-256
            p=self.positions[peer]
            p[0]=min(599,max(0,p[0]+signed(payload[1])))
            p[1]=min(999,max(0,p[1]+signed(payload[2])))
            if payload[0]&1 and peer in self.engines and self.engines[peer].board_open:
                engine=self.engines[peer]
                rect=vision.board(engine.jpeg)[1]
                engine.request('capture_tap',x=(p[0]/600-rect[0])/rect[2],y=(p[1]/1000-rect[1])/rect[3])
    def release(self,peer):
        self.calls.extend([(peer,1,keyboard()),(peer,2,mouse())])


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.radio=Radio()
        self.pairing=Pairing(self.radio)
        self.engine=Control(self.pairing,Path(self.tmp.name)/'a',peer='phone-a')
        self.radio.engines['phone-a']=self.engine
        self.frame_delay=0
        self.running=True
        def frames():
            from collections import defaultdict,deque
            positions=defaultdict(lambda:deque(maxlen=80))
            while self.running:
                for peer,engine in list(self.radio.engines.items()):
                    now=time.monotonic()
                    positions[peer].append((now,*self.radio.positions[peer]))
                    eligible=[entry for entry in positions[peer] if entry[0]<=now-self.frame_delay]
                    _,x,y=eligible[-1] if eligible else positions[peer][0]
                    try: engine.ingest(screen(x,y,engine.board_open))
                    except ValueError:
                        if not engine.closed: raise
                time.sleep(.04)
        self.thread=threading.Thread(target=frames,daemon=True); self.thread.start()
        deadline=time.monotonic()+2
        while not self.engine.at and time.monotonic()<deadline: time.sleep(.01)
    def tearDown(self):
        self.running=False; self.thread.join(2); self.tmp.cleanup()
    def test_real_image_calibration_automatic_binding_and_persistence(self):
        result=self.engine.request('ensure_session')
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.engine.peer,'phone-a')
        self.assertAlmostEqual(self.engine.calibration['width'],600,delta=5)
        self.assertAlmostEqual(self.engine.calibration['height'],1000,delta=5)
        self.assertEqual(len(self.engine.calibration['samples']),7)
        self.assertFalse(self.engine.board_open)
        saved=json.loads(self.engine.path.read_text())
        self.assertEqual(saved['peer'],'phone-a')
        result=self.engine.request('pointer_tap',x=.5,y=.5)
        self.assertTrue(result['ok'],result)
        self.assertAlmostEqual(self.radio.positions['phone-a'][0],300,delta=5)
    def test_calibration_tolerates_eight_hundred_ms_pointer_frame_lag(self):
        self.frame_delay=.8
        result=self.engine.request('ensure_session')
        self.assertTrue(result['ok'],result)
        self.assertAlmostEqual(self.engine.calibration['width'],600,delta=5)
        self.assertAlmostEqual(self.engine.calibration['height'],1000,delta=5)
        self.assertEqual(self.engine.peer,'phone-a')

    def test_saved_calibration_is_reused_by_a_new_control_without_preparation(self):
        self.assertTrue(self.engine.request('ensure_session')['ok'])
        restored=Control(self.pairing,Path(self.tmp.name)/'a',peer='phone-a')
        self.assertTrue(restored.calibration_compatible())
        self.assertEqual(restored.label,'Pointer ready')
        self.radio.engines['phone-a']=restored
        deadline=time.monotonic()+2
        while not restored.at and time.monotonic()<deadline: time.sleep(.01)
        try:
            probe=restored.request('pointer_quick_probe')
            tap=restored.request('pointer_tap',x=.5,y=.5)
        finally: self.radio.engines['phone-a']=self.engine
        self.assertTrue(probe['ok'],probe)
        self.assertIn('Pointer ready',probe['message'])
        self.assertTrue(tap['ok'],tap)
        self.assertIn('pointer position visually verified',tap['message'])
        self.assertAlmostEqual(self.radio.positions['phone-a'][0],300,delta=5)

    def test_pointer_gate_waits_for_two_stable_distinct_frames_before_clicking(self):
        self.running=False; self.thread.join(2)
        self.engine.ingest(screen(0,0))
        started=time.monotonic()
        def delayed_frames():
            for point in [(0,0)]*7+[(180,300),(190,310),(190,310),(190,310)]:
                time.sleep(.1); self.engine.ingest(screen(*point))
        writer=threading.Thread(target=delayed_frames); writer.start()
        try:
            point,_,_=self.engine.click(gated=True,after=started)
        finally: writer.join(3)
        self.assertGreater(time.monotonic()-started,.8)
        self.assertAlmostEqual(point[0],190/600,delta=.005)
        self.assertEqual(sum(r==2 and p[0]==1 for _,r,p in self.radio.calls),1)

    def test_one_pointer_frame_is_not_enough_and_timeout_sends_no_click(self):
        self.running=False; self.thread.join(2)
        started=time.monotonic()
        self.engine.ingest(screen())
        with self.assertRaises(ValueError): self.engine.wait_pointer(started,timeout=.15)
        self.assertFalse(any(any(payload) for _,_,payload in self.radio.calls))

    def test_tap_feedback_ignores_late_mismatch_until_matching_feedback_arrives(self):
        pressed=time.monotonic()
        self.engine.request('capture_tap',x=.9,y=.9)
        timer=threading.Timer(.3,lambda:self.engine.request('capture_tap',x=.25,y=.5)); timer.start()
        try: actual=self.engine.wait_tap(pressed,(.25,.5),(0,0,1,1))
        finally: timer.join(2)
        self.assertEqual(actual,(.25,.5))

    def test_home_and_board_route_only_to_the_bound_subscribed_peer(self):
        result=self.engine.request('pointer_home')
        self.assertTrue(result['ok'],result)
        self.assertEqual(sum(any(p[1:]) for _,r,p in self.radio.calls if r==2),40)
        self.assertTrue(all(peer=='phone-a' for peer,_,_ in self.radio.calls))
        result=self.engine.request('set_page_board',aim=True)
        self.assertTrue(result['ok'],result)
        vision.board(self.engine.jpeg)
        self.radio.positions.clear()
        self.assertFalse(self.engine.request('pointer_home')['ok'])
        self.assertFalse(self.engine.request('set_page_board',aim=True)['ok'])

    def test_two_phone_association_and_targeted_keyboard(self):
        self.radio.positions['phone-b']=[0,0]
        other=Control(self.pairing,Path(self.tmp.name)/'b',peer='phone-b')
        self.radio.engines['phone-b']=other
        deadline=time.monotonic()+2
        while not other.at and time.monotonic()<deadline: time.sleep(.01)
        self.assertTrue(self.engine.request('ensure_session')['ok'])
        self.assertEqual(self.engine.peer,'phone-a')
        self.assertEqual(other.peer,'phone-b')
        self.radio.calls.clear()
        result=self.engine.request('type_text',text='Hi!')
        self.assertTrue(result['ok'],result)
        self.assertTrue(self.radio.calls)
        self.assertTrue(all(call[0]=='phone-a' for call in self.radio.calls))
    def test_two_phone_preparation_is_parallel_and_never_guesses_a_peer(self):
        self.radio.positions['phone-b'] = [0, 0]
        other = Control(self.pairing, Path(self.tmp.name)/'b', peer='phone-b')
        self.radio.engines['phone-b'] = other
        entered, resume = threading.Event(), threading.Event()
        original = self.radio.send
        def delayed(peer, report, payload):
            if peer == 'phone-a' and not entered.is_set():
                entered.set()
                if not resume.wait(30): raise OSError('Test timed out')
            original(peer, report, payload)
        self.radio.send = delayed
        result = []
        first = threading.Thread(target=lambda: result.append(self.engine.request('ensure_session')))
        first.start()
        try:
            self.assertTrue(entered.wait(3))
            second = other.request('ensure_session')
            self.assertTrue(second['ok'], second)
            self.assertTrue(first.is_alive(), 'Phone B must complete while phone A is blocked')
        finally:
            resume.set(); first.join(20)
        self.assertTrue(result[0]['ok'], result)
        self.assertEqual(self.engine.peer, 'phone-a')
        self.assertEqual(other.peer, 'phone-b')
        self.assertIsNot(self.engine.calibration, other.calibration)
        with self.assertRaises(ValueError): self.engine.bind('phone-b')
        del self.radio.positions['phone-a']
        self.assertFalse(self.engine.request('ensure_session')['ok'])
        self.assertEqual(self.engine.peer, 'phone-a')

    def test_remove_detaches_control_and_erases_only_its_calibration(self):
        self.engine.bind('phone-a')
        other = Control(self.pairing, Path(self.tmp.name)/'b',peer='phone-b')
        other.bind('phone-b')
        self.engine.remove()
        self.assertTrue(self.engine.closed)
        self.assertNotIn(self.engine, self.pairing.engines)
        self.assertIn(other, self.pairing.engines)
        self.assertFalse(self.engine.path.exists())
        self.assertTrue(other.path.exists())
        self.assertIsNone(self.engine.peer)
        self.assertIsNone(self.engine.calibration)
        self.assertEqual((self.engine.jpeg, self.engine.frame_id, len(self.engine.taps)), (b'', None, 0))
        self.assertFalse(self.engine.board_open)
        with self.assertRaises(ValueError): self.engine.ingest(screen())
        with self.assertRaises(ValueError): self.engine.request('capture_pointer', epoch='old')
        with self.assertRaises(ValueError): self.engine.request('capture_tap', x=.5, y=.5)
        with self.assertRaises(ValueError): self.engine.save()
        with self.assertRaises(ValueError): self.pairing.associate(self.engine)
        self.assertTrue(all(peer == 'phone-a' for peer, _, _ in self.radio.calls))

    def test_remove_releases_after_an_inflight_report_and_prevents_new_input(self):
        self.engine.bind('phone-a')
        entered, resume = threading.Event(), threading.Event()
        original = self.radio.send
        def delayed(peer, report, payload):
            entered.set()
            if not resume.wait(3): raise OSError('Test send timed out')
            original(peer, report, payload)
        self.radio.send = delayed
        result = []
        action = threading.Thread(target=lambda: result.append(self.engine.request('type_text', text='abc')))
        action.start()
        self.assertTrue(entered.wait(2))
        removal = threading.Thread(target=self.engine.remove)
        removal.start()
        try:
            deadline = time.monotonic() + 2
            while not self.engine.closed and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(self.engine.closed)
        finally:
            resume.set(); action.join(3); removal.join(3)
        self.assertFalse(action.is_alive())
        self.assertFalse(removal.is_alive())
        self.assertFalse(result[0]['ok'])
        self.assertEqual(self.radio.calls[-2:], [('phone-a', 1, keyboard()), ('phone-a', 2, mouse())])
        with self.assertRaises(ValueError): self.engine.request('type_text', text='later')

    def test_unbound_records_cannot_probe_or_steal_another_phone(self):
        unbound = Control(self.pairing, Path(self.tmp.name)/'legacy')
        unbound.ingest(screen())
        self.assertFalse(unbound.request('ensure_session')['ok'])
        self.assertEqual(self.radio.calls, [])
        self.assertIsNone(unbound.peer)

    def test_stale_and_invalid_text_send_no_nonzero_reports(self):
        self.engine.bind('phone-a')
        self.assertFalse(self.engine.request('type_text',text='🙂')['ok'])
        self.assertFalse(any(any(payload) for _,_,payload in self.radio.calls))
        self.engine.at=time.monotonic()-6
        result=self.engine.request('send_key',key='cmd+h')
        self.assertFalse(result['ok'])
        self.assertFalse(any(any(payload) for _,_,payload in self.radio.calls))
    def test_stop_interrupts_typing_and_releases_only_this_peer(self):
        self.engine.bind('phone-a')
        result=[]
        thread=threading.Thread(target=lambda:result.append(self.engine.request('type_text',text='a'*100)))
        thread.start(); time.sleep(.08)
        self.engine.stop_capture(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0]['ok'])
        self.assertLess(len(self.radio.calls),100)
        self.assertTrue(all(peer=='phone-a' for peer,_,_ in self.radio.calls))


class ProvisioningTests(unittest.TestCase):
    def test_ca_reuse_and_server_renewal_for_changed_address(self):
        from cryptography import x509
        with tempfile.TemporaryDirectory() as directory:
            cert,key,root,der=certificates(directory,'192.168.1.2')
            first=Path(cert).read_bytes(); ca=Path(root).read_bytes()
            certificates(directory,'192.168.1.2')
            self.assertEqual(first,Path(cert).read_bytes())
            certificates(directory,'192.168.1.3')
            self.assertNotEqual(first,Path(cert).read_bytes())
            self.assertEqual(ca,Path(root).read_bytes())
            parsed=x509.load_pem_x509_certificate(Path(cert).read_bytes())
            self.assertIn('192.168.1.3',str(parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value))
            self.assertEqual(Path(key).stat().st_mode&0o777,0o600)
    def test_registration_rerun_preserves_other_entries_and_uninstall_restores(self):
        with tempfile.TemporaryDirectory() as directory,patch('oiu.registrations.shutil.which',return_value=None):
            home=Path(directory); (home/'.codex').mkdir(); (home/'.cursor').mkdir(); (home/'.claude').mkdir()
            original='# keep this comment\n[mcp_servers.other]\ncommand="other-server"\n'
            (home/'.codex/config.toml').write_text(original)
            (home/'.claude.json').write_text(json.dumps({'mcpServers':{'other':{'command':'keep'}}}))
            skill=home/'source-skill.md'; skill.write_text('---\nname: open-iphone-use\ndescription: test\n---\nTest')
            configure(home,Path('/test/cli'),skill)
            first=(home/'.codex/config.toml').read_text()
            configure(home,Path('/test/cli'),skill)
            self.assertEqual(first,(home/'.codex/config.toml').read_text())
            claude=json.loads((home/'.claude.json').read_text()); claude['unrelated']='new'
            (home/'.claude.json').write_text(json.dumps(claude))
            configure(home,Path('/test/cli'),uninstall=True)
            self.assertIn('# keep this comment',(home/'.codex/config.toml').read_text())
            self.assertNotIn('open-iphone-use',(home/'.codex/config.toml').read_text())
            self.assertEqual(json.loads((home/'.claude.json').read_text())['unrelated'],'new')
            self.assertFalse((home/'.agents/skills/open-iphone-use/SKILL.md').exists())
    def test_modified_registration_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory,patch('oiu.registrations.shutil.which',return_value=None):
            home=Path(directory); (home/'.cursor').mkdir()
            skill=home/'source';skill.write_text('test')
            configure(home,Path('/test/cli'),skill)
            path=home/'.cursor/mcp.json'; value=json.loads(path.read_text())
            value['mcpServers']['open-iphone-use']['command']='user-edit';path.write_text(json.dumps(value))
            configure(home,Path('/test/cli'),uninstall=True)
            self.assertEqual(json.loads(path.read_text())['mcpServers']['open-iphone-use']['command'],'user-edit')


class EncoderTests(unittest.TestCase):
    def test_native_descriptor_parity_and_ascii_prevalidation(self):
        source=(Path(__file__).resolve().parents[2]/'macos/Sources/OpenIPhoneUseCore/BLEHIDPeripheral.swift').read_text()
        array=source.split('public static let reportDescriptor = Data([')[1].split('])')[0]
        array=re.sub(r'//[^\n]*','',array)
        self.assertEqual(REPORT_MAP,bytes(int(token,16) for token in re.findall(r'0x([0-9a-fA-F]+)',array)))
        self.assertEqual(chord('cmd+h'),bytes([8,0,11,0,0,0,0,0]))
        self.assertEqual(chord('return'),chord('enter'))
        self.assertEqual(chord('Return'),chord('enter'))
        self.assertEqual(chord('esC'),chord('escape'))
        self.assertEqual(chord('cmd+Return'),bytes([8,0,0x28,0,0,0,0,0]))
        self.assertEqual(chord('ret'),chord('enter'))
        self.assertEqual(chord('pgdn'),chord('pagedown'))
        self.assertEqual(chord('arrowup'),chord('up'))
        self.assertEqual(chord('home'),bytes([0,0,0x4a,0,0,0,0,0]))
        with self.assertRaisesRegex(ValueError,'Accepted'):chord('banana')
        self.assertEqual(stroke('!'),(2,0x1e))
        self.assertEqual(mouse(-127,127),bytes([0,129,127,0]))
        with self.assertRaises(ValueError): mouse(128)
        with self.assertRaises(ValueError): stroke('🙂')
    def test_visual_gate_rejects_non_board(self):
        with self.assertRaises(ValueError): vision.pointer(screen(show_board=False))
        point,_=vision.pointer(screen())
        self.assertAlmostEqual(point[0],.25,delta=.01)
