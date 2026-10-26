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
from PIL import Image

cli=str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='oiu-smoke-') as directory:
    state=Path(directory)
    env=dict(os.environ)
    env.pop('OIU_DISABLE_BLUETOOTH',None)
    env.pop('OIU_MAC_HELPER',None)
    process=subprocess.Popen([cli,'--state',directory,'serve','--port','0','--ui-port','0',
                              '--certificate-port','0','--no-print-secrets'],
                             env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=process.stdout.readline().strip()
        assert line.startswith('Open iPhone Use: https://'),(line,process.stderr.read() if process.poll() is not None else '')
        public=line.split('Open iPhone Use: ')[1]
        connection=json.loads((state/'connection.json').read_text())
        saved=json.loads((state/'state.json').read_text())
        context=ssl.create_default_context(cafile=str(state/'certificates/root-ca.pem'))
        with urlopen(public+'/',context=context,timeout=5) as response: assert b'Open iPhone Use' in response.read()
        def api(path,body=None,headers=None):
            auth={'Authorization':'Bearer '+saved['admin_token']}
            auth.update(headers or {})
            with urlopen(Request(connection['url']+path,data=body,headers=auth),timeout=5) as response: return json.load(response)
        setup=api('/api/setup')
        assert setup['capture_origin']==public
        for _ in range(2):
            enrolled=api('/api/enroll',b'{}',{'X-Open-iPhone-Use-Invite':saved['invite_token']})
            token=enrolled['capture_path'].split('token=')[1]
            output=BytesIO(); Image.new('RGB',(360,640),(30,80,120)).save(output,format='JPEG')
            with urlopen(Request(public+'/api/frame',data=output.getvalue(),headers={'X-Open-iPhone-Use-Token':token}),context=context,timeout=5) as response:
                assert json.load(response)['frameID']
        devices=api('/api/devices')
        assert len(devices)==2 and all(d['capture_active'] for d in devices)
        result=subprocess.run([cli,'--state',directory,'devices'],capture_output=True,text=True,check=True)
        assert len(json.loads(result.stdout))==2
        if sys.platform=='darwin':
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(3);channel.connect(str(state/'radio/bridge.sock'))
                channel.sendall(b'{"requestID":"smoke-status","operation":"radio_status","deviceID":"iphone"}\n')
                with channel.makefile('rb') as reader: status=json.loads(json.loads(reader.readline())['message'])
                print('Native macOS radio state:',status['state'],'; subscribed peers:',len(status['peers']))
        print('PASS installed serve: automatic native backend, CA/TLS, localhost grid/API, independent enrollments/uploads and CLI devices. No physical input was sent.')
    finally:
        process.terminate()
        try: process.wait(8)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        process.stdout.close();process.stderr.close()
