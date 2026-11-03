"""Installed CLI: start the server, read the Web UI and candidate list, stop. Never adds a phone
or sends input. Usage: python serve_smoke.py /path/to/open-iphone-use"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

cli = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='oiu-smoke-') as directory:
    process = subprocess.Popen([cli, '--state', directory, 'serve', '--foreground', '--port', '0'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'Open iPhone Use is running'
        url = process.stdout.readline().strip().split('Web UI:  ')[1]
        with urlopen(url + '/', timeout=5) as response: assert b'Open iPhone Use' in response.read()
        with urlopen(url + '/api/devices', timeout=5) as response: assert json.load(response) == []
        with urlopen(url + '/api/candidates', timeout=30) as response: candidates = json.load(response)
        print(f'Connected phones visible to this Mac: {len(candidates)}')
        listed = subprocess.run([cli, '--state', directory, 'devices'], check=True, capture_output=True, text=True)
        assert json.loads(listed.stdout) == []
        print('PASS installed serve: Web UI, device API, candidate listing and CLI. No phone was added.')
    finally:
        process.terminate()
        process.wait(10)
