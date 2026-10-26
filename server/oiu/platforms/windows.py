"""Windows WinRT encrypted HID GATT server, with targeted client notifications.

Requires Windows 10 1703+ / Windows 11 and a peripheral-capable Bluetooth driver.
Uses PyWinRT's native projections rather than a simulated or BLE-central implementation.
"""
import asyncio
import uuid as uuids
from winrt.windows.devices.bluetooth import BluetoothAdapter, BluetoothError, BluetoothLEDevice
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider, GattLocalCharacteristicParameters, GattLocalDescriptorParameters,
    GattCharacteristicProperties, GattProtectionLevel, GattServiceProviderAdvertisingParameters,
    GattCommunicationStatus, GattWriteOption, GattServiceProviderAdvertisementStatus,
)
from winrt.windows.storage.streams import DataReader, DataWriter

from ..hid import HID_UUID, REPORT_MAP, uuid
from ..names import specific
from .runtime import AsyncRadio


def buffer(data):
    writer = DataWriter()
    try:
        writer.write_bytes(data)
        return writer.detach_buffer()
    finally:
        writer.close()


class WindowsRadio(AsyncRadio):
    def __init__(self):
        self.provider = None
        self.reports = {}
        self.values = {}
        self.report_values = {}
        self.events = []
        self.error = None
        self.tasks = set()
        self.peer_names = {}
        self.name_pending = set()
        self.advertising = None
        self.stopping = False
        super().__init__()

    def _run_loop(self):
        from winrt.runtime import init_apartment, uninit_apartment, ApartmentType
        init_apartment(ApartmentType.MULTI_THREADED)
        try: super()._run_loop()
        finally: uninit_apartment()

    def _event(self, coroutine, deferral):
        # WinRT callbacks may originate on RPC threads; only the owned MTA loop runs work.
        async def complete():
            try: await coroutine
            except Exception as error: self.error = f'GATT request failed: {error}'
            finally: deferral.complete()
        asyncio.run_coroutine_threadsafe(complete(), self.loop)

    async def start(self):
        try:
            adapter = await BluetoothAdapter.get_default_async()
            if adapter is None: raise OSError('No Bluetooth adapter found')
            if not adapter.is_peripheral_role_supported:
                raise OSError('Bluetooth driver does not support LE peripheral role; update the driver or use a supported USB adapter')
            result = await GattServiceProvider.create_async(uuids.UUID(HID_UUID))
            if result.error != BluetoothError.SUCCESS:
                raise OSError(f'HID service creation returned {result.error}; enable Bluetooth and allow Bluetooth access. DisabledByPolicy means Windows blocked service publication; check OS service restrictions, permissions and policy')
            self.provider = result.service_provider
            self.events.append((self.provider, 'advertisement_status_changed', self.provider.add_advertisement_status_changed(self._advertisement)))
            async def characteristic(short, properties, value=None, report=0):
                parameters = GattLocalCharacteristicParameters()
                parameters.characteristic_properties = properties
                parameters.read_protection_level = GattProtectionLevel.ENCRYPTION_REQUIRED
                parameters.write_protection_level = GattProtectionLevel.ENCRYPTION_REQUIRED
                if value is not None: parameters.static_value = buffer(value)
                result = await self.provider.service.create_characteristic_async(uuids.UUID(uuid(short)), parameters)
                if result.error != BluetoothError.SUCCESS: raise OSError(f'Characteristic {short} failed: {result.error}')
                char = result.characteristic
                if report:
                    self.reports[report] = char
                    self.values[id(char)] = bytes(8 if report == 1 else 4)
                    self.events.append((char, 'read_requested', char.add_read_requested(self._read)))
                    self.events.append((char, 'subscribed_clients_changed', char.add_subscribed_clients_changed(self._subscribers)))
                    params = GattLocalDescriptorParameters()
                    params.read_protection_level = GattProtectionLevel.ENCRYPTION_REQUIRED
                    params.static_value = buffer(bytes([report, 1]))
                    descriptor = await char.create_descriptor_async(uuids.UUID(uuid('2908')), params)
                    if descriptor.error != BluetoothError.SUCCESS: raise OSError(f'Report reference {report} failed: {descriptor.error}')
                elif short in ('2a4c', '2a4e'):
                    self.values[id(char)] = b'\x01' if short == '2a4e' else b'\0'
                    self.events.append((char, 'write_requested', char.add_write_requested(self._write)))
                    if short == '2a4e':
                        self.events.append((char, 'read_requested', char.add_read_requested(self._read)))
                return char
            prop = GattCharacteristicProperties
            await characteristic('2a4a', prop.READ, b'\x11\x01\0\x02')
            await characteristic('2a4b', prop.READ, REPORT_MAP)
            self.control = await characteristic('2a4c', prop.WRITE_WITHOUT_RESPONSE)
            self.mode = await characteristic('2a4e', prop.READ | prop.WRITE_WITHOUT_RESPONSE)
            await characteristic('2a4d', prop.READ | prop.NOTIFY, report=1)
            await characteristic('2a4d', prop.READ | prop.NOTIFY, report=2)
            advertising = GattServiceProviderAdvertisingParameters()
            advertising.is_connectable = True
            advertising.is_discoverable = True
            self.advertising = advertising
            self.provider.start_advertising(advertising)
            deadline = self.loop.time() + 10
            while self.provider.advertisement_status not in (GattServiceProviderAdvertisementStatus.STARTED,
                                                             GattServiceProviderAdvertisementStatus.STARTED_WITHOUT_ALL_ADVERTISEMENT_DATA,
                                                             GattServiceProviderAdvertisementStatus.ABORTED):
                if self.loop.time() > deadline: raise OSError('Windows Bluetooth advertising timed out')
                await asyncio.sleep(.05)
            if self.provider.advertisement_status == GattServiceProviderAdvertisementStatus.ABORTED:
                raise OSError(self.error or 'Windows aborted HID advertising')
            task=asyncio.create_task(self.keep_advertising())
            self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        except Exception as error:
            raise OSError(f'Windows HID startup failed: {error}. Check Settings > Bluetooth, privacy permissions, driver peripheral support and enterprise Bluetooth policy') from error

    async def keep_advertising(self):
        # The OS owns bonds and the local BLE identity. Use the same provider for retries.
        while not self.stopping:
            await asyncio.sleep(3)
            if self.stopping or self.provider is None: return
            status=self.provider.advertisement_status
            if status in (GattServiceProviderAdvertisementStatus.STOPPED, GattServiceProviderAdvertisementStatus.ABORTED):
                try: self.provider.start_advertising(self.advertising)
                except OSError as error: self.error=f'Bluetooth advertising retry failed: {error}'

    def _advertisement(self, sender, args):
        def update():
            if args.error != BluetoothError.SUCCESS: self.error = f'Bluetooth advertising failed: {args.error}'
            elif args.status in (GattServiceProviderAdvertisementStatus.STARTED,
                                 GattServiceProviderAdvertisementStatus.STARTED_WITHOUT_ALL_ADVERTISEMENT_DATA): self.error=None
        self.loop.call_soon_threadsafe(update)

    def _read(self, sender, args):
        deferral = args.get_deferral()
        async def respond():
            request = await args.get_request_async()
            if request is None: return
            report = next((r for r, char in self.reports.items() if char == sender), None)
            if report:
                peer = args.session.device_id.id
                value = self.report_values.get((peer, report), bytes(8 if report == 1 else 4))
            else: value = self.values.get(id(sender), b'\1')
            if request.offset > len(value): request.respond_with_protocol_error(0x07)
            else: request.respond_with_value(buffer(value[request.offset:]))
        self._event(respond(), deferral)

    def _subscribers(self, sender, args):
        def reset():
            report = next((r for r, char in self.reports.items() if char == sender), None)
            connected = {c.session.device_id.id for c in sender.subscribed_clients}
            for key in list(self.report_values):
                if key[1] == report and key[0] not in connected: self.report_values.pop(key, None)
        self.loop.call_soon_threadsafe(reset)

    def _write(self, sender, args):
        deferral = args.get_deferral()
        async def respond():
            request = await args.get_request_async()
            if request is None: return
            reader = DataReader.from_buffer(request.value)
            try:
                data = bytearray(reader.unconsumed_buffer_length)
                reader.read_bytes(data)
            finally: reader.close()
            valid = request.offset == 0 and len(data) == 1 and ((sender == self.mode and data[0] == 1) or (sender == self.control and data[0] in (0, 1)))
            if not valid:
                if request.option == GattWriteOption.WRITE_WITH_RESPONSE:
                    request.respond_with_protocol_error(0x13)
                return
            self.values[id(sender)] = bytes(data)
            if request.option == GattWriteOption.WRITE_WITH_RESPONSE: request.respond()
        self._event(respond(), deferral)

    async def get_peers(self):
        if self.error: raise OSError(self.error)
        peers = {}
        for report, char in self.reports.items():
            for client in char.subscribed_clients:
                peer = client.session.device_id.id
                peers.setdefault(peer, []).append(report)
        result = []
        for peer, reports in peers.items():
            name = self.peer_names.get(peer)
            if not name and peer not in self.name_pending:
                self.name_pending.add(peer)
                task = asyncio.create_task(self._resolve_name(peer))
                self.tasks.add(task); task.add_done_callback(self.tasks.discard)
            result.append({'id': peer, 'reports': sorted(reports), 'name': name or f'iPhone ({peer[-4:]})',
                           'name_source': 'winrt-device-name' if name else 'fallback'})
        return result

    async def _resolve_name(self, peer):
        device = None
        try:
            device = await asyncio.wait_for(BluetoothLEDevice.from_id_async(peer), 1)
            name = (specific(device.device_information.name) or specific(device.name)) if device else None
            if name: self.peer_names[peer] = name
        except (OSError, asyncio.TimeoutError): pass
        finally:
            self.name_pending.discard(peer)
            if device: device.close()

    async def notify(self, peer, report, payload):
        if (report, len(payload)) not in ((1, 8), (2, 4)): raise ValueError('Invalid HID payload')
        char = self.reports[report]
        client = next((c for c in char.subscribed_clients if c.session.device_id.id == peer), None)
        if client is None: raise OSError('iPhone disconnected or has not subscribed to this HID report')
        if len(payload) > client.max_notification_size: raise OSError('Bluetooth MTU too small for HID report')
        # Explicit client overload only; never use the broadcast NotifyValueAsync overload.
        result = await char.notify_value_async(buffer(payload), client)
        if result.status != GattCommunicationStatus.SUCCESS:
            raise OSError(f'Windows targeted HID notification failed: {result.status}')
        self.report_values[(peer, report)] = bytes(payload)

    async def shutdown(self):
        self.stopping = True
        for task in list(self.tasks): task.cancel()
        if self.tasks: await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.provider: self.provider.stop_advertising()
        for obj, event, token in reversed(self.events):
            getattr(obj, 'remove_' + event)(token)
        self.events.clear()
        self.reports.clear()
        self.provider = None
