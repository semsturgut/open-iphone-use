"""Transport routing/registration contract tests without pretending to exercise radios."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('dbus_fast'), 'dbus-fast installed only on Linux by default')
class BlueZTests(unittest.TestCase):
    def test_object_manager_matches_exported_interfaces(self):
        from oiu.platforms.bluez import Application,Service,Characteristic,Descriptor
        service=Service('/test/hid')
        char=Characteristic(None,service,0,'2a4d',['read','notify','encrypt-notify'],bytes(8),1)
        app=Application([service,char,Descriptor(char,1)])
        method=next(m for m in app._get_methods(app) if m.name=='GetManagedObjects')
        objects=method.fn(app)
        self.assertEqual(objects[service.path][service.name]['UUID'].value,'00001812-0000-1000-8000-00805f9b34fb')
        self.assertIn('encrypt-notify',objects[char.path][char.name]['Flags'].value)
        self.assertEqual(objects[char.path+'/reference']['org.bluez.GattDescriptor1']['Flags'].value,['read','encrypt-read'])
    def test_start_publishes_full_uuids_encryption_and_report_references(self):
        from oiu.platforms import bluez
        from oiu.hid import HID_UUID, REPORT_MAP, uuid
        from dbus_fast import Variant
        exported = []
        async def noop(*args): pass
        adapter = '/org/bluez/hci0'
        async def objects(): return {adapter: {'org.bluez.GattManager1': {},
            'org.bluez.LEAdvertisingManager1': {}, 'org.bluez.Adapter1': {'Powered': Variant('b', True)}}}
        proxy = types.SimpleNamespace(get_interface=lambda name: types.SimpleNamespace(
            call_get_managed_objects=objects, call_register_agent=noop, call_request_default_agent=noop,
            call_register_application=noop, call_register_advertisement=noop, call_set=noop))
        async def introspect(*args): return None
        async def connect(): return bus
        bus = types.SimpleNamespace(connect=connect, introspect=introspect,
            get_proxy_object=lambda *args: proxy, export=lambda path, obj: exported.append(obj))
        async def check():
            radio = object.__new__(bluez.BlueZRadio)
            radio.registrations, radio.reports, radio.seen_peers = [], {}, set()
            with patch.object(bluez, 'MessageBus', return_value=bus): await radio.start()
        asyncio.run(check())
        services = [o for o in exported if isinstance(o, bluez.Service)]
        self.assertEqual([s.UUID for s in services], [HID_UUID])
        chars = [o for o in exported if isinstance(o, bluez.Characteristic)]
        self.assertEqual([c.UUID for c in chars], [uuid(u) for u in
                         ('2a4a', '2a4b', '2a4c', '2a4e', '2a4d', '2a4d')])
        self.assertEqual(chars[1].value, REPORT_MAP)
        for c in chars:
            self.assertIn('encrypt-write' if c.short == '2a4c' else 'encrypt-read', c.flags)
            if c.report: self.assertIn('encrypt-notify', c.flags)
        descriptors = [o for o in exported if isinstance(o, bluez.Descriptor)]
        self.assertEqual([d.report for d in descriptors], [1, 2])
        self.assertEqual([len(c.value) for c in chars if c.report], [8, 4])
        adv = next(o for o in exported if isinstance(o, bluez.Advertisement))
        self.assertEqual(adv.ServiceUUIDs, [HID_UUID])
        self.assertEqual(adv.Appearance, 0x03c0)
        self.assertEqual(adv.Timeout, 0)

    def test_control_writes_reject_boot_protocol_invalid_values_and_offsets(self):
        from oiu.platforms.bluez import Characteristic, Service
        from dbus_fast import DBusError, Variant
        for short, valid in [('2a4c', b'\0'), ('2a4e', b'\1')]:
            char = Characteristic(None, Service('/test'), 0, short, [])
            method = next(m for m in char._get_methods(char) if m.name == 'WriteValue').fn
            method(char, valid, {})
            for value, options in [(valid, {'offset': Variant('q', 1)}), (b'\2', {}), (b'', {})]:
                with self.assertRaises(DBusError): method(char, value, options)
            if short == '2a4e':
                with self.assertRaises(DBusError): method(char, b'\0', {})

    def test_read_reports_are_scoped_to_the_requesting_peer(self):
        from oiu.platforms.bluez import Characteristic, Service
        from dbus_fast import Variant
        char=Characteristic(types.SimpleNamespace(seen_peers=set()),Service('/test'),0,'2a4d',[],bytes(4),2)
        char.peer_values={'/a':bytes([1,2,3,4]),'/b':bytes([0,9,8,7])}
        read=next(m.fn for m in char._get_methods(char) if m.name=='ReadValue')
        self.assertEqual(read(char,{'device':Variant('o','/a')}),bytes([1,2,3,4]))
        self.assertEqual(read(char,{'device':Variant('o','/b')}),bytes([0,9,8,7]))
        self.assertEqual(read(char,{}),bytes(4))

    def test_per_peer_channel_does_not_broadcast_and_legacy_ambiguity_fails(self):
        from oiu.platforms.bluez import BlueZRadio
        async def check():
            radio=object.__new__(BlueZRadio)
            radio.loop=asyncio.get_running_loop()
            peers=['/org/bluez/hci0/dev_A','/org/bluez/hci0/dev_B']
            radio.seen_peers={peers[0]}  # A second connected phone may not have read a value yet.
            async def connected(): return peers
            radio.connected=connected
            local,remote=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET if sys.platform=='linux' else socket.SOCK_DGRAM)
            local.setblocking(False); remote.setblocking(False)
            events=[]
            char=types.SimpleNamespace(channels={peers[0]:local},peer_values={},notifying=True,value=b'',emit_properties_changed=events.append)
            radio.reports={('/org/bluez/hci0',2):char}
            try:
                await radio.notify(peers[0],2,b'\0\x10\0\0')
                self.assertEqual(remote.recv(4),b'\0\x10\0\0')
                self.assertEqual(events,[])
                with self.assertRaises(OSError): await radio.notify(peers[1],2,bytes(4))
                char.channels={}
                with self.assertRaises(OSError): await radio.notify(peers[0],2,bytes(4))
                peers.pop()
                await radio.notify(peers[0],2,b'\0\x01\0\0')
                self.assertEqual(events,[{'Value':b'\0\x01\0\0'}])
            finally: local.close();remote.close()
        asyncio.run(check())


class WindowsContractTests(unittest.TestCase):
    def test_start_builds_full_uuid_encrypted_hid_schema(self):
        import uuid
        from oiu.hid import HID_UUID, REPORT_MAP, uuid as full_uuid
        # Execute the real WinRT startup method with projections replaced at its boundary.
        source = (Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'WindowsRadio')
        start = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'start')
        chars, descriptors, service_ids = [], [], []
        success = 'success'
        async def characteristic(id_, parameters):
            async def descriptor(id_, params):
                descriptors.append((id_, params))
                return types.SimpleNamespace(error=success)
            char = types.SimpleNamespace(create_descriptor_async=descriptor,
                add_read_requested=lambda callback: 1, add_write_requested=lambda callback: 2,
                add_subscribed_clients_changed=lambda callback: 3)
            chars.append((id_, parameters, char))
            return types.SimpleNamespace(error=success, characteristic=char)
        provider = types.SimpleNamespace(service=types.SimpleNamespace(create_characteristic_async=characteristic),
            add_advertisement_status_changed=lambda callback: 0, start_advertising=lambda params: None,
            advertisement_status='started')
        async def create(id_):
            service_ids.append(id_)
            return types.SimpleNamespace(error=success, service_provider=provider)
        async def adapter(): return types.SimpleNamespace(is_peripheral_role_supported=True)
        namespace = dict(asyncio=asyncio, uuids=uuid, HID_UUID=HID_UUID, REPORT_MAP=REPORT_MAP, uuid=full_uuid,
            BluetoothAdapter=types.SimpleNamespace(get_default_async=adapter), BluetoothError=types.SimpleNamespace(SUCCESS=success),
            GattServiceProvider=types.SimpleNamespace(create_async=create),
            GattLocalCharacteristicParameters=types.SimpleNamespace, GattLocalDescriptorParameters=types.SimpleNamespace,
            GattServiceProviderAdvertisingParameters=types.SimpleNamespace,
            GattProtectionLevel=types.SimpleNamespace(ENCRYPTION_REQUIRED='encrypted'),
            GattCharacteristicProperties=types.SimpleNamespace(READ=2, WRITE_WITHOUT_RESPONSE=4, NOTIFY=16),
            GattServiceProviderAdvertisementStatus=types.SimpleNamespace(STARTED='started',
                STARTED_WITHOUT_ALL_ADVERTISEMENT_DATA='partial', ABORTED='aborted'), buffer=lambda value: value)
        exec(compile(ast.Module(body=[start], type_ignores=[]), '<actual-winrt-start>', 'exec'), namespace)
        async def check():
            async def keep(): pass
            radio = types.SimpleNamespace(reports={}, values={}, events=[], tasks=set(), keep_advertising=keep, loop=asyncio.get_running_loop(),
                _advertisement=lambda *args: None, _read=lambda *args: None, _write=lambda *args: None, _subscribers=lambda *args:None)
            await namespace['start'](radio)
            self.assertTrue(radio.advertising.is_connectable)
            self.assertTrue(radio.advertising.is_discoverable)
            await asyncio.gather(*radio.tasks)
        asyncio.run(check())
        self.assertEqual(service_ids, [uuid.UUID(HID_UUID)])
        self.assertEqual([str(id_) for id_, _, _ in chars], [full_uuid(u) for u in
                         ('2a4a', '2a4b', '2a4c', '2a4e', '2a4d', '2a4d')])
        self.assertEqual(chars[1][1].static_value, REPORT_MAP)
        for _, params, _ in chars:
            self.assertEqual(params.read_protection_level, 'encrypted')
            self.assertEqual(params.write_protection_level, 'encrypted')
        self.assertEqual([params.static_value for _, params in descriptors], [b'\1\1', b'\2\1'])
        self.assertTrue(all(str(id_) == full_uuid('2908') for id_, _ in descriptors))
        self.assertTrue(all(params.read_protection_level == 'encrypted' for _, params in descriptors))
        self.assertEqual([params.characteristic_properties for _, params, _ in chars[-2:]], [18, 18])

    def test_winrt_read_uses_the_request_session_and_disconnect_clears_only_that_peer(self):
        source=(Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='WindowsRadio')
        methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_read','_subscribers')]
        namespace={'buffer':lambda value:value}
        exec(compile(ast.Module(body=methods,type_ignores=[]),'<actual-winrt-read>','exec'),namespace)
        async def check():
            received=[];pending=[]
            char=types.SimpleNamespace(subscribed_clients=[])
            radio=types.SimpleNamespace(loop=asyncio.get_running_loop(),reports={2:char},values={},
                report_values={('a',2):bytes([1,2,3,4]),('b',2):bytes([0,8,7,6])},
                _event=lambda coroutine,deferral:pending.append(coroutine))
            for peer in ('a','b'):
                async def request(): return types.SimpleNamespace(offset=0,respond_with_value=received.append)
                args=types.SimpleNamespace(session=types.SimpleNamespace(device_id=types.SimpleNamespace(id=peer)),
                    get_request_async=request,get_deferral=lambda:None)
                namespace['_read'](radio,char,args)
                await pending.pop()
            self.assertEqual(received,[bytes([1,2,3,4]),bytes([0,8,7,6])])
            char.subscribed_clients=[types.SimpleNamespace(session=types.SimpleNamespace(device_id=types.SimpleNamespace(id='b')))]
            namespace['_subscribers'](radio,char,None)
            await asyncio.sleep(0)
            self.assertEqual(radio.report_values,{('b',2):bytes([0,8,7,6])})
        asyncio.run(check())

    def test_control_write_validation_and_command_response_rules(self):
        source = (Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'WindowsRadio')
        write = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_write')
        class Reader:
            def __init__(self, data): self.data = data; self.unconsumed_buffer_length = len(data)
            def read_bytes(self, output): output[:] = self.data
            def close(self): pass
        namespace = dict(DataReader=types.SimpleNamespace(from_buffer=Reader),
                         GattWriteOption=types.SimpleNamespace(WRITE_WITH_RESPONSE='request'))
        exec(compile(ast.Module(body=[write], type_ignores=[]), '<actual-winrt-write>', 'exec'), namespace)
        async def check():
            mode, control = object(), object()
            for data, offset, option, expected in [(b'\1', 0, 'request', 'ok'),
                    (b'\0', 0, 'request', 0x13), (b'\1', 1, 'request', 0x13),
                    (b'\2', 0, 'command', None)]:
                replies, pending = [], []
                async def request(): return types.SimpleNamespace(value=data, offset=offset, option=option,
                    respond=lambda: replies.append('ok'), respond_with_protocol_error=replies.append)
                args = types.SimpleNamespace(get_deferral=lambda: None, get_request_async=request)
                radio = types.SimpleNamespace(mode=mode, control=control, values={},
                    _event=lambda coro, deferral: pending.append(coro))
                namespace['_write'](radio, mode, args)
                await pending[0]
                self.assertEqual(replies, [] if expected is None else [expected])
        asyncio.run(check())

    def test_targeted_notify_requires_exact_client_and_success(self):
        # Run the actual method AST with fake native objects, not a replacement implementation.
        source=(Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        tree=ast.parse(source)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WindowsRadio')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='notify')
        namespace={'GattCommunicationStatus':types.SimpleNamespace(SUCCESS='success'),'buffer':lambda value:value}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual-winrt-notify>','exec'),namespace)
        async def check():
            calls=[]
            a=types.SimpleNamespace(session=types.SimpleNamespace(device_id=types.SimpleNamespace(id='a')),max_notification_size=20)
            b=types.SimpleNamespace(session=types.SimpleNamespace(device_id=types.SimpleNamespace(id='b')),max_notification_size=20)
            async def notify(payload,client):
                calls.append((payload,client));return types.SimpleNamespace(status='success')
            char=types.SimpleNamespace(subscribed_clients=[a,b],notify_value_async=notify)
            radio=types.SimpleNamespace(reports={1:char},report_values={})
            await namespace['notify'](radio,'a',1,bytes(8))
            self.assertIs(calls[0][1],a)
            with self.assertRaises(OSError): await namespace['notify'](radio,'missing',1,bytes(8))
            self.assertEqual(len(calls),1)
        asyncio.run(check())


class MacHIDContractTests(unittest.TestCase):
    def test_full_service_uuid_and_shared_report_map_are_preserved(self):
        root = Path(__file__).resolve().parents[2] / 'macos/Sources'
        headless = (root / 'OpenIPhoneUseHeadless/HeadlessRadio.swift').read_text()
        core = (root / 'OpenIPhoneUseCore/BLEHIDPeripheral.swift').read_text()
        for source in (headless, core):
            self.assertIn('CBUUID(string: "00001812-0000-1000-8000-00805F9B34FB")', source)
            self.assertNotIn('CBUUID(string: "1812")', source)
            self.assertIn('.notifyEncryptionRequired', source)
            self.assertIn('onSubscribedCentrals: [', source)
        self.assertIn('case "2A4B": value = BLEHIDPeripheral.reportDescriptor', headless)
        self.assertIn('value: nil, permissions: [.readEncryptionRequired]', headless)


class BluetoothNameTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('dbus_fast'), 'dbus-fast required')
    def test_bluez_names_are_matched_by_exact_device_path(self):
        from oiu.platforms.bluez import BlueZRadio
        from dbus_fast import Variant
        a, b = '/org/bluez/hci0/dev_A', '/org/bluez/hci0/dev_B'
        async def objects():
            return {peer: {'org.bluez.Device1': {'Connected':Variant('b',True),'Paired':Variant('b',True),
                'Alias':Variant('s','iPhone'),'Name':Variant('s',name)}} for peer,name in ((a,'Real A'),(b,'Real B'))}
        async def check():
            radio=object.__new__(BlueZRadio)
            radio.manager=types.SimpleNamespace(call_get_managed_objects=objects)
            radio.seen_peers={a,b}
            radio.reports={('/org/bluez/hci0',r):types.SimpleNamespace(channels={a:True,b:True},notifying=False) for r in (1,2)}
            peers=await radio.get_peers()
            self.assertEqual({p['id']:p['name'] for p in peers},{a:'Real A',b:'Real B'})
        asyncio.run(check())

    def test_winrt_resolves_names_using_each_exact_subscriber_device_id(self):
        source=(Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='WindowsRadio')
        methods=[n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name in ('get_peers','_resolve_name')]
        looked_up=[]
        async def device(id_):
            looked_up.append(id_)
            return types.SimpleNamespace(name='Real '+id_ if id_=='b' else 'iPhone',device_information=types.SimpleNamespace(name='Real '+id_ if id_=='a' else 'iPhone'),close=lambda:None)
        from oiu.names import specific
        namespace={'asyncio':asyncio,'BluetoothLEDevice':types.SimpleNamespace(from_id_async=device),'specific':specific}
        exec(compile(ast.Module(body=methods,type_ignores=[]),'<actual-winrt-peers>','exec'),namespace)
        async def check():
            clients=[types.SimpleNamespace(session=types.SimpleNamespace(device_id=types.SimpleNamespace(id=id_))) for id_ in ('a','b')]
            radio=types.SimpleNamespace(error=None,peer_names={},name_pending=set(),tasks=set(),
                reports={r:types.SimpleNamespace(subscribed_clients=clients) for r in (1,2)})
            radio._resolve_name=types.MethodType(namespace['_resolve_name'],radio)
            peers=await namespace['get_peers'](radio)
            self.assertEqual([p['name_source'] for p in peers],['fallback','fallback'])
            self.assertEqual(looked_up,[])  # Status did not wait on either name lookup.
            await asyncio.gather(*radio.tasks)
            peers=await namespace['get_peers'](radio)
            self.assertEqual([(p['id'],p['name'],p['reports']) for p in peers],[('a','Real a',[1,2]),('b','Real b',[1,2])])
            self.assertEqual(looked_up,['a','b'])
        asyncio.run(check())


class ReconnectionTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('dbus_fast'), 'requires dbus-fast')
    def test_bluez_release_retries_same_advertisement_and_shutdown_does_not_retry(self):
        from oiu.platforms.bluez import BlueZRadio,Advertisement
        from dbus_fast import DBusError
        calls=[]
        async def register(path, options):
            calls.append(path)
            if len(calls)==1: raise DBusError('org.bluez.Error.NotReady','radio recovering')
        async def intro(*args): return None
        async def no_wait(*args): pass
        async def check():
            radio=object.__new__(BlueZRadio)
            radio.stopping=False; radio.reconnect_tasks=set()
            radio.bus=types.SimpleNamespace(introspect=intro,get_proxy_object=lambda *args:types.SimpleNamespace(
                get_interface=lambda name:types.SimpleNamespace(call_register_advertisement=register)))
            advert=Advertisement('/stable/advertisement',lambda:radio.advertisement_released('/adapter','/stable/advertisement'))
            release=next(m for m in advert._get_methods(advert) if m.name=='Release')
            with patch('oiu.platforms.bluez.asyncio.sleep',no_wait):
                release.fn(advert)
                await asyncio.gather(*radio.reconnect_tasks)
            self.assertEqual(calls,['/stable/advertisement','/stable/advertisement'])
            radio.stopping=True
            release.fn(advert)
            self.assertFalse(radio.reconnect_tasks)
        asyncio.run(check())

    def test_winrt_stopped_provider_restarts_with_same_connectable_parameters(self):
        source=(Path(__file__).resolve().parents[1]/'oiu/platforms/windows.py').read_text()
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='WindowsRadio')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='keep_advertising')
        provider=types.SimpleNamespace(advertisement_status='stopped')
        params=object(); calls=[]
        radio=types.SimpleNamespace(stopping=False,provider=provider,advertising=params,error=None)
        def advertise(value):
            calls.append(value)
            provider.advertisement_status='started'
        provider.start_advertising=advertise
        ticks=0
        async def sleep(*args):
            nonlocal ticks
            ticks+=1
            if ticks==3: radio.stopping=True
        namespace=dict(asyncio=types.SimpleNamespace(sleep=sleep),GattServiceProviderAdvertisementStatus=types.SimpleNamespace(STOPPED='stopped',ABORTED='aborted'))
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual-winrt-reconnect>','exec'),namespace)
        asyncio.run(namespace['keep_advertising'](radio))
        self.assertEqual(calls,[params])  # No repeatedly starting an already active provider.

    def test_mac_restoration_keeps_exact_report_reference_and_central_routing(self):
        source=(Path(__file__).resolve().parents[2]/'macos/Sources/OpenIPhoneUseHeadless/HeadlessRadio.swift').read_text()
        self.assertIn('CBPeripheralManagerOptionRestoreIdentifierKey: restorationIdentifier',source)
        restored=source.split('willRestoreState dict:')[1].split('private func reportID')[0]
        self.assertIn('CBPeripheralManagerRestoredStateServicesKey',restored)
        self.assertIn('reference[1] == 1',restored)
        self.assertIn('reports[1]',restored); self.assertIn('reports[2]',restored)
        self.assertIn('characteristic.subscribedCentrals',restored)
        self.assertIn('subscriptions[id, default: []].insert(report)',restored)
        self.assertIn('onSubscribedCentrals: [peer]',source)
        self.assertIn('peripheral.isAdvertising',source)
        self.assertNotIn('UUID().uuidString',source)  # Identity cannot change on each launch.
