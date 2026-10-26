import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .core import Registry
from .http import Server, tls_context
from .state import StateLease


def connection(args):
    file=Path(args.state)/'connection.json'
    saved=json.loads(file.read_text()) if file.exists() else {}
    url=args.url or os.environ.get('OIU_URL') or saved.get('url','http://127.0.0.1:27891')
    return url.rstrip('/'), saved.get('ca') if url.rstrip('/')==saved.get('url') else None


def call(args,path,data=None,method=None):
    registry=json.loads((Path(args.state)/'state.json').read_text())
    url,ca=connection(args)
    request=Request(url+path,data=json.dumps(data).encode() if data is not None else None,method=method,
                    headers={'Authorization':'Bearer '+registry['admin_token'],'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=95,context=ssl.create_default_context(cafile=ca)) as response: return json.load(response)
    except HTTPError as error:
        try: message=json.load(error).get('error',str(error))
        except ValueError: message=str(error)
        finally: error.close()
        raise ValueError(message) from None


def serve(args,parser):
    from .bootstrap import lan_address,certificates,LANOrigin
    from .control import Pairing,Control,atomic_json
    from .backends import create_radio
    from .certificate_server import CertificateServer
    if bool(args.cert)!=bool(args.key): parser.error('--cert and --key must be supplied together')
    host=args.host or ('127.0.0.1' if args.local_http else lan_address())
    if args.local_http and host not in ('127.0.0.1','localhost'):
        parser.error('--local-http is for localhost only; LAN capture requires HTTPS')
    lease=StateLease(args.state)
    registry=None; radio=None; server=None; public_server=None; local_server=None; local_thread=None
    try:
        registry=Registry(args.state)
        ca=public=None
        cert,key=args.cert,args.key
        if not args.local_http and not cert: cert,key,ca,public=certificates(args.state,host)
        context=None if args.local_http else tls_context(cert,key)
        if not args.capture_only:
            radio=create_radio(args.state,args.helper)
            pairing=Pairing(radio)
            registry.backend_factory=lambda device: Control(pairing,registry.directory/device.id)
            for device in registry.devices.values(): device.backend=registry.backend_factory(device)
        # Auto mode follows interface changes; explicit --host remains a fixed bind.
        follow_lan = not args.local_http and not args.host and not args.cert
        bind_host = '0.0.0.0' if follow_lan else host
        server=Server((bind_host,args.port),registry,context)
        url=f"{'http' if args.local_http else 'https'}://{host}:{server.server_port}"
        registry.public_url=url
        ui_url=url
        if not args.local_http:
            local_server=Server(('127.0.0.1',args.ui_port),registry,watchdog=False)
            local_thread=threading.Thread(target=local_server.serve_forever,daemon=True)
            local_thread.start()
            ui_url=f'http://127.0.0.1:{local_server.server_port}'
        def save_origin(origin):
            atomic_json(registry.directory/'connection.json',dict(url=ui_url,ca=None if local_server else ca,public_url=origin))
            registry.public_url=origin
        save_origin(url)
        if follow_lan:
            registry.capture_origin=LANOrigin(registry.directory,host,server.server_port,context,save_origin)
        if public and not args.no_certificate_server:
            public_server=CertificateServer((bind_host,args.certificate_port),public)
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
        print(f'Open iPhone Use: {url}',flush=True)
        if args.capture_only: print('Bluetooth: capture-only mode (no input)',flush=True)
        elif hasattr(radio,'startup'):
            print('Bluetooth: '+radio.startup['state']+(' — '+radio.startup['error'] if radio.startup.get('error') else ''),flush=True)
        else: print('Bluetooth: native HID radio started; approve the OS pairing/permission prompts',flush=True)
        if public:
            print(f'Public CA certificate: {public}',flush=True)
            print('CA SHA-256: '+hashlib.sha256(Path(public).read_bytes()).hexdigest().upper(),flush=True)
            if public_server: print(f'Install public CA on iPhone: http://{host}:{public_server.server_port}/root-ca.cer',flush=True)
            print('On iPhone, install the profile, verify its fingerprint, then enable it in Settings > General > About > Certificate Trust Settings. The localhost web UI needs no certificate trust; LAN browsers must trust this CA.',flush=True)
        print('Pair on each iPhone: Settings > Accessibility > Touch > AssistiveTouch > Devices > Bluetooth Devices > Open iPhone Use (Windows may show the PC name). Keep the phone unlocked.',flush=True)
        if not args.no_print_secrets:
            print(f'Web UI (private operator link): {ui_url}/#{registry.admin_token}',flush=True)
            print(f'Open in Safari on EACH iPhone: {url}/join?token={registry.invite_token}',flush=True)
        else: print(f'Web UI: {ui_url} — use open-iphone-use token to log in; sharing links appear there.',flush=True)
        print('Approve Start sharing in Safari. Tap a live screen in the grid; the first tap prepares its control session with Safari visible. No pairing UUID configuration is needed.',flush=True)
        server.serve_forever()
    finally:
        if server: server.server_close()
        if local_server:
            local_server.shutdown(); local_server.server_close(); local_thread.join(2)
        if public_server: public_server.close()
        if registry: registry.close()
        if radio: radio.close()
        lease.close()


def main():
    parser=argparse.ArgumentParser(description='Open iPhone Use — headless multi-iPhone HTTPS + HID + MCP')
    parser.add_argument('--state',default=os.environ.get('OIU_STATE',str(Path.home()/'.open-iphone-use')))
    parser.add_argument('--url',default=None)
    sub=parser.add_subparsers(dest='command',required=True)
    start=sub.add_parser('serve')
    start.add_argument('--host',help='LAN IPv4 address; detected automatically')
    start.add_argument('--port',type=int,default=27891)
    start.add_argument('--ui-port',type=int,default=27893)
    start.add_argument('--cert'); start.add_argument('--key')
    start.add_argument('--helper',help='Override native macOS helper path')
    start.add_argument('--local-http',action='store_true',help='Localhost HTTP development mode')
    start.add_argument('--capture-only',action='store_true',help='Explicitly disable HID for diagnostics')
    start.add_argument('--certificate-port',type=int,default=27892)
    start.add_argument('--no-certificate-server',action='store_true')
    start.add_argument('--no-print-secrets',action='store_true')
    sub.add_parser('devices'); sub.add_parser('token'); sub.add_parser('mcp')
    add=sub.add_parser('add'); add.add_argument('name')
    remove=sub.add_parser('remove',help='Remove a phone and revoke its sharing link')
    remove.add_argument('--device',required=True)
    status=sub.add_parser('status'); status.add_argument('--device',required=True)
    act=sub.add_parser('action'); act.add_argument('--device',required=True)
    act.add_argument('operation'); act.add_argument('--arguments',default='{}')
    register=sub.add_parser('register-agents',help='Register detectable MCP clients and install the agent skill')
    register.add_argument('--home',default=str(Path.home()))
    register.add_argument('--uninstall',action='store_true')
    register.add_argument('--skill',help='Skill source path (setup supplies its checked-out copy)')
    args=parser.parse_args()
    try:
        if args.command=='serve': serve(args,parser)
        elif args.command=='register-agents':
            from .registrations import configure
            configure(Path(args.home),Path(sys.executable).parent/('open-iphone-use.exe' if os.name=='nt' else 'open-iphone-use'),args.skill,args.uninstall)
        elif args.command=='token': print(json.loads((Path(args.state)/'state.json').read_text())['admin_token'])
        elif args.command=='devices': print(json.dumps(call(args,'/api/devices'),indent=2))
        elif args.command=='add':
            result=call(args,'/api/devices',{'name':args.name})
            origin=call(args,'/api/setup').get('capture_origin') or connection(args)[0]
            print(json.dumps(dict(device_id=result['device_id'],capture_url=origin+result['capture_path']),indent=2))
        elif args.command=='remove':
            if not args.device.isalnum(): raise ValueError('Invalid device ID')
            print(json.dumps(call(args,f'/api/devices/{args.device}',method='DELETE'),indent=2))
        elif args.command=='status': print(json.dumps(call(args,f'/api/devices/{args.device}/status'),indent=2))
        elif args.command=='action':
            result=call(args,f'/api/devices/{args.device}/action',dict(operation=args.operation,arguments=json.loads(args.arguments)))
            print(json.dumps(result,indent=2))
            if not result.get('ok'): return 1
        elif args.command=='mcp':
            from .mcp import run
            run(args)
    except (OSError,ValueError,KeyError,ImportError) as error:
        print(f'open-iphone-use: {error}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__': sys.exit(main())
