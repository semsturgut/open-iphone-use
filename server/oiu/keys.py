"""US-layout key strokes and chords as (modifier bits, HID keyboard usage)."""
import math


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
                up=0x52, down=0x51, left=0x50, right=0x4f, end=0x4d, pageup=0x4b, pagedown=0x4e)
    alias = {'return': 'enter', 'ret': 'enter', 'esc': 'escape', 'del': 'delete', 'bksp': 'backspace',
             'spacebar': 'space', 'pgup': 'pageup', 'pgdn': 'pagedown', 'arrowup': 'up', 'arrowdown': 'down',
             'arrowleft': 'left', 'arrowright': 'right'}
    key = alias.get(parts[-1], parts[-1])
    if key in keys: return modifier, keys[key]
    if len(key) == 1:
        extra, usage = stroke(key)
        return modifier | extra, usage
    raise ValueError(f'Unknown key: {key}. Accepted: return/enter, tab, esc/escape, del/delete, bksp/backspace, space, pgup/pageup, pgdn/pagedown, up/down/left/right, cmd/ctrl/shift/alt+<key>, a single character, or a button: home, lock, volume_up, volume_down, mute, siri')


def fraction(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Coordinates must be finite screen fractions 0…1')
    return float(value)
