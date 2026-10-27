"""Keep credentials owner-private, including Windows ACLs rather than chmod alone."""
import csv
import os
from pathlib import Path
import subprocess


def secure_directory(directory):
    path=Path(directory)
    path.mkdir(parents=True,mode=0o700,exist_ok=True)
    if os.name=='nt':
        result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True)
        sid=next(csv.reader(result.stdout.splitlines()))[1]
        if not sid.startswith('S-1-'): raise OSError('Could not determine Windows user SID for private state ACL')
        result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True,text=True)
        if result.returncode: raise OSError('Could not make device state private with icacls; use an NTFS directory owned by this user')
    else: os.chmod(path,0o700)
    return path
