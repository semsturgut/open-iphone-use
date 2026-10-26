"""Owner-confirmed BlueZ pairing agent; no automatic trust or numeric comparison approval."""
import asyncio
import sys
from dbus_fast import DBusError
from dbus_fast.service import ServiceInterface,method


class PairingAgent(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.Agent1')
        self.lock=asyncio.Lock()
        self.pending=None

    async def ask(self,message):
        async with self.lock:
            try: terminal=open('/dev/tty','r')
            except OSError: raise DBusError('org.bluez.Error.Rejected','Pairing needs an interactive owner terminal or the OS Bluetooth pairing UI') from None
            loop=asyncio.get_running_loop()
            future=loop.create_future(); self.pending=future
            print(message, file=sys.stderr,flush=True)
            def read():
                if not future.done(): future.set_result(terminal.readline().strip())
            loop.add_reader(terminal.fileno(),read)
            try: return await asyncio.wait_for(future,60)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                raise DBusError('org.bluez.Error.Rejected','Owner did not approve pairing') from None
            finally:
                loop.remove_reader(terminal.fileno()); terminal.close(); self.pending=None

    @method()
    def Release(self): self.Cancel()
    @method()
    def Cancel(self):
        if self.pending and not self.pending.done(): self.pending.cancel()
    @method()
    async def RequestPinCode(self,device:'o')->'s':
        pin=await self.ask(f'Bluetooth pairing for {device}: enter the PIN displayed by the phone:')
        if not pin or len(pin)>16: raise DBusError('org.bluez.Error.Rejected','Invalid owner PIN')
        return pin
    @method()
    async def RequestPasskey(self,device:'o')->'u':
        value=await self.ask(f'Bluetooth pairing for {device}: enter the six-digit passkey:')
        if not value.isdigit() or not 0<=int(value)<=999999: raise DBusError('org.bluez.Error.Rejected','Invalid owner passkey')
        return int(value)
    @method()
    def DisplayPinCode(self,device:'o',pincode:'s'):
        print(f'Bluetooth pairing {device}: type PIN {pincode} on your phone',file=sys.stderr,flush=True)
    @method()
    def DisplayPasskey(self,device:'o',passkey:'u',entered:'q'):
        print(f'Bluetooth pairing {device}: {passkey:06d} ({entered} digits entered)',file=sys.stderr,flush=True)
    @method()
    async def RequestConfirmation(self,device:'o',passkey:'u'):
        answer=await self.ask(f'Compare Bluetooth code {passkey:06d} with {device}. Type yes only if it matches:')
        if answer.lower()!='yes': raise DBusError('org.bluez.Error.Rejected','Owner rejected Bluetooth numeric comparison')
    @method()
    async def RequestAuthorization(self,device:'o'):
        if (await self.ask(f'Allow Bluetooth pairing from {device}? Type yes:')).lower()!='yes':
            raise DBusError('org.bluez.Error.Rejected','Owner rejected Bluetooth pairing')
    @method()
    async def AuthorizeService(self,device:'o',uuid:'s'):
        if uuid.lower() not in ('00001812-0000-1000-8000-00805f9b34fb','0000180f-0000-1000-8000-00805f9b34fb'):
            if (await self.ask(f'Allow Bluetooth service {uuid} from {device}? Type yes:')).lower()!='yes':
                raise DBusError('org.bluez.Error.Rejected','Owner rejected Bluetooth service authorization')
