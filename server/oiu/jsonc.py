"""Small JSONC object editor: preserve comments/spacing outside the changed value.

Uses the JSON decoder for strings and values; no regex-based comment stripping.
"""
import json


class Document:
    def __init__(self, text):
        self.text = text or '{}\n'
        self.spans = {}
        self.ends = {}
        self.clean = list(self.text)
        i = 0
        while i < len(self.text):
            if self.text[i] == '"':
                _, end = json.JSONDecoder().raw_decode(self.text, i)
                i = end
            elif self.text.startswith('//', i) or self.text.startswith('/*', i):
                if self.text.startswith('//', i):
                    end = self.text.find('\n', i)
                    if end == -1: end = len(self.text)
                else:
                    end = self.text.find('*/', i + 2)
                    if end == -1: raise ValueError('Unterminated JSONC comment')
                    end += 2
                for n in range(i, end):
                    if self.clean[n] not in '\r\n': self.clean[n] = ' '
                i = end
            else: i += 1
        self.clean = ''.join(self.clean)
        end = self._value(0, ())
        if self.clean[end:].strip(): raise ValueError('Trailing JSONC content')
        if () not in self.ends: raise ValueError('Configuration must be an object')

    def _space(self, i):
        while i < len(self.clean) and self.clean[i].isspace(): i += 1
        return i

    def _value(self, i, path):
        i = self._space(i)
        start = i
        if i >= len(self.clean): raise ValueError('Incomplete JSONC')
        if self.clean[i] == '{':
            i = self._space(i + 1)
            keys = set()
            while self.clean[i:i+1] != '}':
                key, i = json.JSONDecoder().raw_decode(self.clean, i)
                if not isinstance(key, str) or key in keys: raise ValueError('Invalid/duplicate object key')
                keys.add(key)
                i = self._space(i)
                if self.clean[i:i+1] != ':': raise ValueError('Expected colon')
                i = self._space(self._value(i + 1, path + (key,)))
                if self.clean[i:i+1] == '}': break
                if self.clean[i:i+1] != ',': raise ValueError('Expected comma')
                i = self._space(i + 1)
            self.ends[path] = i
            i += 1
        elif self.clean[i] == '[':
            i = self._space(i + 1)
            index = 0
            while self.clean[i:i+1] != ']':
                i = self._space(self._value(i, path + (index,)))
                index += 1
                if self.clean[i:i+1] == ']': break
                if self.clean[i:i+1] != ',': raise ValueError('Expected array comma')
                i = self._space(i + 1)
            i += 1
        else:
            _, i = json.JSONDecoder().raw_decode(self.clean, i)
        self.spans[path] = (start, i)
        return i

    def value(self, path):
        span = self.spans.get(tuple(path))
        if span is None: return None
        raw = self.clean[span[0]:span[1]]
        # Our parser accepts trailing commas. Remove only punctuation before a closer.
        import re
        raw = re.sub(r'("(?:\\.|[^"\\])*"|,\s*[}\]])',
                     lambda m: m[0] if m[0].startswith('"') else m[0][1:], raw)
        return json.loads(raw)

    def set(self, path, value):
        path = tuple(path)
        if path in self.spans:
            start, end = self.spans[path]
            return self.text[:start] + json.dumps(value, indent=2) + self.text[end:]
        parent = path[:-1]
        if parent not in self.ends:
            if parent in self.spans: raise ValueError('MCP parent must be an object')
            return self.set(parent, {path[-1]: value})
        end = self.ends[parent]
        start = self.spans[parent][0]
        body = self.clean[start+1:end].rstrip()
        comma = ',' if body and not body.endswith(',') else ''
        addition = comma + '\n  ' + json.dumps(path[-1]) + ': ' + json.dumps(value, indent=2) + '\n'
        return self.text[:end] + addition + self.text[end:]

    def remove(self, path):
        path = tuple(path)
        start, end = self.spans[path]
        # Delete the key as well as its value, keeping surrounding comments.
        parent_start = self.spans[path[:-1]][0]
        decoder = json.JSONDecoder()
        i = parent_start + 1
        while i < start:
            i = self._space(i)
            if self.clean[i] == ',': i += 1; continue
            key_start = i
            key, i = decoder.raw_decode(self.clean, i)
            i = self._space(i) + 1
            value_start = self._space(i)
            if value_start == start: break
            i = self.spans[path[:-1] + (key,)][1]
        after = self._space(end)
        if self.clean[after:after+1] == ',': end = after + 1
        else:
            before = key_start - 1
            while before > parent_start and self.clean[before].isspace(): before -= 1
            if self.clean[before] == ',':
                return self.text[:before] + self.text[before+1:key_start] + self.text[end:]
        return self.text[:key_start] + self.text[end:]
