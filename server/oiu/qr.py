"""Width-aware terminal QR, two vertical modules per Unicode cell, MIT encoder."""
import shutil
import os
from ._vendor.qrcodegen import QrCode


def enable_ansi():
    if os.name!='nt': return True
    # Windows Terminal and modern conhost need virtual-terminal processing enabled.
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetStdHandle.argtypes=[wintypes.DWORD]; kernel.GetStdHandle.restype=wintypes.HANDLE
    kernel.GetConsoleMode.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    handle=kernel.GetStdHandle(-11 & 0xffffffff); mode=wintypes.DWORD()
    return bool(kernel.GetConsoleMode(handle,ctypes.byref(mode)) and kernel.SetConsoleMode(handle,mode.value|4))


def matrix(url):
    qr=QrCode.encode_text(url,QrCode.Ecc.MEDIUM)
    size=qr.get_size()
    return [[0<=x<size and 0<=y<size and qr.get_module(x,y)
             for x in range(-4,size+4)] for y in range(-4,size+4)]


def render(rows,columns,ansi=True):
    width=len(rows)
    # One or two character cells per horizontal module, as available. Each
    # character always carries two vertical modules (top/bottom half-blocks).
    scale=2 if width*2<=columns else 1
    if width>columns:
        return f'Terminal too narrow for QR ({columns} columns; needs {width}). Open or copy the link above.'
    expanded=[[bit for bit in row for _ in range(scale)] for row in rows for _ in range(scale)]
    width*=scale
    lines=[]
    for y in range(0,len(expanded),2):
        top=expanded[y]
        bottom=expanded[y+1] if y+1<len(expanded) else [False]*width
        line=''.join({(False,False):' ',(True,False):'▀',(False,True):'▄',(True,True):'█'}[(bool(a),bool(b))] for a,b in zip(top,bottom))
        # Black foreground and white background on every row, including quiet zone.
        lines.append(('\x1b[30;47m'+line+'\x1b[0m') if ansi else line)
    return '\n'.join(lines)


def terminal(url,ansi=True,columns=None):
    return render(matrix(url),columns if columns is not None else shutil.get_terminal_size(fallback=(80,24)).columns,ansi)
