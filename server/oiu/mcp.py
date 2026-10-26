"""MCP JSON-RPC over stdio; stdout is reserved for protocol messages."""
import json
import sys
from .cli import call
from .core import ACTIONS, PREPARATION

PARAMETERS = {
    "move_pointer": {"dx": "integer", "dy": "integer"},
    "click": {"button": "string"}, "scroll": {"dy": "integer"},
    "type_text": {"text": "string"}, "send_key": {"key": "string"},
    "pointer_tap": {"x": "number", "y": "number"}, "pointer_move": {"x": "number", "y": "number"},
    "swipe": {"profile": "string"}, "advance_feed": {"dy": "integer"},
    "set_page_board": {"aim": "boolean"},
}


def tools():
    result = []
    for name in ["list_devices", "list_bluetooth_candidates", "create_device", "remove_device", "get_device_status", "get_latest_screenshot", "stop_control", *sorted(ACTIONS)]:
        props = {} if name in ("list_devices", "list_bluetooth_candidates") else {"device_id": {"type": "string"}}
        if name == 'create_device': props = {'bt_id': {'type': 'string'}}
        if name in ACTIONS:
            props["expected_frame_id"] = {"type": "string"}
            props.update({key: {"type": kind} for key, kind in PARAMETERS.get(name, {}).items()})
        result.append(dict(name=name, description=f"{name.replace('_', ' ')}. Choose a device explicitly; inspect each resulting screen. HID acceptance does not prove UI success.",
                           inputSchema=dict(type="object", properties=props, required=[p for p in props if not (p=="expected_frame_id" and name in PREPARATION)], additionalProperties=False)))
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
                    instructions="Bluetooth HID must be connected before adding a phone. List Bluetooth candidates and explicitly create_device with its bt_id; then list devices and choose the intended device_id. Preparation takes its own frames and needs no expected_frame_id. For other input read a fresh screenshot and use an ID from that phone’s last two seconds. Red Bluetooth/screen sharing status means control will not work. Inspect a new screen after each action. Owner pairing/sharing/unlocking is required.")
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
    except (KeyError, ValueError, StopIteration, OSError) as error:
        return dict(content=[{"type": "text", "text": str(error)}], isError=True)


def run(args):
    while True:
        line = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not line:
            return
        if len(line) > 1024 * 1024:
            return  # Terminate rather than interpret a trailing fragment as another request.
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise ValueError("Invalid request")
            if "id" not in request:
                continue
            if not isinstance(request.get("params", {}), dict):
                raise ValueError("params must be an object")
            result = handle(args, request["method"], request.get("params", {}))
            response = dict(jsonrpc="2.0", id=request["id"], result=result)
        except (ValueError, LookupError, TypeError) as error:
            response = dict(jsonrpc="2.0", id=request.get("id") if isinstance(request, dict) else None,
                            error=dict(code=-32601 if isinstance(error, LookupError) else -32600, message=str(error)))
        print(json.dumps(response), flush=True)
