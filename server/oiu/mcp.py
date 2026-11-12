"""MCP JSON-RPC over stdio; stdout is reserved for protocol messages."""
import json
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import URLError

LOG = logging.getLogger('oiu.mcp')
SERVER_DOWN = 'Server not running — start it with: open-iphone-use serve'
from .cli import call
from .core import ACTIONS, PREPARATION

PARAMETERS = {
    "click": {}, "scroll": {"dy": "integer"},
    "type_text": {"text": "string"}, "send_key": {"key": "string"},
    "pointer_tap": {"x": "number", "y": "number"}, "pointer_move": {"x": "number", "y": "number"},
    "swipe": {"profile": "string"}, "advance_feed": {"dy": "integer"},
    "ensure_session": {"return_app": "string"},
    "tap_ref": {"ref": "string"}, "batch": {"steps": "array"},
}
FRACTION = '{axis} is a fraction of the FULL returned image: pixel coordinate divided by image {size}; origin is top-left.'
DESCRIPTIONS = {
    'get_ui_tree': 'Read the accessibility tree once as compact ref|action|role|label|value rows. No screenshot needed. Refresh to discover new controls. Refs expire after 120 seconds or reconnect.',
    'tap_ref': 'Tap an observed accessibility ref after live identity, geometry and coverage checks. Refuses changed or covered targets before any input. Defaults to image_mode=none.',
    'batch': 'Run 1 to 16 ordered steps under one device lock with an 80-second execution budget. Stops at the first failure, with per-step receipts and completed count. Steps use operation plus its arguments. Supported operations are tap_ref, get_ui_tree, type_text, send_key, scroll, swipe and advance_feed. All step syntax is validated before input. Refs are checked live at each tap. Use existing refs, or refresh with get_ui_tree between batches to discover new controls. Defaults to image_mode=none.',
    'list_devices': 'List added phones with device_id, name, connection status and frame age.',
    'list_candidates': 'List connected phones (USB or Wi-Fi) with udid, name, iOS version and whether they are already added. Never guess a udid.',
    'create_device': 'Add the phone with this udid from list_candidates. Its screen streams live; no calibration is needed.',
    'remove_device': 'Remove the explicitly selected phone and stop its control. Use only when the user requests removal.',
    'get_device_status': 'Connection details for the selected phone: USB or Wi-Fi, whether input is available, and errors.',
    'get_latest_screenshot': 'Read the selected phone screenshot and frame age. For input use an ID captured after this device’s last input completed, at most 120 seconds old.',
    'stop_control': 'Cancel the running action on the selected phone and release any held touch or key.',
    'ensure_session': 'Check that input is available for the selected phone. Optional return_app opens that app through Spotlight.',
    'pointer_tap': 'Touch the exact point given. Check the returned screen for the UI outcome.',
    'pointer_move': 'Set the point that click touches; nothing is touched.',
    'click': 'Touch the point set by pointer_move.',
    'type_text': 'Type US-layout ASCII text with the phone\'s keyboard focus, at most 500 bytes.',
    'send_key': 'Press a key or chord, e.g. return, escape, cmd+space. home, lock, volume_up, volume_down, mute and siri press hardware buttons.',
    'scroll': 'Scroll by dragging a finger; positive dy moves content up (like the wheel), magnitude up to 1200. Verify the result on the returned screen.',
    'swipe': 'Swipe with profile up, down, left or right across the middle of the screen.',
    'advance_feed': 'Advance to the NEXT item in a vertical feed with dy=1 (upward swipe); dy=-1 returns to the previous item. Inspect creator/title afterward.',
}


