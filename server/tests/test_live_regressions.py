"""Synthetic regressions for test11 findings; no personal screenshots or live radio."""
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw
from oiu import vision
from oiu.control import Control,Pairing


def jpeg(pic):
    output=BytesIO();pic.save(output,format='JPEG',quality=95);return output.getvalue()

def board_image(pointer=(300,400)):
    pic=Image.new('RGB',(600,1000),'white');d=ImageDraw.Draw(pic)
    d.rectangle((0,0,599,45),fill=(230,40,40))
    d.rounded_rectangle((535,8,583,30),radius=4,fill=(0,224,122))  # Charging battery.
    d.rectangle((0,55,599,900),outline=(0,224,122),width=3)
    d.rectangle((0,902,599,999),fill=(240,240,240))
    d.ellipse((370,930,390,950),fill=(140,140,140))  # Safari control, not the pointer.
    if pointer:
        x,y=pointer;d.ellipse((x-11,y-11,x+11,y+11),fill=(140,140,140))
    return pic


class VisualRegressions(unittest.TestCase):
    def test_green_charging_battery_does_not_extend_board_bounds(self):
        _,rect=vision.board(jpeg(board_image()))
        self.assertAlmostEqual(rect[1],.055,delta=.003)
        self.assertAlmostEqual(rect[3],.846,delta=.003)

    def test_status_dot_and_charging_battery_alone_are_not_a_board(self):
        pic=Image.new('RGB',(600,1000),'black');d=ImageDraw.Draw(pic)
        d.rectangle((535,8,583,30),fill=(0,224,122));d.ellipse((15,90,25,100),fill=(0,224,122))
        with self.assertRaisesRegex(ValueError,'status dots and charging battery'):
            vision.board(jpeg(pic))

    def test_visible_board_excludes_safari_toolbar_pointer_lookalike(self):
        point,rect=vision.pointer(jpeg(board_image()),board_required=False,expected=(.5,.4))
        self.assertAlmostEqual(point[0],.5,delta=.005)
        self.assertAlmostEqual(point[1],.4,delta=.005)
        self.assertLess(rect[3],.9)

    def test_expected_region_does_not_accept_a_distant_round_ui_control(self):
        pic=Image.new('RGB',(600,1000),'white');d=ImageDraw.Draw(pic)
        for x,y in ((300,400),(550,850)):d.ellipse((x-11,y-11,x+11,y+11),fill=(140,140,140))
        point,_=vision.pointer(jpeg(pic),False,expected=(.5,.4))
        self.assertAlmostEqual(point[0],.5,delta=.005)
        with self.assertRaises(ValueError):vision.pointer(jpeg(pic),False,expected=(.05,.9))

    def test_two_nearby_candidates_still_reject_click_gate(self):
        pic=board_image();ImageDraw.Draw(pic).ellipse((340,390,360,410),fill=(140,140,140))
        with self.assertRaises(ValueError):vision.pointer(jpeg(pic),False,expected=(.5,.4))

    def test_calibration_rejects_back_button_area(self):
        rect=(0,.05,1,.84)
        self.assertFalse(vision.safe_calibration_point((.82,.14),rect))
        self.assertTrue(vision.safe_calibration_point((.78,.25),rect))
        self.assertFalse(vision.safe_calibration_point((.99,.5),rect))

    def test_translucent_grey_pointer_on_colored_app_screen(self):
        pic=Image.new('RGB',(600,1000),(190,130,110));d=ImageDraw.Draw(pic)
        d.ellipse((289,389,311,411),fill=(165,135,125))
        point,_=vision.pointer(jpeg(pic),False,expected=(.5,.4))
        self.assertAlmostEqual(point[0],.5,delta=.005)
        self.assertAlmostEqual(point[1],.4,delta=.005)

    def test_stationary_grey_icon_is_not_used_as_pointer_after_motion(self):
        before=Image.new('RGB',(600,1000),'white');d=ImageDraw.Draw(before)
        d.ellipse((289,389,311,411),fill=(140,140,140))
        with self.assertRaises(ValueError):
            vision.pointer(jpeg(before),False,expected=(.5,.4),reference=jpeg(before))
        after=before.copy();ImageDraw.Draw(after).ellipse((330,390,350,410),fill=(140,140,140))
        point,_=vision.pointer(jpeg(after),False,expected=(.5,.4),reference=jpeg(before))
        self.assertAlmostEqual(point[0],340/600,delta=.005)


