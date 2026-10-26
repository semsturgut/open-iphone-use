"""Bluetooth-first lifecycle, daemon control and independent terminal QR checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from oiu.qr import matrix, terminal
from oiu.state import StateLease


class HeadlessWorkflowTests(unittest.TestCase):
    def test_terminal_qr_has_quiet_zone_and_reproducible_modules(self):
        url = 'https://192.168.1.2:27891/capture?token=' + 'a'*64
        rows = matrix(url)
        self.assertTrue(all(len(row) == len(rows) for row in rows))
        self.assertTrue(all(not any(row) for row in rows[:4] + rows[-4:]))
        self.assertTrue(all(not any(row[:4] + row[-4:]) for row in rows))
        self.assertEqual(rows[4][4:11], [True]*7)  # Finder border, independently of terminal renderer.
        self.assertEqual(terminal(url, ansi=False).count('\n'), len(rows)-1)
        self.assertIn('\x1b[30;47m', terminal(url))
        self.assertEqual(matrix(url), rows)
        self.assertNotEqual(matrix(url.replace('aaaa','bbbb',1)), rows)

    def test_real_daemon_starts_persists_and_stops_without_pid_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            base = [sys.executable,'-m','oiu.cli','--state',directory]
            try:
                started = subprocess.run(base+['serve','--daemon','--local-http','--capture-only','--port','0'],
                                         check=True,capture_output=True,text=True,timeout=15)
                result=json.loads(started.stdout)
                self.assertTrue(result['ok'])
                self.assertEqual(Path(result['log']).stat().st_mode & 0o777,0o600)
                duplicate=subprocess.run(base+['serve','--daemon','--local-http','--capture-only','--port','0'],
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
                self.assertNotIn(token,Path(result['log']).read_text())
            finally:
                subprocess.run(base+['stop'],capture_output=True,text=True,timeout=10)
