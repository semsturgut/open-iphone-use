"""BlueZ system-bus HID-over-GATT peripheral with encrypted attributes.

AcquireNotify file descriptors route per central. For older BlueZ StartNotify sessions,
notifications are permitted only while exactly one paired central uses an adapter.
Multiple adapters are registered automatically; ambiguous broadcast is never used.
"""
import asyncio
import os
import socket
from dbus_fast import Variant, DBusError
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, PropertyAccess
from dbus_fast.service import ServiceInterface, method, dbus_property

from ..hid import HID_UUID, REPORT_MAP, uuid
from ..names import specific
from .runtime import AsyncRadio

SERVICE = 'org.bluez.GattService1'
CHAR = 'org.bluez.GattCharacteristic1'
DESC = 'org.bluez.GattDescriptor1'
ADV = 'org.bluez.LEAdvertisement1'


class Service(ServiceInterface):
    def __init__(self, path):
        super().__init__(SERVICE)
        self.path = path
    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's': return HID_UUID
    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> 'b': return True
    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> 'ao': return []


class Descriptor(ServiceInterface):
    def __init__(self, char, report):
        super().__init__(DESC)
        self.char, self.report = char, report
        self.path = char.path + '/reference'
    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's': return uuid('2908')
    @dbus_property(access=PropertyAccess.READ)
    def Characteristic(self) -> 'o': return self.char.path
    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> 'as': return ['read', 'encrypt-read']
    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        offset = options.get('offset', Variant('q', 0)).value
        if offset > 2: raise DBusError('org.bluez.Error.InvalidOffset', 'Report reference offset')
        return bytes([self.report, 1])[offset:]


class Characteristic(ServiceInterface):
    def __init__(self, owner, service, index, short, flags, value=b'', report=0):
        super().__init__(CHAR)
        self.owner, self.service, self.short = owner, service, short
        self.path = service.path + '/char' + str(index)
        self.flags, self.value, self.report = flags, value, report
        self.notifying = False
        self.peer_values = {}
        self.channels = {}  # central Device1 path -> connected SOCK_SEQPACKET socket
    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's': return uuid(self.short)
    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> 'o': return self.service.path
    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> 'as': return self.flags
    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> 'ay': return self.value
    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> 'b': return self.notifying or bool(self.channels)
    @dbus_property(access=PropertyAccess.READ)
    def NotifyAcquired(self) -> 'b': return bool(self.channels)
    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        if 'device' in options: self.owner.seen_peers.add(options['device'].value)
        value = self.peer_values.get(options['device'].value if 'device' in options else '', bytes(8 if self.report == 1 else 4)) if self.report else self.value
        offset = options.get('offset', Variant('q', 0)).value
        if offset > len(value): raise DBusError('org.bluez.Error.InvalidOffset', 'HID read offset')
        return value[offset:]
    @method()
    def WriteValue(self, value: 'ay', options: 'a{sv}'):
        if options.get('offset', Variant('q', 0)).value != 0:
            raise DBusError('org.bluez.Error.InvalidOffset', 'HID writes start at zero')
        if self.short not in ('2a4c', '2a4e') or len(value) != 1:
            raise DBusError('org.bluez.Error.NotPermitted', 'Only HID control point and report protocol writes are allowed')
        if self.short == '2a4e' and value != b'\x01':
            raise DBusError('org.bluez.Error.NotSupported', 'Only report protocol is supported')
        if self.short == '2a4c' and value[0] not in (0, 1):
            raise DBusError('org.bluez.Error.InvalidValueLength', 'Invalid HID suspend value')
        self.value = value
    @method()
    def StartNotify(self):
        if not self.report: raise DBusError('org.bluez.Error.NotSupported', 'Not an input report')
        self.notifying = True
        self.emit_properties_changed({'Notifying': True})
    @method()
    def StopNotify(self):
        self.notifying = False
        self.emit_properties_changed({'Notifying': bool(self.channels)})
    @method()
    def AcquireNotify(self, options: 'a{sv}') -> 'hq':
        if not self.report or 'device' not in options:
            raise DBusError('org.bluez.Error.NotSupported', 'Per-device notify acquisition requires a device path')
        peer = options['device'].value
        mtu = options.get('mtu', Variant('q', 23)).value
        if peer in self.channels: raise DBusError('org.bluez.Error.InProgress', 'Notify channel already open')
        local, remote = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        local.setblocking(False)
        self.channels[peer] = local
        def disconnected():
            try: packet = local.recv(1)
            except BlockingIOError: return
            except OSError: packet = b''
            if not packet:
                self.owner.loop.remove_reader(local.fileno())
                self.channels.pop(peer, None)
                self.peer_values.pop(peer, None)
                local.close()
        self.owner.loop.add_reader(local.fileno(), disconnected)
        # The high-level dbus-fast service serializes a real FD and duplicates it via SCM_RIGHTS.
        self.owner.loop.call_later(1, remote.close)
        self.emit_properties_changed({'NotifyAcquired': True, 'Notifying': True})
        return [remote.fileno(), mtu]