def tools():
    result = []
    for name in ["list_devices", "list_candidates", "create_device", "remove_device", "get_device_status", "get_latest_screenshot", "stop_control", *sorted(ACTIONS)]:
        props = {} if name in ("list_devices", "list_candidates") else {"device_id": {"type": "string"}}
        if name == 'create_device': props = {'udid': {'type': 'string'}}
        if name in ('get_latest_screenshot', *ACTIONS):
            props['image_mode'] = {'type': 'string', 'enum': ['full', 'preview', 'none'], 'description': 'full returns the native JPEG, preview returns a JPEG with longest edge up to 640 pixels, none returns metadata and screenHash only. Legacy tools default to full. Tree, ref and batch tools default to none. screenHash is SHA-256 of the native JPEG, independent of preview sizing.'}
        if name in ACTIONS:
            props["expected_frame_id"] = {"type": "string", "description": "ID of a screenshot from this device after its last input completed, at most 120 seconds old. The frameID that an action's own result returns is valid for the next action, so a new get_latest_screenshot per action is not needed; a frame captured before the last input completed is rejected."}
            props.update({key: {"type": kind} for key, kind in PARAMETERS.get(name, {}).items()})
        if name in ('pointer_tap', 'pointer_move'):
            props['x']['description'] = FRACTION.format(axis='x', size='width')
            props['y']['description'] = FRACTION.format(axis='y', size='height')
        optional = {'expected_frame_id'} if name in PREPARATION else set()
        optional.add('image_mode')
        if name == 'batch':
            props['steps']['minItems'] = 1
            props['steps']['maxItems'] = 16
            props['steps']['items'] = {'oneOf': [
                {'type': 'object', 'properties': {'operation': {'const': op}, **{k: {'type': t} for k, t in PARAMETERS.get(op, {}).items()}},
                 'required': ['operation', *PARAMETERS.get(op, {})], 'additionalProperties': False}
                for op in ('tap_ref', 'get_ui_tree', 'type_text', 'send_key', 'scroll', 'swipe', 'advance_feed')]}
        if name == 'ensure_session': optional.add('return_app')
        description = DESCRIPTIONS[name] + ' Choose a device explicitly; an accepted action does not prove the UI changed, so inspect each resulting screen.'
        if name in ('get_latest_screenshot', *ACTIONS):
            description += ' Returned frame metadata includes actual width and height in pixels. Coordinates use the full image: x=px/width, y=py/height, origin top-left.'
        if name in ('swipe', 'advance_feed', 'scroll'):
            description += ' The UI may still be animating; take a fresh screenshot before targeting.'
        result.append(dict(name=name, description=description,
                           inputSchema=dict(type="object", properties=props, required=[p for p in props if p not in optional], additionalProperties=False)))
        if name == 'remove_device':
            result[-1]['annotations'] = dict(destructiveHint=True, readOnlyHint=False)
    return result


