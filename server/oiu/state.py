"""Single writer lease using OS locks, released automatically on process exit."""
import os
from pathlib import Path


class StateLease:
    def __init__(self, directory):
        path = Path(directory)
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.file = open(path / 'server.lock', 'a+b')
        os.chmod(path / 'server.lock', 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('Another server already owns this state directory') from None

    def close(self):
        self.file.close()
