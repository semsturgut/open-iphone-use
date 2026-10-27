"""Exercise two real Swift helpers without advertising Bluetooth or touching a phone."""
import os
from pathlib import Path
import sys
import subprocess
import time
import tempfile
from oiu.backends import MacBackend
from oiu.core import Device

os.environ['OIU_DISABLE_BLUETOOTH'] = '1'
executable = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='oiu-') as directory:
    a = Device('a', 'Synthetic A', 'test-a')
    b = Device('b', 'Synthetic B', 'test-b')
    try:
        a.backend = MacBackend(executable, Path(directory) / 'a', 'smoke-a')
        b.backend = MacBackend(executable, Path(directory) / 'b', 'smoke-b')
        a.upload(b'\xff\xd8\xffsynthetic-a')
        b.upload(b'\xff\xd8\xffsynthetic-b')
        fa = a.backend.request('get_latest_screenshot')['frame']
        fb = b.backend.request('get_latest_screenshot')['frame']
        assert fa['frameID'] != fb['frameID']
        assert fa['frameID'] == a.frame_id
        assert fb['frameID'] == b.frame_id
        assert fa['base64'] != fb['base64']
        assert a.backend.request('capture_tap', x=.2, y=.3)['ok']
        assert a.backend.request('get_device_status')['status']['captureActive']
        refused = a.action('click', {'expectedFrameID': a.frame_id})
        assert not refused['ok'] and 'Pairing identity mismatch' in refused['message']
        a.stop()
        assert not a.backend.request('get_latest_screenshot')['frame']['captureActive']
        assert b.backend.request('get_latest_screenshot')['frame']['captureActive']
        a.backend.close()
        a.backend = MacBackend(executable, Path(directory) / 'a', 'smoke-a')
        assert a.backend.request('headless_identity')['message'] == str(a.backend.process.pid)
        # A terminated server must not leave an advertising helper alive indefinitely.
        code = """from oiu.backends import MacBackend
import os, sys
backend = MacBackend(sys.argv[1], sys.argv[2], 'orphan-test')
print(backend.process.pid, flush=True)
os._exit(0)
"""
        child = subprocess.run([sys.executable, '-c', code, executable, str(Path(directory)/'orphan')],
                               check=True, capture_output=True, text=True)
        pid = int(child.stdout.strip())
        gone = False
        for _ in range(80):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(.05)
        if not gone:
            os.kill(pid, 15)
        assert gone, 'Native helper did not stop after parent exit'
        print('Two native headless helpers: independent frame IDs, JPEGs, tap ingress, capture stop and unbound-input rejection, restart and parent-exit cleanup passed; Bluetooth disabled.')
    finally:
        a.backend.close()
        b.backend.close()