def handle(args, method, params):
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18") else "2024-11-05"
        return dict(protocolVersion=version, capabilities={"tools": {}},
                    serverInfo={"name": "open-iphone-use", "version": "0.2.0"},
                    instructions="Prefer get_ui_tree to find controls without images, then tap_ref with its ref. Live checks reject moved or covered targets. batch serializes up to 16 steps and stops on failure. image_mode=none omits image bytes and preview downsizes to 640 pixels, both preserve screenHash and frameID. Refresh the tree to discover new refs. Phones connect to the Mac by USB or Wi-Fi. To add one, list_candidates and create_device with its udid; then list_devices and choose the intended device_id. ensure_session needs no expected_frame_id. For legacy coordinate, keyboard and gesture tools use a screenshot ID for that phone captured after its last input completed and at most 120 seconds old. Returned images include actual pixel width and height. For coordinates use x=px/width and y=py/height over the full image, origin top-left. Touches land exactly where given. After swipe, advance_feed or scroll, take a fresh screenshot before targeting because UI may still be animating. Every action result carries its own frameID, captured after the input ended and valid for the next action, so an extra get_latest_screenshot after each action is unnecessary. The screen must be live (a frame within five seconds). The owner unlocks the phone; iOS permissions still apply.")
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools()}
    if method != "tools/call":
        raise LookupError("Method not found")
    try:
        name = params["name"]
        schema = next(t["inputSchema"] for t in tools() if t["name"] == name)
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
            raise ValueError("Invalid or missing tool arguments")
        for key, value in arguments.items():
            kind = schema["properties"][key]["type"]
            valid = {"string": isinstance(value, str), "integer": type(value) is int,
                     "number": type(value) in (int, float), "boolean": type(value) is bool, "array": isinstance(value, list)}[kind]
            if not valid or ('enum' in schema['properties'][key] and value not in schema['properties'][key]['enum']):
                raise ValueError(f"Invalid {key}")
        if name == "list_devices":
            result = call(args, "/api/devices")
        elif name == 'list_candidates':
            result = call(args, '/api/candidates')
        elif name == 'create_device':
            result = call(args, '/api/devices', arguments)
        else:
            device = arguments.pop("device_id")
            if not device.isalnum():
                raise ValueError("Invalid device ID")
            path = f"/api/devices/{device}/"
            if name == "remove_device":
                result = call(args, path.rstrip('/'), method='DELETE')
            elif name == "get_device_status":
                result = call(args, path + "status")
            elif name == "get_latest_screenshot":
                result = call(args, path + "snapshot?image_mode=" + arguments.get("image_mode", "full"))
            else:
                if "expected_frame_id" in arguments:
                    arguments["expectedFrameID"] = arguments.pop("expected_frame_id")
                result = call(args, path + "action", dict(operation=name, arguments=arguments))
        frame = result.pop("frame", None) if isinstance(result, dict) else None
        content = [{"type": "text", "text": json.dumps(result)}]
        if frame and frame.get("base64"):
            content.extend([{"type": "image", "mimeType": "image/jpeg", "data": frame["base64"]},
                            {"type": "text", "text": json.dumps({k: v for k, v in frame.items() if k != "base64"})}])
        elif frame:
            content.append({'type': 'text', 'text': json.dumps(frame)})
        return dict(content=content, isError=isinstance(result, dict) and result.get("ok") is False)
    except (URLError, ConnectionError, TimeoutError) as error:
        LOG.warning('Server connection unavailable: %s', error)
        return dict(content=[{"type": "text", "text": SERVER_DOWN}], isError=True)
    except Exception as error:
        LOG.exception('MCP tool failed')
        return dict(content=[{"type": "text", "text": str(error) or type(error).__name__}], isError=True)


def configure_logging(args):
    from .permissions import secure_directory
    from .files import private_file
    try:
        directory=Path(args.state)
        secure_directory(directory)
        path=directory/'mcp.log'
        if not path.exists(): private_file(path,b'')
        handler=RotatingFileHandler(path,maxBytes=1024*1024,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        LOG.addHandler(handler); LOG.setLevel(logging.INFO); LOG.propagate=False
        return handler
    except OSError:
        # Diagnostics must never go to the JSON-RPC stdout stream.
        handler=logging.StreamHandler(sys.stderr)
        LOG.addHandler(handler); LOG.propagate=False
        return handler


def run(args):
    handler=configure_logging(args)
    try:
        while True:
            try: line=sys.stdin.buffer.readline(1024*1024+1)
            except (OSError,ValueError): return  # Client closed the transport.
            if not line: return  # EOF is a client disconnect, not a server failure.
            oversized=len(line)>1024*1024
            if oversized:
                while not line.endswith(b'\n'):
                    try: line=sys.stdin.buffer.readline(1024*1024+1)
                    except (OSError,ValueError): return
                    if not line: break
            request=None
            try:
                if oversized: raise ValueError('Request exceeds 1 MiB')
                request=json.loads(line)
                if not isinstance(request,dict) or request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):
                    raise ValueError('Invalid request')
                if 'id' not in request: continue
                if not isinstance(request.get('params',{}),dict): raise ValueError('params must be an object')
                result=handle(args,request['method'],request.get('params',{}))
                response=dict(jsonrpc='2.0',id=request['id'],result=result)
            except Exception as error:
                LOG.exception('MCP request failed')
                response=dict(jsonrpc='2.0',id=request.get('id') if isinstance(request,dict) else None,
                              error=dict(code=-32601 if isinstance(error,LookupError) else -32600,message=str(error)))
            try: print(json.dumps(response),flush=True)
            except (BrokenPipeError,OSError):
                LOG.info('MCP client closed stdout')
                # Prevent a second BrokenPipeError during interpreter stdout teardown.
                import os
                try:
                    with open(os.devnull,'w') as sink: os.dup2(sink.fileno(),sys.stdout.fileno())
                except (OSError,ValueError): pass
                return
    finally:
        LOG.removeHandler(handler); handler.close()
