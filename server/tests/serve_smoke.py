"""Installed CLI, real native radio startup + automatic HTTPS. Never sends input reports.

Uses synthetic JPEGs and a temporary state directory; no app bundle, backend JSON,
explicit helper argument, disabled Bluetooth flag or owner device credentials are used.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from PIL import Image

cli=str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='oiu-smoke-') as directory:
    state=Path(directory)
    env=dict(os.environ)
    env.pop('OIU_DISABLE_BLUETOOTH',None)
    env.pop('OIU_MAC_HELPER',None)
    process=subprocess.Popen([cli,'--state',directory,'serve','--foreground','--port','0','--ui-port','0',
                              '--certificate-port','0','--no-print-secrets'],
                             env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=process.stdout.readline().strip()
        assert line=='Open iPhone Use is running',line
        connection=json.loads((state/'connection.json').read_text())
        public=connection['public_url']
        saved=json.loads((state/'state.json').read_text())
        context=ssl.create_default_context(cafile=str(state/'certificates/root-ca.pem'))
        with urlopen(connection['url']+'/',timeout=5) as response: assert b'Open iPhone Use' in response.read()
        try: urlopen(public+'/',context=context,timeout=5)
        except HTTPError as error: assert error.code==403;error.close()
        else: raise AssertionError('Operator UI must not be exposed on capture HTTPS')
        def api(path,body=None,headers=None):
            auth={}
            auth.update(headers or {})
            with urlopen(Request(connection['url']+path,data=body,headers=auth),timeout=5) as response: return json.load(response)
        setup=api('/api/setup')
        assert setup['capture_origin']==public
        # Bluetooth-first enrollment must not create fake phones when no hardware peers exist.
        try: candidates=api('/api/bluetooth')
        except HTTPError as error:
            assert error.code==400;error.close();candidates=[]
        try: api('/api/devices',b'{"name":"unbound"}')
        except HTTPError as error: assert error.code==400;error.close()
        else: raise AssertionError('Free-text enrollment must be rejected')
        try: api('/api/enroll',b'{}')
        except HTTPError as error: assert error.code==410;error.close()
        else: raise AssertionError('Legacy invitation enrollment must be rejected')
        assert api('/api/devices')==[]
        result=subprocess.run([cli,'--state',directory,'devices'],capture_output=True,text=True,check=True)
        assert json.loads(result.stdout)==[]
        if sys.platform=='darwin':
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(3);channel.connect(str(state/'radio/bridge.sock'))
                channel.sendall(b'{"requestID":"smoke-status","operation":"radio_status","deviceID":"iphone"}\n')
                with channel.makefile('rb') as reader: status=json.loads(json.loads(reader.readline())['message'])
                print('Native macOS radio state:',status['state'],'; subscribed peers:',len(status['peers']))
        print('PASS installed serve: automatic native backend, CA/TLS, localhost grid/API, Bluetooth-first enrollment gate and CLI devices. No physical input was sent.')
    finally:
        process.terminate()
        try: process.wait(8)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        process.stdout.close();process.stderr.close()
