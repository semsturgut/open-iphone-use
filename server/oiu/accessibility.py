"""Physical AX trees over the existing RSD connection, with checked ref admission.

Wire protocol and paired-dump admission adapted from AXe (MIT, see AXE_LICENSE.txt).
No Objective-C classes are instantiated when decoding device archives.
"""
import asyncio
import contextlib
from dataclasses import dataclass
import math
import plistlib
import re
import time
import uuid

SERVICE = 'com.apple.accessibility.axAuditDaemon.remoteAXService'
MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 4096
MAX_RECORDS = 32768
MAX_DEPTH = 64


class AXError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def invalid(message='Invalid accessibility archive'):
    return AXError('treeUnavailable', message)


class Archive:
    """Bounded NSKeyedArchive reader. Unknown value classes remain inert dictionaries."""
    def __init__(self, data):
        if not isinstance(data, bytes) or len(data) > MAX_BYTES or not data.startswith(b'bplist00'):
            raise invalid()
        try:
            archive = plistlib.loads(data)
            self.objects = archive['$objects']
            if archive['$archiver'] != 'NSKeyedArchiver' or archive['$version'] != 100000:
                raise invalid()
            if not isinstance(self.objects, list) or len(self.objects) > 262144:
                raise invalid('Accessibility archive exceeds object limit')
            self.cache = {0: None}
            self.visiting = set()
            self.root = self.decode(archive['$top']['root'])
        except (KeyError, IndexError, TypeError, OverflowError, RecursionError, plistlib.InvalidFileException) as error:
            raise invalid() from error

    def decode(self, value, depth=0):
        if depth > MAX_DEPTH:
            raise invalid('Accessibility archive exceeds depth limit')
        if isinstance(value, plistlib.UID):
            index = value.data
            if index in self.visiting or index >= len(self.objects):
                raise invalid('Cyclic or invalid accessibility reference')
            if index not in self.cache:
                self.visiting.add(index)
                self.cache[index] = self.decode(self.objects[index], depth + 1)
                self.visiting.remove(index)
            return self.cache[index]
        if isinstance(value, list):
            return [self.decode(v, depth + 1) for v in value]
        if not isinstance(value, dict):
            return value
        kind = self.objects[value['$class'].data]['$classname'] if '$class' in value else ''
        if kind in ('NSArray', 'NSMutableArray', 'NSSet', 'NSMutableSet'):
            return self.decode(value['NS.objects'], depth + 1)
        if kind in ('NSDictionary', 'NSMutableDictionary'):
            keys = self.decode(value['NS.keys'], depth + 1)
            values = self.decode(value['NS.objects'], depth + 1)
            if len(keys) != len(values):
                raise invalid()
            return dict(zip(keys, values))
        if kind in ('NSString', 'NSMutableString'):
            return self.decode(value['NS.string'], depth + 1)
        if kind in ('NSData', 'NSMutableData'):
            return value['NS.data']
        if kind in ('NSAttributedString', 'NSMutableAttributedString'):
            return self.decode(value['NSString'], depth + 1)
        return {'class': kind, **{k: self.decode(v, depth + 1) for k, v in value.items() if k != '$class'}}


def identity(obj):
    if not isinstance(obj, dict) or obj.get('class') != 'AXPTranslationObject':
        raise invalid('Missing accessibility object identity')
    pid, oid = obj.get('pid'), obj.get('objectID')
    if type(pid) is not int or pid <= 0 or type(oid) is not int or not oid:
        raise invalid('Invalid accessibility object identity')
    return pid, oid