class Advertisement(ServiceInterface):
    def __init__(self, path, released=None):
        super().__init__(ADV)
        self.path = path
        self.released = released
    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> 's': return 'peripheral'
    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> 'as': return [HID_UUID]
    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> 's': return 'Open iPhone Use'
    @dbus_property(access=PropertyAccess.READ)
    def Appearance(self) -> 'q': return 0x03c0
    @method()
    def Release(self):
        if self.released: self.released()
    @dbus_property(access=PropertyAccess.READ)
    def Timeout(self) -> 'q': return 0  # Keep accepting bonded centrals indefinitely.



class Application(ServiceInterface):
    def __init__(self, objects):
        super().__init__('org.freedesktop.DBus.ObjectManager')
        self.objects = objects
    @method()
    def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
        # Property access is obtained from the actual service interface, keeping introspection
        # and ObjectManager output identical even as per-device channels change.
        result = {}
        for obj in self.objects:
            result[obj.path] = {obj.name: {p.name: Variant(p.signature, getattr(obj, p.name))
                                         for p in obj.introspect().properties}}
        return result


class BlueZRadio(AsyncRadio):
    def __init__(self):
        self.bus = None
        self.registrations = []
        self.reports = {}
        self.seen_peers = set()
        self.reconnect_tasks = set()
        self.stopping = False
        super().__init__()

    async def start(self):
        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=True).connect()
            intro = await self.bus.introspect('org.bluez', '/')
            self.manager = self.bus.get_proxy_object('org.bluez', '/', intro).get_interface('org.freedesktop.DBus.ObjectManager')
            from .bluez_agent import PairingAgent
            self.agent = PairingAgent()
            self.bus.export('/dev/openiphoneuse/agent',self.agent)
            self.agent_manager = self.bus.get_proxy_object('org.bluez','/org/bluez',await self.bus.introspect('org.bluez','/org/bluez')).get_interface('org.bluez.AgentManager1')
            await self.agent_manager.call_register_agent('/dev/openiphoneuse/agent','KeyboardDisplay')
            try: await self.agent_manager.call_request_default_agent('/dev/openiphoneuse/agent')
            except DBusError:
                import sys
                print('BlueZ default-agent permission denied: approve pairing through the OS Bluetooth UI.',file=sys.stderr)
            objects = await self.manager.call_get_managed_objects()
            adapters = [p for p, interfaces in objects.items()
                        if 'org.bluez.GattManager1' in interfaces and 'org.bluez.LEAdvertisingManager1' in interfaces]
            if not adapters: raise OSError('No BlueZ adapter supports GATT server + LE advertising; connect a BLE peripheral-capable adapter')
            for index, adapter in enumerate(adapters):
                data = objects[adapter].get('org.bluez.Adapter1', {})
                if not data.get('Powered', Variant('b', False)).value:
                    raise OSError(f'{adapter} is powered off. Enable Bluetooth in system settings or run bluetoothctl power on')
                root = f'/dev/openiphoneuse/adapter{index}'
                service = Service(root + '/hid')
                chars = [Characteristic(self, service, 0, '2a4a', ['read', 'encrypt-read'], b'\x11\x01\0\x02'),
                         Characteristic(self, service, 1, '2a4b', ['read', 'encrypt-read'], REPORT_MAP),
                         Characteristic(self, service, 2, '2a4c', ['write-without-response', 'encrypt-write']),
                         Characteristic(self, service, 3, '2a4e', ['read', 'write-without-response', 'encrypt-read', 'encrypt-write'], b'\x01')]
                for report, size in ((1, 8), (2, 4)):
                    char = Characteristic(self, service, len(chars), '2a4d', ['read', 'notify', 'encrypt-read', 'encrypt-notify'], bytes(size), report)
                    chars.append(char)
                    self.reports[(adapter, report)] = char
                interfaces = [service, *chars, *[Descriptor(c, c.report) for c in chars if c.report]]
                self.bus.export(root, Application(interfaces))
                for interface in interfaces: self.bus.export(interface.path, interface)
                advertisement = Advertisement(root + '/advertisement', lambda a=adapter, p=root+'/advertisement': self.advertisement_released(a,p))
                self.bus.export(advertisement.path, advertisement)
                intro = await self.bus.introspect('org.bluez', adapter)
                proxy = self.bus.get_proxy_object('org.bluez', adapter, intro)
                gatt = proxy.get_interface('org.bluez.GattManager1')
                adv = proxy.get_interface('org.bluez.LEAdvertisingManager1')
                await gatt.call_register_application(root, {})
                self.registrations.append((gatt, adv, root, None))
                await adv.call_register_advertisement(advertisement.path, {})
                self.registrations[-1] = (gatt, adv, root, advertisement.path)
                props = proxy.get_interface('org.freedesktop.DBus.Properties')
                # Pairing is handled by the desktop/bluetoothctl owner agent, never auto-accepted.
                await props.call_set('org.bluez.Adapter1', 'Pairable', Variant('b', True))
        except Exception as error:
            raise OSError(f'BlueZ HID startup failed: {error}. Install/start bluez and dbus, allow this user to call org.bluez, and run bluetoothctl agent KeyboardDisplay; default-agent for owner pairing') from error

    def advertisement_released(self, adapter, path):
        if self.stopping: return
        task=asyncio.create_task(self.restore_advertisement(adapter,path))
        self.reconnect_tasks.add(task); task.add_done_callback(self.reconnect_tasks.discard)

    async def restore_advertisement(self, adapter, path):
        # BlueZ retains bond keys. Re-register the same connectable advert after a release;
        # do not RemoveDevice, replace its identity or initiate pairing on behalf of the owner.
        while not self.stopping:
            await asyncio.sleep(3)
            try:
                intro=await self.bus.introspect('org.bluez',adapter)
                adv=self.bus.get_proxy_object('org.bluez',adapter,intro).get_interface('org.bluez.LEAdvertisingManager1')
                await adv.call_register_advertisement(path,{})
                return
            except DBusError as error:
                if error.type=='org.bluez.Error.AlreadyExists': return
            except OSError: pass

    async def connected(self):
        objects = await self.manager.call_get_managed_objects()
        self.peer_names = {}
        for path, values in objects.items():
            data=values.get('org.bluez.Device1')
            if data is None: continue
            alias=data.get('Alias'); name=data.get('Name')
            self.peer_names[path]=specific(alias.value if alias else None) or specific(name.value if name else None) or 'iPhone'
        return [path for path, values in objects.items() if 'org.bluez.Device1' in values
                and values['org.bluez.Device1'].get('Connected', Variant('b', False)).value
                and values['org.bluez.Device1'].get('Paired', Variant('b', False)).value]

    async def get_peers(self):
        connected = [p for p in await self.connected() if p in self.seen_peers or any(p in c.channels for c in self.reports.values())]
        result = []
        for peer in connected:
            adapter = peer.split('/dev_')[0]
            reports = [r for r in (1, 2) if (adapter, r) in self.reports and
                       (peer in self.reports[(adapter, r)].channels or self.reports[(adapter, r)].notifying)]
            if reports:
                name = getattr(self, 'peer_names', {}).get(peer)
                result.append({'id': peer, 'reports': reports, 'name': name or f'iPhone ({peer[-4:]})',
                               'name_source': 'bluez-device-name' if name else 'fallback'})
        return result

    async def notify(self, peer, report, payload):
        if (report, len(payload)) not in ((1, 8), (2, 4)): raise ValueError('Invalid HID report')
        connected = await self.connected()
        if peer not in connected: raise OSError('BlueZ peer disconnected/unpaired')
        adapter = peer.split('/dev_')[0]
        char = self.reports.get((adapter, report))
        if not char: raise OSError('HID report service is missing')
        channel = char.channels.get(peer)
        if channel:
            try:
                # A seqpacket write preserves each HID report boundary.
                await asyncio.wait_for(self.loop.sock_sendall(channel, payload), 3)
            except (OSError, asyncio.TimeoutError) as error:
                raise OSError('Per-peer BlueZ notify channel closed/backpressured') from error
        else:
            same_adapter = [p for p in connected if p.startswith(adapter + '/dev_')]
            if not char.notifying: raise OSError('iPhone has not subscribed to this HID report')
            if same_adapter != [peer] or char.channels:
                raise OSError('This BlueZ version uses shared notifications: use one paired phone per BLE adapter, or a BlueZ build supporting per-device AcquireNotify; refusing broadcast')
            char.value = payload
            char.emit_properties_changed({'Value': payload})
        char.peer_values[peer] = bytes(payload)

    async def shutdown(self):
        self.stopping = True
        for task in self.reconnect_tasks: task.cancel()
        if self.reconnect_tasks: await asyncio.gather(*self.reconnect_tasks, return_exceptions=True)
        for char in self.reports.values():
            for channel in list(char.channels.values()):
                self.loop.remove_reader(channel.fileno()); channel.close()
            char.channels.clear()
        for gatt, adv, root, advertisement in reversed(self.registrations):
            if advertisement:
                try: await adv.call_unregister_advertisement(advertisement)
                except Exception: pass  # BlueZ may already have removed it on disconnect.
            try: await gatt.call_unregister_application(root)
            except Exception: pass
        if self.bus:
            if hasattr(self,'agent_manager'):
                try: await self.agent_manager.call_unregister_agent('/dev/openiphoneuse/agent')
                except Exception: pass
            self.bus.disconnect()
