"""Offline pointer regressions using synthetic captures and fake HID only."""
import io
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw
from oiu import calibration, vision
from oiu.control import Control, ObservationUnavailable, Pairing
from test_control import Radio, screen


def jpeg_image(pic):
    out=io.BytesIO(); pic.save(out,format='JPEG',quality=95); return out.getvalue()


class LandingTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.engine=Control(Pairing(Radio()),Path(self.temp.name),peer='phone-a')
        self.engine.ingest(screen())
        self.engine.calibration=dict(width=600,height=1000,offset_x=0,offset_y=0,aspect=.6,
            version=calibration.VERSION,motion=calibration.MOTION,fit_rms=[.001,.001],frame_size=[600,1000])
        self.addCleanup(self.wait_debug)

    def wait_debug(self):
        if self.engine.debug_thread: self.engine.debug_thread.join(5)

    def action(self, points, x=.5, y=.5):
        e=self.engine
        with patch.object(e,'capture_after',return_value=(time.monotonic(),e.jpeg)), \
             patch.object(e,'wait_pointer',side_effect=points),patch.object(e,'wait_frame'),patch('oiu.control.time.sleep'):
            return e.request('pointer_tap',x=x,y=y)

    def test_offset_learning_applied_persisted_and_no_suspect(self):
        e=self.engine
        for _ in range(2):
            self.assertTrue(self.action([((.5,.525),(0,0,1,1),1),((.5,.5),(0,0,1,1),2)])['ok'])
        self.assertFalse(e.calibration.get('suspect',False))
        result=self.action([((.5,.5),(0,0,1,1),1)])
        self.assertEqual(result['units'],[300,475])
        self.assertAlmostEqual(result['landing_offset'][1],.025)
        saved=json.loads(e.path.read_text())['calibration']
        self.assertEqual(saved['landing_samples'],e.calibration['landing_samples'])
        self.assertFalse(saved.get('suspect',False))

    def test_offset_requires_agreement_caps_and_resets_for_frame_or_calibration(self):
        e=self.engine; c=e.calibration
        c['landing_frame_size']=[600,1000]; c['landing_samples']=[[.1,0],[-.1,0]]
        self.assertEqual(e.landing_offset(c),(0.,0.))
        c['landing_samples']=[[.08,.08],[.08,.08]]
        self.assertAlmostEqual(sum(v*v for v in e.landing_offset(c))**.5,.05)
        c['landing_frame_size']=[300,500]
        self.assertEqual(e.landing_offset(c),(0.,0.)); self.assertNotIn('landing_samples',c)
        e.calibration=dict(c); self.assertEqual(e.landing_offset(e.calibration),(0.,0.))

    def test_first_landings_learn_consistent_samples_and_reject_outlier(self):
        e=self.engine
        for error in (.101,.03,.0165):
            result=self.action([((.5,.5+error),(0,0,1,1),1),ObservationUnavailable('hidden')])
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['landing'],'predicted')
            self.assertFalse(e.calibration.get('suspect',False))
        self.assertEqual(len(e.calibration['landing_samples']),3)
        self.assertAlmostEqual(e.landing_offset(e.calibration)[1],(.03+.0165)/2)
        # After applying the learned bias, a near-target first landing still
        # records the underlying bias, rather than adding it twice.
        offset=e.landing_offset(e.calibration)[1]
        result=self.action([((.5,.5),(0,0,1,1),1)])
        self.assertTrue(result['ok']); self.assertAlmostEqual(result['landing_offset'][1],offset)
        self.assertAlmostEqual(e.calibration['landing_samples'][-1][1],offset)
        self.assertAlmostEqual(e.landing_offset(e.calibration)[1],offset)

    def test_large_first_miss_corrected_to_good_final_is_not_suspect(self):
        result=self.action([((.5,.601),(0,0,1,1),1),((.5,.5098),(0,0,1,1),2)])
        self.assertTrue(result['ok'],result); self.assertTrue(result['verified'])
        self.assertFalse(self.engine.calibration.get('suspect',False))

    def test_frame_change_persists_offset_reset_even_when_pointer_is_unseen(self):
        e=self.engine
        e.calibration['landing_frame_size']=[600,1000]
        e.calibration['landing_samples']=[[0,.025],[0,.025]]
        e.save()
        e.ingest(jpeg_image(Image.new('RGB',(300,500),'white')))
        result=self.action([ObservationUnavailable('hidden')])
        self.assertFalse(result['click_sent'])
        stored=json.loads(e.path.read_text())['calibration']
        self.assertNotIn('landing_samples',stored)
        self.assertNotIn('landing_frame_size',stored)

    def test_predicted_after_observed_correction_clicks_even_if_untrusted(self):
        self.engine.calibration['suspect']=True
        result=self.action([((.5,.525),(0,0,1,1),1),ObservationUnavailable('hidden')])
        self.assertTrue(result['ok'],result); self.assertTrue(result['click_sent'])
        self.assertFalse(result['verified']); self.assertEqual(result['landing'],'predicted')
        self.assertAlmostEqual(result['predicted_landing'][1],.5)
        self.assertIsNone(result['final_landing'])

    def test_never_observed_untrusted_and_large_observed_miss_refuse(self):
        self.engine.calibration['suspect']=True
        result=self.action([ObservationUnavailable('hidden')])
        self.assertFalse(result['ok']); self.assertFalse(result['click_sent'])
        with patch.object(self.engine,'click') as click, patch.object(self.engine,'save_debug') as debug:
            result=self.action([((.3,.3),(0,0,1,1),1)]*3)
        self.assertFalse(result['ok']); self.assertFalse(result['click_sent']); self.assertFalse(result['verified'])
        self.assertEqual(result['landing'],'observed')
        self.assertIn('more than 3%',result['message']); click.assert_not_called(); debug.assert_called()

    def test_observed_small_miss_clicks_unverified(self):
        result=self.action([((.52,.5),(0,0,1,1),1)]*3)
        self.assertTrue(result['ok']); self.assertTrue(result['click_sent'])
        self.assertFalse(result['verified']); self.assertEqual(result['landing'],'observed')

    def test_outside_tolerance_prediction_refuses_even_if_calibration_trusted(self):
        self.engine.calibration['width']=10
        with patch.object(self.engine,'click') as click, patch.object(self.engine,'save_debug') as debug:
            result=self.action([((.56,.5),(0,0,1,1),1),ObservationUnavailable('hidden')])
        self.assertFalse(result['ok']); self.assertFalse(result['click_sent'])
        self.assertEqual(result['landing'],'unknown'); self.assertAlmostEqual(result['predicted_landing'][0],.46)
        self.assertIn('predicted landing is more than 1%',result['message'])
        click.assert_not_called(); debug.assert_called()

    def test_correction_passes_previous_observation_to_difference_fallback(self):
        e=self.engine
        with patch.object(e,'capture_after',return_value=(time.monotonic(),e.jpeg)), \
             patch.object(e,'wait_pointer',side_effect=[((.52,.5),(0,0,1,1),1),((.5,.5),(0,0,1,1),2)]) as wait, \
             patch.object(e,'wait_frame'),patch('oiu.control.time.sleep'):
            self.assertTrue(e.request('pointer_tap',x=.5,y=.5)['ok'])
        self.assertEqual(wait.call_args_list[1].kwargs['exclude'],(.52,.5))

    def test_slow_capture_accepts_one_post_motion_frame(self):
        e=self.engine; now=time.monotonic(); e.frame_intervals.extend([.5,.6,.5])
        with patch.object(e,'capture_after',return_value=(now+.6,e.jpeg)), \
             patch('oiu.vision.pointer',return_value=((.5,.5),(0,0,1,1))) as pointer:
            point,_,_=e.wait_pointer(now,expected=(.5,.5),board_required=False)
        self.assertEqual(point,(.5,.5)); self.assertEqual(pointer.call_count,1)
        self.assertEqual(e.last_observation_method,'direct')

    def test_late_first_frame_is_not_a_slow_capture_interval(self):
        e=self.engine; now=time.monotonic(); e.frame_intervals.extend([.03]*5)
        with patch.object(e,'capture_after',side_effect=[(now+.6,e.jpeg),(now+.8,e.jpeg)]), \
             patch('oiu.vision.pointer',return_value=((.5,.5),(0,0,1,1))) as pointer:
            e.wait_pointer(now,expected=(.5,.5),board_required=False)
        self.assertEqual(pointer.call_count,2)

    def test_slow_consecutive_frames_allow_single_detection(self):
        e=self.engine; now=time.monotonic(); e.frame_intervals.extend([.03]*5)
        with patch.object(e,'capture_after',side_effect=[(now+.1,e.jpeg),(now+.5,e.jpeg)]), \
             patch('oiu.vision.pointer',side_effect=[ValueError('hidden'),((.5,.5),(0,0,1,1))]) as pointer:
            e.wait_pointer(now,expected=(.5,.5),board_required=False)
        self.assertEqual(pointer.call_count,2)

    def test_difference_new_disc_and_widespread_change(self):
        base=Image.new('RGB',(600,1000),'white'); after=base.copy()
        ImageDraw.Draw(after).ellipse((285,485,315,515),fill=(145,145,145))
        reference=jpeg_image(base); post=jpeg_image(after)
        point,_=vision.pointer_difference(post,reference,(.5,.5))
        self.assertAlmostEqual(point[0],.5,places=2); self.assertAlmostEqual(point[1],.5,places=2)
        ImageDraw.Draw(after).rectangle((0,0,599,300),fill='black')
        with self.assertRaises(ValueError): vision.pointer_difference(jpeg_image(after),reference,(.5,.5))
        square=base.copy(); ImageDraw.Draw(square).rectangle((285,485,315,515),fill=(145,145,145))
        with self.assertRaises(ValueError): vision.pointer_difference(jpeg_image(square),reference,(.5,.5))
        e=self.engine; e.frame_intervals.extend([.5]*3); now=time.monotonic()
        with patch.object(e,'capture_after',return_value=(now+.5,post)),patch('oiu.vision.pointer',side_effect=ValueError('hidden')):
            e.wait_pointer(now,expected=(.5,.5),board_required=False,reference=reference)
        self.assertEqual(e.last_observation_method,'difference')

    def test_difference_excludes_vacated_pointer_and_finds_new_disc(self):
        base=Image.new('RGB',(600,1000),(200,200,200))
        old=base.copy(); new=base.copy()
        ImageDraw.Draw(old).ellipse((260,490,280,510),fill=(100,100,100))
        ImageDraw.Draw(new).ellipse((296,490,316,510),fill=(100,100,100))
        reference,post=jpeg_image(old),jpeg_image(new)
        with self.assertRaises(ValueError): vision.pointer_difference(post,reference,(.49,.5))
        point,_=vision.pointer_difference(post,reference,(.49,.5),exclude=(.45,.5))
        self.assertAlmostEqual(point[0],.51,places=2)
        e=self.engine; e.frame_intervals.extend([.5]*3); now=time.monotonic()
        with patch.object(e,'capture_after',return_value=(now+.5,post)), \
             patch('oiu.vision.pointer',side_effect=ValueError('hidden')):
            point,_,_=e.wait_pointer(now,expected=(.49,.5),tolerance=.06,board_required=False,reference=reference,exclude=(.45,.5))
        self.assertAlmostEqual(point[0],.51,places=2)

    def test_wake_net_zero_and_bounded_to_three(self):
        e=self.engine; moves=[]
        with patch.object(e,'move',side_effect=lambda x,y:moves.append((x,y))): e.wake_pointer(-1)
        self.assertEqual(moves,[(-3,0),(3,0)])
        e.motion_at=time.monotonic()-1
        calls=[(time.monotonic()+i*.01,e.jpeg) for i in range(6)]
        calls.append(ObservationUnavailable('end'))
        def capture(*args):
            if calls: return_value=calls.pop(0)
            else: raise ObservationUnavailable('end')
            if isinstance(return_value,Exception): raise return_value
            return return_value
        with patch.object(e,'capture_after',side_effect=capture),patch.object(e,'wake_pointer') as wake, \
             patch('oiu.vision.pointer',side_effect=ValueError('hidden')):
            with self.assertRaises(ObservationUnavailable): e.wait_pointer(time.monotonic(),expected=(.5,.5),board_required=False,timeout=.01)
        self.assertLessEqual(wake.call_count,3); self.assertEqual(wake.call_count,3)

    def test_debug_files_rotation_and_io_errors(self):
        e=self.engine; e.debug_target=[.5,.5]
        for i in range(15): self.assertTrue(e.save_debug('hidden',expected=(.5,.5),landing=None,reference=e.jpeg))
        if e.debug_thread: e.debug_thread.join(5)
        paths=list((Path(self.temp.name)/'debug').iterdir())
        self.assertLessEqual(len(paths),30); self.assertTrue(any(p.suffix=='.jpg' for p in paths))
        meta=next(p for p in paths if p.suffix=='.json')
        self.assertEqual(json.loads(meta.read_text())['target'],[.5,.5])
        with patch.object(Path,'mkdir',side_effect=OSError('no space')):
            e.save_debug('hidden')
            if e.debug_thread: e.debug_thread.join(5)
            result=self.action([ObservationUnavailable('hidden')])
            if e.debug_thread: e.debug_thread.join(5)
            self.assertTrue(result['ok']); self.assertTrue(result['click_sent'])


if __name__=='__main__': unittest.main()