def geometry(value, count):
    if not isinstance(value, dict) or value.get('class') != 'NSValue':
        raise invalid('Missing accessibility geometry')
    key = 'NS.rectval' if count == 4 else 'NS.pointval'
    encoded = value.get(key)
    if not isinstance(encoded, str):
        raise invalid('Invalid accessibility geometry')
    numbers = tuple(float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', encoded))
    if len(numbers) != count or not all(math.isfinite(v) for v in numbers):
        raise invalid('Invalid accessibility geometry')
    return numbers


def text(value):
    return str(value) if isinstance(value, (str, int, float, bool)) else ''


def role(attributes):
    raw = attributes.get(45)
    if isinstance(raw, str):
        return raw
    names = {1: 'application', 2: 'button', 3: 'checkbox', 4: 'generic', 5: 'group',
             6: 'heading', 7: 'image', 8: 'link', 9: 'radio', 10: 'scrollarea', 11: 'scrollbar',
             12: 'tabgroup', 13: 'slider', 14: 'text', 15: 'textfield', 16: 'webarea',
             17: 'textarea', 18: 'splitter', 19: 'popupbutton', 20: 'sheet', 21: 'grid', 22: 'incrementor'}
    return names.get(raw, f'role-{raw}')


def signature(attributes):
    """Only stable target facts. A changing value is meaningful and invalidates admission."""
    return tuple(attributes.get(k) for k in (21, 25, 27, 32, 33, 45, 53, 77, 1, 60, 124))


@dataclass
class Tree:
    root: tuple
    objects: dict
    attributes: dict
    paths: dict
    shape: tuple
    orientation: object

    @classmethod
    def parse(cls, envelope):
        if not isinstance(envelope, dict) or envelope.get('class') != 'AXPTranslatorResponse':
            raise invalid()
        if envelope.get('associatedRequestType') != 11 or envelope.get('error') != 0:
            raise invalid()
        payload = envelope.get('resultData')
        if not isinstance(payload, dict) or not isinstance(payload.get('treeDump'), list):
            raise invalid()
        records = payload['treeDump']
        if len(records) > MAX_RECORDS:
            raise invalid('Accessibility tree exceeds record limit')
        kind = payload.get('treeDumpType')
        if kind is None and not records:
            return None, None
        if kind not in ('AXPTreeDumpTypeInitialDump', 'AXPTreeDumpTypeAdditionalData'):
            raise invalid()
        roots, objects, attributes = [], {}, {}
        orientation = None
        for record in records:
            if not isinstance(record, dict) or record.get('class') != 'AXPTranslatorResponse' or record.get('error') != 0:
                raise invalid()
            request = record.get('associatedRequestType')
            obj = record.get('resultData') if request == 4 else record.get('associatedTranslationObject')
            key = identity(obj) if obj is not None else None
            if key:
                objects[key] = obj
            if request == 4:
                if not obj.get('isApplicationElement'):
                    raise invalid()
                roots.append(key)
            elif request == 5:
                if key is None or key in attributes or not isinstance(record.get('resultData'), dict):
                    raise invalid()
                attributes[key] = record['resultData'].copy()
            elif request == 2:
                if key:
                    attributes.setdefault(key, {})[record['attribute']] = record.get('resultData')
                elif record.get('attribute') == 129:
                    orientation = record.get('resultData')
        if len(roots) != 1 or len(attributes) > MAX_NODES:
            raise invalid()
        root = roots[0]
        shapes = []
        paths = {}
        for attribute in (8, 9):
            seen, shape, stack = set(), [], [(root, ())]
            while stack:
                key, path = stack.pop()
                if key in seen or len(path) > MAX_DEPTH or key not in attributes:
                    raise invalid('Incomplete or cyclic accessibility tree')
                seen.add(key)
                children = attributes[key].get(attribute)
                if not isinstance(children, list) or len(children) > MAX_NODES:
                    raise invalid('Incomplete accessibility children')
                shape.append((key[0], len(children)))
                if attribute == 9:
                    paths[key] = path
                stack.extend((identity(child), path + (index,)) for index, child in reversed(list(enumerate(children))))
            shapes.append(tuple(shape))
        return kind, cls(root, objects, attributes, paths, (root, *shapes), orientation)

    def rows(self, prefix):
        rows, refs = [], {}
        for key in self.paths:
            attributes = self.attributes[key]
            if not attributes.get(32, False):
                continue
            label, value = text(attributes.get(33)), text(attributes.get(53))
            if not label.strip() and not value.strip():
                continue
            ref = f'{prefix}-{len(refs) + 1}'
            actionable = attributes.get(27) == 1 and attributes.get(124) is True
            action = 'tap' if actionable else 'read'
            fields = (ref, action, role(attributes), label, value)
            rows.append('|'.join(v.replace('\\', '\\\\').replace('|', '\\|').replace('\r', '\\r').replace('\n', '\\n') for v in fields))
            refs[ref] = key
        return '\n'.join(rows), refs


def request_archive(request_type, obj, attribute=0, point=None):
    """Encode the small, fixed set of read requests needed for target checks."""
    objects = ['$null']

    def add(value):
        objects.append(value)
        return plistlib.UID(len(objects) - 1)

    def klass(name):
        return add({'$classname': name, '$classes': [name, 'NSObject']})

    def dictionary(values):
        return add({'$class': klass('NSDictionary'), 'NS.keys': [add(k) for k in values],
                    'NS.objects': list(values.values())})

    translated = {'$class': klass('AXPTranslationObject'), 'pid': obj['pid'], 'objectID': add(obj['objectID']),
                  'rawElementData': plistlib.UID(0), 'isApplicationElement': obj.get('isApplicationElement', False),
                  'didPopuldateAppInfo': True, 'bridgeDelegateToken': plistlib.UID(0)}
    parameters = plistlib.UID(0)
    if request_type == 5:
        attributes = add({'$class': klass('NSArray'), 'NS.objects': [add(k) for k in (21, 25, 27, 32, 33, 45, 53, 77, 1, 60, 124)]})
        parameters = dictionary({'attributes': attributes})
    if point is not None:
        translated['pid'] = 0
        translated['isApplicationElement'] = False
        value = add({'$class': klass('NSValue'), 'NS.special': 1, 'NS.pointval': add('{%s, %s}' % point)})
        parameters = dictionary({'point': value, 'displayId': add(0)})
    root = add({'$class': klass('AXPTranslatorRequest'), 'requestType': request_type,
                'attributeType': attribute, 'actionType': 0, 'clientType': 0 if point else 2,
                'translation': add(translated), 'parameters': parameters})
    return plistlib.dumps({'$version': 100000, '$archiver': 'NSKeyedArchiver', '$top': {'root': root},
                          '$objects': objects}, fmt=plistlib.FMT_BINARY, sort_keys=False)


class AccessibilitySession:
    """One subscription, one reader and at most one live request, all on the backend loop."""
    def __init__(self, rsd):
        self.rsd = rsd
        self.provider = None
        self.reader = None
        self.tree = None
        self.initial = None
        self.sequence = 0
        self.changed = asyncio.Event()
        self.pending = None
        self.error = None
        self.refs = {}
        self.observed = None
        self.observed_at = 0

    async def close(self):
        self.refs.clear()
        self.observed = None
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader
            self.reader = None
        if self.provider:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.provider.dtx._ctrl_channel.invoke('clientNeedsAccessibility:', False, expects_reply=False), .5)
            await self.provider.close()
            self.provider = None
        self.tree = None

    async def refresh(self):
        # A read started during an app transition can produce an empty/mismatched
        # pair. Retry that read once on a new AX channel, within one total budget.
        try:
            async with asyncio.timeout(25):
                for attempt in range(2):
                    try:
                        return await self._refresh_once()
                    except AXError as error:
                        if attempt or error.code != 'treeUnavailable':
                            raise
                        await asyncio.sleep(.15)
        except TimeoutError as error:
            raise AXError('treeUnavailable', 'No complete accessibility tree arrived within 25 seconds. Retry get_ui_tree after the screen settles.') from error

    async def _refresh_once(self):
        # Repeating YES on one subscription replays old data on device. Reopen only AX.
        await self.close()
        self.error, self.initial = None, None
        self.changed.clear()
        if SERVICE not in self.rsd.peer_info.get('Services', {}):
            raise AXError('accessibilityUnavailable', 'This phone does not publish remoteAXService')
        from pymobiledevice3.dtx_service_provider import DtxServiceProvider

        class Provider(DtxServiceProvider):
            SERVICE_NAME = SERVICE

        self.provider = Provider(self.rsd)
        try:
            async with asyncio.timeout(25):
                await self.provider.connect()
                queue = asyncio.Queue(maxsize=256)
                self.provider.dtx.ctx['control_dispatch_queue'] = queue
                self.reader = asyncio.create_task(self.read(queue))
                await self.provider.dtx._ctrl_channel.invoke('clientNeedsAccessibility:', True, expects_reply=False)
                while self.tree is None:
                    await self.changed.wait()
                    self.changed.clear()
                    if self.error:
                        raise self.error
            self.observed = self.tree
            self.observed_at = time.monotonic()
            rows, self.refs = self.tree.rows(uuid.uuid4().hex[:12])
            return dict(ok=True, rows=rows, format='ref|action|role|label|value', count=len(self.refs))
        except BaseException:
            await self.close()
            raise

    async def read(self, queue):
        try:
            while True:
                name, args = await queue.get()
                if name != 'processDataFromRemoteDevice:':
                    continue
                if len(args) != 1:
                    raise invalid()
                response = Archive(args[0]).root
                if response.get('associatedRequestType') == 11:
                    self.sequence += 1
                    self.tree = None
                    kind, tree = Tree.parse(response)
                    if kind == 'AXPTreeDumpTypeInitialDump':
                        self.initial = tree.shape
                    elif kind == 'AXPTreeDumpTypeAdditionalData':
                        if self.initial is None or tree.shape != self.initial:
                            raise invalid('Accessibility dump pair changed during the read')
                        self.tree = tree
                        self.initial = None
                    else:
                        self.initial = None
                        raise invalid('Device sent an empty accessibility dump')
                    self.changed.set()
                elif self.pending:
                    expected, future = self.pending
                    request_type, key, attribute = expected
                    if response.get('associatedRequestType') != request_type or response.get('attribute') != attribute:
                        raise AXError('accessibilityReplyMismatch', 'Accessibility response did not match the request')
                    # iOS sends targetless type-5/6 replies. One pending request,
                    # its DTX ACK, and closing on timeout own their correlation.
                    if response.get('associatedTranslationObject') is not None and identity(response['associatedTranslationObject']) != key:
                        raise AXError('accessibilityReplyMismatch', 'Accessibility response identified another target')
                    if not future.done():
                        future.set_result(response)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = error
            self.tree = None
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(error)
            self.changed.set()

    async def request(self, request_type, obj, attribute=0, point=None):
        if self.pending is not None:
            raise AXError('busy', 'Another accessibility request owns this channel')
        if self.error:
            raise self.error
        future = asyncio.get_running_loop().create_future()
        self.pending = ((request_type, identity(obj), attribute), future)
        try:
            async with asyncio.timeout(4):
                # Wait for both the DTX acknowledgement and the matching AX response.
                await self.provider.dtx._ctrl_channel.invoke('processDataFromHost:',
                    request_archive(request_type, obj, attribute, point))
                response = await future
                if response.get('error') != 0:
                    raise AXError('targetChanged', 'Accessibility target could not be checked')
                return response.get('resultData')
        except BaseException:
            # A delayed response must never satisfy a later request.
            await self.close()
            raise
        finally:
            self.pending = None

    async def checked_point(self, ref):
        key = self.refs.get(ref)
        old = self.observed
        if key is None or old is None or time.monotonic() - self.observed_at > 120:
            raise AXError('staleRef', 'Unknown or expired ref. Call get_ui_tree again.')
        if self.tree is None or self.error:
            raise AXError('targetChanged', 'Accessibility tree changed. Call get_ui_tree again.')
        sequence = self.sequence
        root_now = await self.request(5, old.objects[old.root])
        if root_now.get(21) != old.attributes[old.root].get(21):
            raise AXError('targetChanged', 'Display geometry changed. No input sent.')
        obj = old.objects[key]
        before = old.attributes[key]
        current = await self.request(5, obj)
        if not isinstance(current, dict) or signature(current) != signature(before):
            raise AXError('targetChanged', 'Target moved or changed. No input sent. Call get_ui_tree again.')
        if current.get(27) != 1 or current.get(32) is not True or current.get(124) is not True:
            raise AXError('targetNotHittable', 'Target is hidden, disabled or unavailable for interaction')
        x, y, w, h = geometry(current.get(21), 4)
        bx, by, bw, bh = geometry(old.attributes[old.root].get(21), 4)
        px, py = geometry(current.get(1), 2)
        if not (w > 0 and h > 0 and bw > 0 and bh > 0 and bx == by == 0
                and x <= px <= x + w and y <= py <= y + h and 0 < px < bw and 0 < py < bh):
            raise AXError('targetNotHittable', 'Target has no safe on-screen activation point')
        # Resolve the frontmost element at the actual touch point, including covering windows.
        hit = await self.request(6, old.objects[old.root], point=(px, py))
        hit_key = identity(hit)
        hit_attributes = await self.request(5, hit)
        # Hit testing creates a new translation object. The on-device element token
        # (PID, AX object data) is the stable identity across translation objects.
        token = before.get(60)
        if (not isinstance(token, dict) or not isinstance(token.get('ElementData'), bytes)
                or not token['ElementData'] or hit_key[0] != key[0]
                or hit_attributes.get(60) != token or signature(hit_attributes) != signature(before)):
            raise AXError('targetCovered', 'Another element covers the target. No input sent.')
        # Recheck geometry after hit-testing. A push during admission invalidates the check.
        final = await self.request(5, obj)
        last_hit = await self.request(6, old.objects[old.root], point=(px, py))
        if (signature(final) != signature(before) or sequence != self.sequence or self.tree is None
                or identity(last_hit) != hit_key):
            raise AXError('targetChanged', 'Target changed during admission. No input sent.')
        return px / bw, py / bh
