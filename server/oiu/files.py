"""Owner-private files written atomically."""
import json
import os


def private_file(path, data):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as file: file.write(data)
    os.replace(temporary, path)


def atomic_json(path, value):
    private_file(path, json.dumps(value).encode())
