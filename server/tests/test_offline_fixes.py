"""Offline regression tests: synthetic frames and fake radios only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import time

from oiu import calibration, vision
from oiu.backends import MacRadio, MacBackend
from oiu.control import Control, ObservationUnavailable, Pairing
from oiu.core import Registry
import test_control as fixtures
from test_control import Radio, screen


class RegistryAndRadioTests(TestCase):
    def test_existing_identity_is_reused_and_reconnect_keeps_binding(self):
        class FakeRadio:
            def __init__(self): self.connected = True
            def peers(self):
                return [dict(id='peer', reports=[1, 2], name='Phone')] if self.connected else []
        with TemporaryDirectory() as directory:
            registry = Registry(directory); radio = FakeRadio(); registry.radio = radio
            original = registry.create_bluetooth('peer')
            calibration_path = Path(directory)/original.id/'calibration.json'
            calibration_path.parent.mkdir(); calibration_path.write_text('{"marker":"keep"}')
            radio.connected = False
            self.assertIs(registry.create_bluetooth('peer'), original)
            self.assertFalse(registry.list()[0]['bluetooth_connected'])
            radio.connected = True
            self.assertTrue(registry.list()[0]['bluetooth_connected'])
            self.assertEqual(calibration_path.read_text(), '{"marker":"keep"}')
            restored = Registry(directory); restored.radio = radio
            self.assertEqual(restored.create_bluetooth('peer').id, original.id)
            self.assertEqual(len(restored.devices), 1)

    def test_old_helper_is_allowed_to_exit_before_new_one_starts(self):
        with TemporaryDirectory() as directory:
            bridge=MacBackend.__new__(MacBackend); bridge.path=str(Path(directory)/'bridge.sock')
            Path(bridge.path).touch()
            replies=[{'ok':True,'message':'123'}, ConnectionRefusedError()]
            with patch.object(bridge,'request',side_effect=replies) as request, patch('oiu.backends.time.sleep'):
                bridge.wait_for_previous_helper()
            self.assertEqual(request.call_count,2)
            with patch.object(bridge,'request',side_effect=TimeoutError('hung')):
                with self.assertRaisesRegex(RuntimeError,'refusing to overlap'):
                    bridge.wait_for_previous_helper()

    def test_radio_waits_through_corebluetooth_initialization(self):
        states = iter([{'state':'waitingForBluetooth'}, {'state':'bluetoothOff'}, {'state':'resetting'},
                       {'state':'publishing'}, {'state':'advertising','peers':[]}])
        class Bridge:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs):
                import json
                return {'ok':True, 'message':json.dumps(next(states))}
        with patch('oiu.backends.MacBackend', Bridge), patch('oiu.backends.time.sleep'):
            radio = MacRadio('fake', '/tmp/unused-fake')
        self.assertEqual(radio.startup['state'], 'advertising')


class PointerTests(TestCase):
    def engine(self, directory):
        engine = Control(Pairing(Radio()), Path(directory), peer='phone-a')
        engine.ingest(screen())
        engine.calibration = dict(width=600, height=1000, offset_x=0, offset_y=0,
            aspect=.6, version=calibration.VERSION, motion=calibration.MOTION,
            fit_rms=[.001,.001], frame_size=[600,1000])
        return engine

    def test_tap_feedback_logs_geometry_and_event_minus_pointer(self):
        with TemporaryDirectory() as directory:
            engine=self.engine(directory); pressed=time.monotonic()
            geometry={'board':{'left':20,'top':80,'width':320,'height':640,'untrusted':'ignore'},
                      'client':{'x':100,'y':400},
                      'visualViewport':{'offsetLeft':3,'offsetTop':11,'width':320,'height':640,'scale':1.5}}
            engine.request('capture_tap',x=.25,y=.5,geometry=geometry)
            actual=engine.wait_tap(pressed,(.25,.48),(0,0,1,1))
            self.assertEqual(actual,(.25,.5))
            self.assertAlmostEqual(engine.last_tap_diagnostic['event_minus_pointer'][1],.02)
            self.assertEqual(engine.last_tap_diagnostic['dom']['visualViewport']['scale'],1.5)
            self.assertNotIn('untrusted',engine.last_tap_diagnostic['dom']['board'])

    def test_calibration_fits_pointer_not_shifted_event(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory)
            fixture = fixtures.ControlTests(); fixture.engine = engine
            fixture.calibration_fakes()
            with patch.object(engine, 'wait_tap', side_effect=lambda pressed, point, rect: (point[0],point[1]-.024)):
                engine.calibrate()
            self.assertAlmostEqual(engine.calibration['offset_y'], 0, places=5)
            self.assertAlmostEqual(engine.calibration['event_pointer_delta']['mean'][1], -.024, places=5)
            fixture.doCleanups()

    def test_unseen_untrusted_does_not_click_and_trusted_reports_blind(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory)
            with patch.object(engine,'capture_after',return_value=(time.monotonic(),engine.jpeg)), \
                 patch.object(engine,'wait_pointer',side_effect=ObservationUnavailable('hidden')), \
                 patch.object(engine,'wait_frame'), patch('oiu.control.time.sleep'):
                engine.calibration['suspect'] = True
                rejected = engine.request('pointer_tap', x=.5, y=.5)
                self.assertFalse(rejected['ok']); self.assertFalse(rejected['click_sent'])
                self.assertFalse(any(packet[2][0] == 1 for packet in engine.radio.calls if packet[1] == 2))
                engine.calibration['suspect'] = False
                result = engine.request('pointer_tap', x=.5, y=.5)
                self.assertTrue(result['ok']); self.assertTrue(result['click_sent'])
                self.assertFalse(result['verified']); self.assertIn('hidden',result['observation_reason'])
                self.assertEqual((result['frame']['width'], result['frame']['height']), (600,1000))

    def test_repeated_signed_bias_marks_suspect(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory)
            with patch.object(engine,'capture_after',return_value=(time.monotonic(),engine.jpeg)), \
                 patch.object(engine,'wait_pointer',side_effect=[((.5,.52),(0,0,1,1),1),((.5,.5),(0,0,1,1),2)]*3), \
                 patch('oiu.control.time.sleep'):
                for _ in range(3): engine.absolute(.5,.5)
            self.assertTrue(engine.calibration['suspect'])

    def test_hidden_pointer_is_woken_while_waiting_for_slow_frames(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory)
            now = time.monotonic()
            frames = [ObservationUnavailable('slow'), (now+.1, engine.jpeg), (now+.3, engine.jpeg)]
            with patch.object(engine, 'capture_after', side_effect=frames), \
                 patch.object(engine, 'wake_pointer') as wake, \
                 patch('oiu.vision.pointer', return_value=((.75,.5),(0,0,1,1))):
                point, _, _ = engine.wait_pointer(now, expected=(.75,.5), board_required=False)
            self.assertEqual(point,(.75,.5))
            wake.assert_called_once_with(-1)
            self.assertEqual(engine.observation_frames,2)

    def test_fading_pointer_is_woken_when_frames_arrive_without_detection(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory); now=time.monotonic(); engine.motion_at=now-1
            frames=[(now+.1,engine.jpeg),(now+.3,engine.jpeg),(now+.5,engine.jpeg)]
            detections=[ValueError('hidden'),((.25,.5),(0,0,1,1)),((.25,.5),(0,0,1,1))]
            with patch.object(engine,'capture_after',side_effect=frames), \
                 patch.object(engine,'wake_pointer') as wake, \
                 patch('oiu.vision.pointer',side_effect=detections):
                point,_,_=engine.wait_pointer(now,expected=(.25,.5),board_required=False)
            self.assertEqual(point,(.25,.5)); wake.assert_called_once_with(1)

    def test_wake_moves_net_zero_and_slow_cadence_has_own_budget(self):
        with TemporaryDirectory() as directory:
            engine = self.engine(directory); moves=[]
            with patch.object(engine, 'move', side_effect=lambda x,y:moves.append((x,y))): engine.wake_pointer()
            self.assertEqual(moves,[(1,0),(-1,0)])
            engine.frame_intervals.extend([.033]*10+[1.])
            self.assertGreaterEqual(engine.observation_budget(),2.5)
            self.assertEqual(vision.dimensions(screen()), (600,1000))

class ServiceTests(TestCase):
    def test_stop_waits_for_previous_owner_lease_after_listener_closes(self):
        from types import SimpleNamespace
        from oiu import service
        calls=[]
        def call(args, path, data=None):
            calls.append(path)
            if path == '/api/shutdown': return {'ok': True}
            if len(calls) == 1: return {'instance_id': 'old'}
            raise OSError('listener stopped')
        class Lease:
            attempts = 0
            def __init__(self, path):
                Lease.attempts += 1
                if Lease.attempts < 3: raise ValueError('still held')
            def close(self): calls.append('lease released')
        with patch.object(service, 'StateLease', Lease), patch.object(service.time, 'sleep'):
            self.assertTrue(service.stop(SimpleNamespace(state='/unused'), call)['ok'])
        self.assertEqual(Lease.attempts, 3)
        self.assertEqual(calls[-1], 'lease released')