class MotionRegressions(unittest.TestCase):
    def engine(self,directory):
        class Radio:
            def peers(self):return [{'id':'phone','reports':[1,2]}]
            def send(self,*args):pass
            def release(self,*args):pass
        engine=Control(Pairing(Radio()),Path(directory),peer='phone')
        engine.ingest(jpeg(board_image()))
        engine.calibration={'width':600,'height':1000,'offset_x':0,'offset_y':0,'aspect':.6,'version':3,'motion':'vertical-horizontal-16units-8ms-home-each'}
        return engine

    def test_calibration_failure_closes_board_and_invalidates_saved_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory);engine.save()
            with patch.object(engine,'wait_board',side_effect=ValueError('board missing')) as wait:
                result=engine.request('prepare_session')
            self.assertFalse(result['ok'])
            self.assertIn('Keep Safari sharing visible',result['message'])
            self.assertEqual(wait.call_count,2)
            self.assertFalse(engine.board_open)
            self.assertIsNone(engine.calibration)
            import json
            self.assertIsNone(json.loads(engine.path.read_text())['calibration'])

    def test_seven_points_fit_and_reject_inconsistent_motion(self):
        from oiu.calibration import fit
        units=[(64,128),(160,320),(132,220),(468,220),(132,780),(468,780),(300,500)]
        samples=[(x,y,x/600,y/1000) for x,y in units]
        result=fit(samples,.6)
        self.assertEqual(len(result['samples']),7)
        self.assertAlmostEqual(result['width'],600,delta=.01)
        self.assertAlmostEqual(result['height'],1000,delta=.01)
        samples[-1]=(300,500,.62,.62)
        with self.assertRaisesRegex(ValueError,'inconsistent'):fit(samples,.6)

    def test_axis_report_sizes_do_not_depend_on_other_axis_distance(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            def plan(dx,dy):
                reports=[]
                with patch.object(engine,'packet',side_effect=lambda r,p:reports.append(p)),patch('oiu.control.time.sleep'):
                    engine.move(dx,dy)
                signed=lambda b:b if b<128 else b-256
                return [(signed(p[1]),signed(p[2])) for p in reports]
            a,b=plan(64,128),plan(64,777)
            self.assertEqual([x for x,y in a if x],[x for x,y in b if x])
            self.assertEqual(sum(x for x,y in b),64);self.assertEqual(sum(y for x,y in b),777)
            self.assertTrue(all(x==0 or y==0 for x,y in b))
            self.assertTrue(all(abs(v)<=16 for pair in b for v in pair))

    def test_visual_correction_converges_before_tap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory);events=[]
            # Initial miss from the live test's lower-left target, then a correct landing.
            observations=[((.259,.647),(0,0,1,1),1),((.25,.7),(0,0,1,1),2),((.25,.7),(0,0,1,1),3)]
            with patch.object(engine,'home',side_effect=lambda:setattr(engine,'motion_at',time.monotonic())),patch.object(engine,'capture_after',return_value=(time.monotonic(),engine.jpeg)),patch.object(engine,'move',side_effect=lambda *args,**kw:(setattr(engine,'motion_at',time.monotonic()),events.append(('move',args)))),patch.object(engine,'wait_pointer',side_effect=observations),patch.object(engine,'packet',side_effect=lambda r,p:events.append(('packet',p))),patch('oiu.control.time.sleep'):
                engine.absolute(.25,.7)
                engine.click(gated=True,expected=(.25,.7),board_required=False)
            self.assertEqual(events[1],('move',(-5,53)))
            self.assertEqual(events[2][0],'packet')
            self.assertEqual(events[2][1][0],1)

    def test_reported_four_hardware_misses_are_corrected_before_click(self):
        cases=[((.25,.25),(.253,.234)),((.75,.25),(.805,.282)),
               ((.25,.7),(.259,.647)),((.75,.7),(.734,.679))]
        for target,landed in cases:
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                engine=self.engine(directory); moves=[]
                seen=[(landed,(0,0,1,1),1),(target,(0,0,1,1),2)]
                def move(dx,dy):
                    engine.motion_at=time.monotonic();moves.append((dx,dy))
                with patch.object(engine,'home',side_effect=lambda:setattr(engine,'motion_at',time.monotonic())),patch.object(engine,'capture_after',return_value=(time.monotonic(),engine.jpeg)),patch.object(engine,'move',side_effect=move),patch.object(engine,'wait_pointer',side_effect=seen),patch.object(engine,'packet') as packet:
                    self.assertEqual(engine.absolute(*target),target)
                    packet.assert_not_called()  # No press while positioning/correcting.
                self.assertEqual(moves[1],(round((target[0]-landed[0])*600),round((target[1]-landed[1])*1000)))

    def test_current_pointer_click_rejects_a_stationary_decoy(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            with patch.object(engine,'move',side_effect=lambda *args:setattr(engine,'motion_at',time.monotonic())),patch.object(engine,'wait_pointer',return_value=((.5,.5),(0,0,1,1),1)),patch.object(engine,'packet') as packet:
                with self.assertRaisesRegex(ValueError,'no click was sent'):engine.click_current()
                packet.assert_not_called()

    def test_nonconverging_position_never_presses_a_button(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            with patch.object(engine,'home',side_effect=lambda:setattr(engine,'motion_at',time.monotonic())),patch.object(engine,'capture_after',return_value=(time.monotonic(),engine.jpeg)),patch.object(engine,'move',side_effect=lambda *args:setattr(engine,'motion_at',time.monotonic())),patch.object(engine,'wait_pointer',return_value=((.3,.3),(0,0,1,1),1)),patch.object(engine,'packet') as packet:
                with self.assertRaisesRegex(ValueError,'no click was sent'):engine.absolute(.4,.4)
                packet.assert_not_called()

    def test_old_motion_calibration_is_invalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory);engine.calibration['version']=1
            self.assertFalse(engine.request('get_device_status')['status']['calibrationReady'])
            with self.assertRaisesRegex(ValueError,'older motion profile'):engine.absolute(.5,.5)
            with patch.object(engine,'calibrate') as calibrate:engine.ready()
            calibrate.assert_called_once()

    def test_board_timeout_reports_detection_failure_not_absent_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            with patch.object(engine,'capture_after',side_effect=ValueError('No fresh screen arrived')):
                with self.assertRaisesRegex(ValueError,'Calibration board did not become visible'):engine.wait_board(timeout=.01)

    def test_advance_feed_positive_drag_moves_up_and_negative_down(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            for direction in (1,-1):
                moves=[];reports=[]
                with patch.object(engine,'absolute'),patch.object(engine,'move',side_effect=lambda dx,dy,**kw:moves.append((dx,dy,kw))),patch.object(engine,'packet',side_effect=lambda r,p:reports.append(p)),patch.object(engine,'wait_frame'),patch('oiu.control.time.sleep'):
                    self.assertTrue(engine.request('advance_feed',dy=direction)['ok'])
                self.assertEqual(len(moves),1)
                self.assertEqual(moves[0][1],-400*direction)
                self.assertEqual(reports[0][0],1)
                self.assertEqual(reports[-1][0],0)

    def test_mcp_descriptions_pin_direction_in_both_servers(self):
        from oiu.mcp import tools
        descriptions={item['name']:item['description'] for item in tools()}
        self.assertIn('Positive dy',descriptions['scroll'])
        self.assertIn('NEXT',descriptions['advance_feed'])
        source=(Path(__file__).resolve().parents[2]/'macos/Sources/OpenIPhoneUseMCP/OpenIPhoneUseMCPServer.swift').read_text()
        scroll=source.split('Tool(name: "scroll",',1)[1].split('inputSchema:',1)[0]
        feed=source.split('Tool(name: "advance_feed",',1)[1].split('inputSchema:',1)[0]
        self.assertIn('Positive dy',scroll)
        self.assertIn('NEXT',feed)
        native=(Path(__file__).resolve().parents[2]/'macos/Sources/OpenIPhoneUseCore/OpenIPhoneUseController+Pointer.swift').read_text()
        self.assertIn('swipeScreen(direction: items > 0 ? "up" : "down")',native)
        self.assertIn('FeedDirection.wheelDelta(items: items, amount: wheelPerItem)',native)
        self.assertIn('hid.scroll(wheel: wheel)',native)

    def test_positive_scroll_matches_native_hid_wheel_sign(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=self.engine(directory)
            for amount in (600,-600):
                reports=[]
                with patch.object(engine,'packet',side_effect=lambda r,p:reports.append(p)),patch.object(engine,'wait_frame'),patch('oiu.control.time.sleep'):
                    self.assertTrue(engine.request('scroll',dy=amount)['ok'])
                signed=lambda b:b if b<128 else b-256
                self.assertEqual(sum(signed(p[3]) for p in reports),amount)
                self.assertTrue(all(p[0]==0 for p in reports))
