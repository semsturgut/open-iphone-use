"""Bluetooth-first lifecycle, daemon control and independent terminal QR checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import re
import signal
from oiu.qr import matrix, terminal
from oiu.state import StateLease


def start_with_diagnostics(command, directory, timeout=float(os.environ.get('OIU_TEST_DAEMON_TIMEOUT', '90'))):
    """Start the daemon; on a hang, dump Python stacks of the CLI and daemon before failing."""
    env = dict(os.environ, PYTHONFAULTHANDLER='1')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    began = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            subprocess.run(['pkill', '-ABRT', '-f', directory], capture_output=True)
            process.send_signal(signal.SIGABRT)
        stdout, stderr = process.communicate(timeout=10)
        log = Path(directory)/'server.log'
        text = log.read_text(errors='replace') if log.exists() else '(no server.log)'
        text = re.sub(r'token=[0-9A-Za-z_-]+', 'token=<redacted>', text)
        raise AssertionError(f'serve did not return within {timeout}s\nCLI stdout:\n{stdout}\nCLI stderr:\n{stderr}\nserver.log:\n{text}')
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
    print(f'daemon start took {time.monotonic()-began:.2f}s', file=sys.stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class HeadlessWorkflowTests(unittest.TestCase):
    def test_real_daemon_starts_persists_and_stops_without_pid_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            base = [sys.executable,'-m','oiu.cli','--state',directory]
            try:
                started = start_with_diagnostics(base+['serve','--local-http','--capture-only','--port','0'],directory)
                self.assertIn('Open iPhone Use is running',started.stdout)
                self.assertNotIn('#',started.stdout)
                self.assertNotIn('token',started.stdout.lower())
                self.assertIn('Log:',started.stdout)
                log=Path(directory)/'server.log'
                self.assertEqual(log.stat().st_mode & 0o777,0o600)
                duplicate=subprocess.run(base+['serve','--local-http','--capture-only','--port','0'],
                                         capture_output=True,text=True,timeout=15)
                self.assertNotEqual(duplicate.returncode,0)
                listed=subprocess.run(base+['devices'],check=True,capture_output=True,text=True)
                self.assertEqual(json.loads(listed.stdout),[])
                no_bt=subprocess.run(base+['new'],capture_output=True,text=True)
                self.assertNotEqual(no_bt.returncode,0)
                self.assertIn('Bluetooth',no_bt.stderr)
                stopped=subprocess.run(base+['stop'],check=True,capture_output=True,text=True,timeout=15)
                self.assertTrue(json.loads(stopped.stdout)['ok'])
                deadline=time.monotonic()+5
                while True:
                    try:
                        lease=StateLease(directory);lease.close();break
                    except ValueError:
                        if time.monotonic()>=deadline: raise
                        time.sleep(.05)
                token=json.loads((Path(directory)/'state.json').read_text())['admin_token']
                self.assertNotIn(token,log.read_text())
            finally:
                subprocess.run(base+['stop'],capture_output=True,text=True,timeout=10)
