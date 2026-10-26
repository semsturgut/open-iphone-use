"""Transport routing/registration contract tests without pretending to exercise radios."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import socket
import sys
import types
import unittest


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
    def test_per_peer_channel_does_not_broadcast_and_legacy_ambiguity_fails(self):
        from oiu.platforms.bluez import BlueZRadio
        async def check():
            radio=object.__new__(BlueZRadio)
            radio.loop=asyncio.get_running_loop()
            peers=['/org/bluez/hci0/dev_A','/org/bluez/hci0/dev_B']
            radio.seen_peers=set(peers)
            async def connected(): return peers
            radio.connected=connected
            local,remote=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET if sys.platform=='linux' else socket.SOCK_DGRAM)
            local.setblocking(False); remote.setblocking(False)
            events=[]
            char=types.SimpleNamespace(channels={peers[0]:local},notifying=True,value=b'',emit_properties_changed=events.append)
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
            radio=types.SimpleNamespace(reports={1:char})
            await namespace['notify'](radio,'a',1,bytes(8))
            self.assertIs(calls[0][1],a)
            with self.assertRaises(OSError): await namespace['notify'](radio,'missing',1,bytes(8))
            self.assertEqual(len(calls),1)
        asyncio.run(check())
