"""An owned asyncio loop for native APIs, with bounded cross-thread calls and shutdown."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import threading


class AsyncRadio:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name=type(self).__name__, daemon=True)
        self.thread.start()
        self.closed = False
        try:
            self.call(self.start(), timeout=20)
        except BaseException:
            self.close()
            raise

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending: task.cancel()
        if pending: self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def call(self, coroutine, timeout=5):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except FutureTimeout:
            future.cancel()
            raise OSError('Native Bluetooth API timed out; check the radio, permissions and subscriptions') from None

    def peers(self): return self.call(self.get_peers())
    def send(self, peer, report, payload): return self.call(self.notify(peer, report, payload))

    def release(self, peer):
        for report, size in ((1, 8), (2, 4)):
            try: self.send(peer, report, bytes(size))
            except (OSError, ValueError): pass

    def close(self):
        if self.closed: return
        self.closed = True
        try: self.call(self.shutdown(), timeout=5)
        except (OSError, RuntimeError): pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(6)
