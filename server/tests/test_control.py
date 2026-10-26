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
        self.engine=Control(self.pairing,Path(self.tmp.name)/'a')
        self.radio.engines['phone-a']=self.engine
        self.running=True
        def frames():
            while self.running:
                for peer,engine in list(self.radio.engines.items()):
                    x,y=self.radio.positions[peer]
                    engine.ingest(screen(x,y,engine.board_open))
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
        self.assertFalse(self.engine.board_open)
        saved=json.loads(self.engine.path.read_text())
        self.assertEqual(saved['peer'],'phone-a')
        result=self.engine.request('pointer_tap',x=.5,y=.5)
        self.assertTrue(result['ok'],result)
        self.assertAlmostEqual(self.radio.positions['phone-a'][0],300,delta=5)
    def test_two_phone_association_and_targeted_keyboard(self):
        self.radio.positions['phone-b']=[0,0]
        other=Control(self.pairing,Path(self.tmp.name)/'b')
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
        self.assertEqual(stroke('!'),(2,0x1e))
        self.assertEqual(mouse(-127,127),bytes([0,129,127,0]))
        with self.assertRaises(ValueError): mouse(128)
        with self.assertRaises(ValueError): stroke('🙂')
    def test_visual_gate_rejects_non_board(self):
        with self.assertRaises(ValueError): vision.pointer(screen(show_board=False))
        point,_=vision.pointer(screen())
        self.assertAlmostEqual(point[0],.25,delta=.01)
