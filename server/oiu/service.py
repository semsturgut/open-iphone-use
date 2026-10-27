"""Detached background server lifecycle, authenticated shutdown, no PID-based killing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .permissions import secure_directory
from .state import StateLease


def daemon(args, argv, call):
    directory = secure_directory(args.state)
    marker = uuid.uuid4().hex
    command = [sys.executable, '-m', 'oiu.cli', *[value for value in argv if value != '--daemon']]
    if '--foreground' not in command: command.append('--foreground')
    options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    log = directory / 'server.log'
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.chmod(log, 0o600)
    with os.fdopen(fd, 'ab') as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                   env=dict(os.environ, OIU_DAEMON_START_ID=marker), **options)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError(f'Server did not start (another server may own this state). Inspect private log: {log}')
        try:
            saved = json.loads((directory/'connection.json').read_text())
            if saved.get('instance_id') == marker:
                health=call(args,'/api/health')
                if health.get('instance_id') == marker:
                    return dict(health,url=saved['url'],pid=process.pid,log=str(log))
        except (OSError, ValueError): pass
        time.sleep(.1)
    process.terminate()
    try: process.wait(10)
    except subprocess.TimeoutExpired: process.kill(); process.wait()
    raise ValueError(f'Server startup timed out; inspect private log: {log}')


def stop(args, call):
    identity = call(args, '/api/health')['instance_id']
    result = call(args, '/api/shutdown', {})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if call(args, '/api/health').get('instance_id') == identity:
                time.sleep(.1)
                continue
        except (OSError, ValueError): pass
        # Listener shutdown precedes registry/radio cleanup. Do not report a
        # completed stop until the old server has released its state lease.
        try:
            lease = StateLease(args.state)
        except ValueError:
            time.sleep(.1)
            continue
        lease.close()
        return result
    raise ValueError('Shutdown requested but server has not stopped yet')
