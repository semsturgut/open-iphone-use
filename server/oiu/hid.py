"""HOGP descriptor and validated US keyboard / relative pointer report encoding."""
import math

HID_UUID = '00001812-0000-1000-8000-00805f9b34fb'
REPORT_MAP = bytes.fromhex('''
05 01 09 06 a1 01 85 01 05 07 19 e0 29 e7 15 00 25 01
75 01 95 08 81 02 95 01 75 08 81 01 95 06 75 08 15 00
25 65 05 07 19 00 29 65 81 00 c0 05 01 09 02 a1 01
85 02 09 01 a1 00 05 09 19 01 29 03 15 00 25 01 95 03
75 01 81 02 95 01 75 05 81 01 05 01 09 30 09 31 09 38
15 81 25 7f 75 08 95 03 81 06 c0 c0''')


def uuid(short):
    return f'0000{short.lower()}-0000-1000-8000-00805f9b34fb'


def mouse(dx=0, dy=0, wheel=0, buttons=0):
    if any(type(v) is not int or not -127 <= v <= 127 for v in (dx, dy, wheel)) or buttons not in range(8):
        raise ValueError('HID movement must be integer -127…127; buttons must be 0…7')
    return bytes([buttons, dx & 255, dy & 255, wheel & 255])


def keyboard(modifier=0, usage=0):
    return bytes([modifier, 0, usage, 0, 0, 0, 0, 0])


def stroke(character):
    if len(character) != 1 or ord(character) > 127:
        raise ValueError('Typing supports US-layout ASCII only')
    if 'a' <= character <= 'z': return 0, ord(character) - 93
    if 'A' <= character <= 'Z': return 2, ord(character) - 61
    if '1' <= character <= '9': return 0, ord(character) - 19
    plain = {'0': 0x27, ' ': 0x2c, '\n': 0x28, '\r': 0x28, '\t': 0x2b, '\b': 0x2a}
    if character in plain: return 0, plain[character]
    for low, high, usage in [('-', '_', 0x2d), ('=', '+', 0x2e), ('[', '{', 0x2f), (']', '}', 0x30),
                             ('\\', '|', 0x31), (';', ':', 0x33), ("'", '"', 0x34), ('`', '~', 0x35),
                             (',', '<', 0x36), ('.', '>', 0x37), ('/', '?', 0x38)]:
        if character in (low, high): return (2 if character == high else 0), usage
    if character in '!@#$%^&*()':
        index = '!@#$%^&*()'.index(character)
        return 2, 0x1e + index if index < 9 else 0x27
    raise ValueError(f'Unsupported ASCII character U+{ord(character):04X}')


def chord(value):
    if not isinstance(value, str): raise ValueError('Key must be a string')
    parts = value.lower().split('+')
    modifier = 0
    modifiers = dict(ctrl=1, control=1, shift=2, alt=4, option=4, opt=4, cmd=8, command=8, gui=8, super=8)
    for part in parts[:-1]:
        if part not in modifiers: raise ValueError(f'Unknown key modifier: {part}')
        modifier |= modifiers[part]
    keys = dict(enter=0x28, tab=0x2b, escape=0x29, backspace=0x2a, delete=0x4c, space=0x2c,
                up=0x52, down=0x51, left=0x50, right=0x4f, home=0x4a, end=0x4d, pageup=0x4b, pagedown=0x4e)
    alias = {'return': 'enter', 'ret': 'enter', 'esc': 'escape', 'escape': 'escape', 'del': 'delete', 'bksp': 'backspace',
             'spacebar': 'space', 'pgup': 'pageup', 'pgdn': 'pagedown', 'arrowup': 'up', 'arrowdown': 'down',
             'arrowleft': 'left', 'arrowright': 'right'}
    key = alias.get(parts[-1], parts[-1])
    if key in keys: return keyboard(modifier, keys[key])
    if len(key) == 1:
        extra, usage = stroke(key)
        return keyboard(modifier | extra, usage)
    raise ValueError(f'Unknown key: {key}. Accepted: return/enter, tab, esc/escape, del/delete, bksp/backspace, space, pgup/pageup, pgdn/pagedown, up/down/left/right, arrowup/arrowdown/arrowleft/arrowright, cmd/ctrl/shift/alt/cmd+<key>, or a single character')


def fraction(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Coordinates must be finite screen fractions 0…1')
    return float(value)
