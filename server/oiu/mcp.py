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
    "move_pointer": {"dx": "integer", "dy": "integer"},
    "click": {"button": "string"}, "scroll": {"dy": "integer"},
    "type_text": {"text": "string"}, "send_key": {"key": "string"},
    "pointer_tap": {"x": "number", "y": "number"}, "pointer_move": {"x": "number", "y": "number"},
    "swipe": {"profile": "string"}, "advance_feed": {"dy": "integer"},
    "set_page_board": {"aim": "boolean"},
    "ensure_session": {"return_app": "string"}, "prepare_session": {"return_app": "string"},
}


def tools():
    result = []
    for name in ["list_devices", "list_bluetooth_candidates", "create_device", "remove_device", "get_device_status", "get_latest_screenshot", "stop_control", *sorted(ACTIONS)]:
        props = {} if name in ("list_devices", "list_bluetooth_candidates") else {"device_id": {"type": "string"}}
        if name == 'create_device': props = {'bt_id': {'type': 'string'}}
        if name in ACTIONS:
            props["expected_frame_id"] = {"type": "string", "description": "ID of a screenshot from this device after its last input completed, at most 120 seconds old. The frameID that an action's own result returns is valid for the next action, so a new get_latest_screenshot per action is not needed; a frame captured before the last input completed is rejected."}
            props.update({key: {"type": kind} for key, kind in PARAMETERS.get(name, {}).items()})
        if name in ('pointer_tap', 'pointer_move'):
            for axis in ('x', 'y'):
                props[axis]['description'] = f'{axis} is a fraction of the FULL returned image: pixel coordinate divided by image width for x or image height for y; origin is top-left.'
        optional = {'expected_frame_id'} if name in PREPARATION else set()
        if name in ('ensure_session', 'prepare_session'): optional.add('return_app')
        result.append(dict(name=name, description=f"{name.replace('_', ' ')}. Choose a device explicitly; inspect each resulting screen. HID acceptance does not prove UI success.",
                           inputSchema=dict(type="object", properties=props, required=[p for p in props if p not in optional], additionalProperties=False)))
        if name == 'scroll':
            result[-1]['description'] = 'Send raw HID wheel ticks. Positive dy moves content up/advances on iOS, matching native macOS. App sensitivity varies; use modest values and verify reel identity, not video animation or ok:true.'
        if name == 'advance_feed':
            result[-1]['description'] = 'Advance to the NEXT item with dy=1 (upward drag); dy=-1 returns to the previous item. Presses, drags and releases open-loop with calibration (or a compatible fallback) and never wait for the pointer to be visible, so it works over video apps. Inspect creator/title afterward; HID acceptance is not a transition count.'
        if name in ('ensure_session', 'prepare_session'):
            result[-1]['description'] = 'Prepare the pointer: a compatible, non-suspect calibration is reused; otherwise the Safari sharing tab is brought to the front and calibrated. Calibration accepts a good fit immediately and remeasures once if poor. Optional return_app reopens that app via Spotlight after preparation. Errors tell exactly what the owner must do.'
        if name in ('pointer_tap', 'pointer_move'):
            result[-1]['description'] += ' Uses bounded corrections and learns consistent landing offsets. Inspect click_sent, verified, landing, predicted_landing and observation_method: predicted and blind landings are unverified, and even an observed landing may miss. Check the returned screen for the UI outcome.'
        if name == 'get_latest_screenshot':
            result[-1]['description'] = 'Read the selected phone screenshot and frame age. For input use an ID captured after this device’s last input completed, at most 120 seconds old. Screen sharing must still be active (latest frame within five seconds).'
        if name in ('get_latest_screenshot', *ACTIONS):
            result[-1]['description'] += ' Returned frame metadata includes actual width and height in pixels. Coordinates use the full image: x=px/width, y=py/height, origin top-left.'
        if name in ('swipe', 'advance_feed', 'scroll'):
            result[-1]['description'] += ' The UI may still be animating; take a fresh screenshot before targeting.'
        if name == 'create_device':
            result[-1]['description'] = 'Create a phone bound to the explicitly selected Bluetooth HID peer from list_bluetooth_candidates. Returns its private capture_path; combine with setup capture_origin. Bluetooth must be connected before adding.'
        if name == 'list_bluetooth_candidates':
            result[-1]['description'] = 'List connected Bluetooth HID phones, their names, availability and exact bt_id (id). Pairing in AssistiveTouch is required; never guess a peer.'
        if name == 'remove_device':
            result[-1]['description'] = 'Remove the explicitly selected phone, revoke its sharing link and cancel control. Use only when the user requests removal; OS Bluetooth pairing is retained.'
            result[-1]['annotations'] = dict(destructiveHint=True, readOnlyHint=False)
    return result


def handle(args, method, params):
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18") else "2024-11-05"
        return dict(protocolVersion=version, capabilities={"tools": {}},
                    serverInfo={"name": "open-iphone-use-portable", "version": "0.1.0"},
                    instructions="Bluetooth HID must be connected before adding a phone. List Bluetooth candidates and explicitly create_device with its bt_id; then list devices and choose the intended device_id. Preparation takes its own frames and needs no expected_frame_id. For other input use a screenshot ID for that phone captured after its last input completed and at most 120 seconds old. Returned images include actual pixel width and height. For coordinates use x=px/width and y=py/height over the full image, origin top-left. After swipe, advance_feed or scroll, take a fresh screenshot before targeting because UI may still be animating. Every action result carries its own frameID, captured after the input ended and valid for the next action, so an extra get_latest_screenshot after each action is unnecessary; a frame captured before the last input completed is rejected. Screen sharing must remain active with a frame in the last five seconds. Red Bluetooth/screen sharing status means control will not work. Owner pairing/sharing/unlocking is required.")
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
                     "number": type(value) in (int, float), "boolean": type(value) is bool}[kind]
            if not valid:
                raise ValueError(f"Invalid {key}")
        if name == "list_devices":
            result = call(args, "/api/devices")
        elif name == 'list_bluetooth_candidates':
            result = call(args, '/api/bluetooth')
        elif name == 'create_device':
            result = call(args, '/api/devices', {'bt_id': arguments['bt_id']})
            result['capture_url'] = call(args, '/api/setup')['capture_origin'] + result['capture_path']
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
                result = call(args, path + "snapshot")
            else:
                if "expected_frame_id" in arguments:
                    arguments["expectedFrameID"] = arguments.pop("expected_frame_id")
                result = call(args, path + "action", dict(operation=name, arguments=arguments))
        frame = result.pop("frame", None) if isinstance(result, dict) else None
        content = [{"type": "text", "text": json.dumps(result)}]
        if frame:
            content.extend([{"type": "image", "mimeType": "image/jpeg", "data": frame["base64"]},
                            {"type": "text", "text": json.dumps({k: v for k, v in frame.items() if k != "base64"})}])
        return dict(content=content, isError=isinstance(result, dict) and result.get("ok") is False)
    except (URLError, ConnectionError, TimeoutError) as error:
        LOG.warning('Server connection unavailable: %s', error)
        return dict(content=[{"type": "text", "text": SERVER_DOWN}], isError=True)
    except Exception as error:
        LOG.exception('MCP tool failed')
        return dict(content=[{"type": "text", "text": str(error) or type(error).__name__}], isError=True)


def configure_logging(args):
    from .permissions import secure_directory
    from .bootstrap import private_file
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
