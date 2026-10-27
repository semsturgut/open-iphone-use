"""Subprocess checks with a reserved, non-listening loopback socket; no live server access."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


class StdioTests(unittest.TestCase):
    def test_server_down_tool_error_keeps_process_alive_then_clean_eof(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as unused:
            unused.bind(('127.0.0.1',0))
            command=[sys.executable,'-m','oiu.cli','--state',directory,'--url',f'http://127.0.0.1:{unused.getsockname()[1]}','mcp']
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                def rpc(id_,method,params={}):
                    process.stdin.write(json.dumps(dict(jsonrpc='2.0',id=id_,method=method,params=params))+'\n'); process.stdin.flush()
                    return json.loads(process.stdout.readline())
                self.assertIn('serverInfo',rpc(1,'initialize')['result'])
                reply=rpc(2,'tools/call',dict(name='list_devices'))['result']
                self.assertTrue(reply['isError'])
                self.assertEqual(reply['content'][0]['text'],'Server not running — start it with: open-iphone-use serve')
                self.assertIsNone(process.poll())
                self.assertEqual(rpc(3,'ping')['result'],{})
                self.assertIn('tools',rpc(4,'tools/list')['result'])
                process.stdin.close(); process.stdin=None
                stdout,stderr=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0)
                self.assertEqual(stdout,''); self.assertEqual(stderr,'')
                log=Path(directory)/'mcp.log'
                self.assertIn('Server connection unavailable',log.read_text())
                if os.name!='nt': self.assertEqual(log.stat().st_mode&0o777,0o600)
            finally:
                if process.poll() is None: process.kill(); process.communicate()

    def test_bad_state_or_request_returns_error_and_ping_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'connection.json').write_text('{ corrupt')
            messages=['{bad', json.dumps(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='list_devices'))),
                      json.dumps(dict(jsonrpc='2.0',id=2,method='ping'))]
            result=subprocess.run([sys.executable,'-m','oiu.cli','--state',directory,'mcp'],input='\n'.join(messages)+'\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0)
            replies=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertIn('error',replies[0]); self.assertTrue(replies[1]['result']['isError'])
            self.assertEqual(replies[2]['result'],{})
            self.assertEqual(result.stderr,'')

    def test_closed_stdout_exits_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            process=subprocess.Popen([sys.executable,'-m','oiu.cli','--state',directory,'mcp'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            process.stdout.close()
            _,error=process.communicate(json.dumps(dict(jsonrpc='2.0',id=1,method='ping')).encode()+b'\n',timeout=5)
            self.assertEqual(process.returncode,0)
            self.assertNotIn(b'Traceback',error)
