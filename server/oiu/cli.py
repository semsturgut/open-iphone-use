import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .backends import MacBackend, BlueZBackend, WindowsBackend
from .core import Registry
from .http import Server, tls_context
from .state import StateLease


def call(args, path, data=None):
    registry = json.loads((Path(args.state) / "state.json").read_text())
    request = Request(args.url.rstrip("/") + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + registry["admin_token"], "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=95) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            message = json.load(error).get("error", str(error))
        except ValueError:
            message = str(error)
        finally:
            error.close()
        raise ValueError(message) from None


def main():
    parser = argparse.ArgumentParser(description="Portable Open iPhone Use server (trusted LAN/localhost)")
    parser.add_argument("--state", default=os.environ.get("OIU_STATE", str(Path.home() / ".open-iphone-use")))
    parser.add_argument("--url", default=os.environ.get("OIU_URL", "http://127.0.0.1:27891"))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=27891)
    serve.add_argument("--cert")
    serve.add_argument("--key")
    serve.add_argument("--backend-config", help="Local JSON map of device IDs to backend configuration")
    sub.add_parser("devices")
    sub.add_parser("token", help="Print the private operator login credential locally")
    sub.add_parser("mcp", help="MCP stdio client for the running server")
    add = sub.add_parser("add")
    add.add_argument("name")
    status = sub.add_parser("status")
    status.add_argument("--device", required=True)
    act = sub.add_parser("action")
    act.add_argument("--device", required=True)
    act.add_argument("operation")
    act.add_argument("--arguments", default="{}", help="JSON with expectedFrameID and action fields")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            if bool(args.cert) != bool(args.key):
                parser.error("--cert and --key must be supplied together")
            if args.host not in ("127.0.0.1", "::1", "localhost") and not args.cert:
                parser.error("LAN listeners require --cert and --key (owner-trusted PEM certificate)")
            lease = StateLease(args.state)
            registry = Registry(args.state)
            try:
                if args.backend_config:
                    config = json.loads(Path(args.backend_config).read_text())
                    centrals = set()
                    for device_id, item in config.items():
                        device = registry.devices[device_id]
                        kind = item["kind"]
                        if kind == "macos":
                            if sys.platform != "darwin":
                                raise ValueError("macOS HID requires macOS")
                            central = item.get("central")
                            if central and central in centrals:
                                raise ValueError("A central cannot be bound to multiple devices")
                            if central:
                                centrals.add(central)
                            device.backend = MacBackend(item["executable"], registry.directory / device_id,
                                                        device_id, central)
                        elif kind == "bluez":
                            device.backend = BlueZBackend()
                        elif kind == "windows":
                            device.backend = WindowsBackend()
                        else:
                            raise ValueError("Unknown backend")
                context = tls_context(args.cert, args.key) if args.cert else None
                server = Server((args.host, args.port), registry, context)
                def stop(*_):
                    threading.Thread(target=server.shutdown, daemon=True).start()
                signal.signal(signal.SIGINT, stop)
                signal.signal(signal.SIGTERM, stop)
                print(f"Open iPhone Use: {'https' if context else 'http'}://{args.host}:{server.server_port}", flush=True)
                print("Use `open-iphone-use token` to retrieve the operator login locally.", flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
            finally:
                registry.close()
                lease.close()
        elif args.command == "token":
            print(json.loads((Path(args.state) / "state.json").read_text())["admin_token"])
        elif args.command == "devices":
            print(json.dumps(call(args, "/api/devices"), indent=2))
        elif args.command == "add":
            result = call(args, "/api/devices", {"name": args.name})
            print(json.dumps(dict(device_id=result["device_id"], capture_url=args.url.rstrip("/") + result["capture_path"]), indent=2))
        elif args.command == "status":
            print(json.dumps(call(args, f"/api/devices/{args.device}/status"), indent=2))
        elif args.command == "action":
            result = call(args, f"/api/devices/{args.device}/action",
                          dict(operation=args.operation, arguments=json.loads(args.arguments)))
            print(json.dumps(result, indent=2))
            if not result.get("ok"):
                return 1
        elif args.command == "mcp":
            from .mcp import run
            run(args)
    except (OSError, ValueError, KeyError) as error:
        print(f"open-iphone-use: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
