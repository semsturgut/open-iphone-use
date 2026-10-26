"""Terminal QR codes, using the vendored MIT Project Nayuki encoder (no runtime deps)."""
from ._vendor.qrcodegen import QrCode


def matrix(url):
    qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    return [[0 <= x < size and 0 <= y < size and qr.get_module(x, y)
             for x in range(-4, size + 4)] for y in range(-4, size + 4)]


def terminal(url, ansi=True):
    rows = matrix(url)
    # Black on white explicitly, rather than depending on light/dark terminal themes.
    if ansi:
        return '\n'.join('\x1b[30;47m' + ''.join('██' if bit else '  ' for bit in row) + '\x1b[0m' for row in rows)
    return '\n'.join(''.join('██' if bit else '  ' for bit in row) for row in rows)
