import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import threading
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .core import Registry
from .http import Server, tls_context
from .state import StateLease


def connection(args):
    file=Path(args.state)/'connection.json'
    saved=json.loads(file.read_text()) if file.exists() else {}
    url=args.url or os.environ.get('OIU_URL') or saved.get('url','http://127.0.0.1:27893')
    return url.rstrip('/'), saved.get('ca') if url.rstrip('/')==saved.get('url') else None


def call(args,path,data=None,method=None):
    url,ca=connection(args)
    request=Request(url+path,data=json.dumps(data).encode() if data is not None else None,method=method,
                    headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=95,context=ssl.create_default_context(cafile=ca)) as response: return json.load(response)
    except HTTPError as error:
        try: message=json.load(error).get('error',str(error))
        except ValueError: message=str(error)
        finally: error.close()
        raise ValueError(message) from None


def show_link(args, device):
    from .qr import terminal, enable_ansi
    setup=call(args,'/api/setup')
    origin=setup['capture_origin'] or connection(args)[0]
    url=origin+device['capture_path']
    print(f"{device['name']} [{device['device_id']}]\n{url}")
    if sys.stdout.isatty() and not enable_ansi():
        print('This console cannot enforce black-on-white QR output. Use Windows Terminal or open the link above.')
    else:
        qr=terminal(url,ansi=sys.stdout.isatty())
        try: qr.encode(sys.stdout.encoding or 'utf-8')
        except UnicodeEncodeError: qr='This output encoding cannot render QR half-blocks. Open the link above in a Unicode terminal.'
        print(qr)
    if setup.get('certificate_url'):
        print('First use: install the public CA on the iPhone, verify SHA-256 and enable full trust.')
        print(setup['certificate_url'] + '\nCA SHA-256: ' + setup['ca_fingerprint'])
    print('Scan on this Bluetooth-paired iPhone. The server must remain running; Bluetooth HID is required.')


def print_running(url,status,log):
    on=status.get('bluetooth_on',False)
    dot=('● ' if sys.stdout.isatty() else '')+('on' if on else 'off')
    from .qr import enable_ansi
    if sys.stdout.isatty() and enable_ansi(): dot=('\x1b[32m' if on else '\x1b[31m')+dot+'\x1b[0m'
    print('Open iPhone Use is running')
    print('Web UI:  '+url)
    print(f"Bluetooth: {dot}  ({status.get('bluetooth_state','unknown')})")
    print('Add a phone: open-iphone-use new')
    print('Stop:      open-iphone-use stop')
    print('Log:       '+str(log),flush=True)


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
        registry.instance_id=os.environ.get("OIU_DAEMON_START_ID") or uuid.uuid4().hex
        ca=public=None
        cert,key=args.cert,args.key
        if not args.local_http and not cert: cert,key,ca,public=certificates(args.state,host)
        context=None if args.local_http else tls_context(cert,key)
        if not args.capture_only:
            radio=create_radio(args.state,args.helper)
            registry.radio=radio
            pairing=Pairing(radio)
            # Migrate only an already saved exact peer; never choose an arbitrary phone.
            claimed = set()
            for device in registry.devices.values():
                saved = registry.directory/device.id/'calibration.json'
                if device.bt_id is None and saved.exists(): device.bt_id=json.loads(saved.read_text()).get('peer')
                if device.bt_id in claimed: device.bt_id=None
                elif device.bt_id: claimed.add(device.bt_id)
            registry.backend_factory=lambda device: Control(pairing,registry.directory/device.id,peer=device.bt_id)
            for device in registry.devices.values(): device.backend=registry.backend_factory(device)
            registry.save()
        # Auto mode follows interface changes; explicit --host remains a fixed bind.
        follow_lan = not args.local_http and not args.host and not args.cert
        bind_host = '0.0.0.0' if follow_lan else host
        server=Server((bind_host,args.port),registry,context,operator=args.local_http)
        url=f"{'http' if args.local_http else 'https'}://{host}:{server.server_port}"
        registry.public_url=url
        ui_url=url
        if not args.local_http:
            local_server=Server(('127.0.0.1',args.ui_port),registry,watchdog=False)
            local_thread=threading.Thread(target=local_server.serve_forever,daemon=True)
            local_thread.start()
            ui_url=f'http://127.0.0.1:{local_server.server_port}'
        def save_origin(origin):
            atomic_json(registry.directory/'connection.json',dict(url=ui_url,ca=None if local_server else ca,public_url=origin,instance_id=registry.instance_id))
            registry.public_url=origin
        save_origin(url)
        if follow_lan:
            registry.capture_origin=LANOrigin(registry.directory,host,server.server_port,context,save_origin)
        if public and not args.no_certificate_server:
            public_server=CertificateServer((bind_host,args.certificate_port),public)
            registry.certificate_port=public_server.server_port
            registry.ca_fingerprint=hashlib.sha256(Path(public).read_bytes()).hexdigest().upper()
        registry.shutdown=server.shutdown
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
        print_running(ui_url,registry.global_status(),registry.directory/'server.log' if os.environ.get('OIU_DAEMON_START_ID') else 'terminal (--foreground)')
        if hasattr(radio,'startup') and radio.startup.get('error'):
            print('Bluetooth startup: '+radio.startup['error'],file=sys.stderr,flush=True)
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
    mode=start.add_mutually_exclusive_group()
    mode.add_argument('--foreground',action='store_true',help='Run in the terminal for debugging (default: background)')
    mode.add_argument('--daemon',action='store_true',help=argparse.SUPPRESS)
    start.add_argument('--host',help='LAN IPv4 address; detected automatically')
    start.add_argument('--port',type=int,default=27891)
    start.add_argument('--ui-port',type=int,default=27893)
    start.add_argument('--cert'); start.add_argument('--key')
    start.add_argument('--helper',help='Override native macOS helper path')
    start.add_argument('--local-http',action='store_true',help='Localhost HTTP development mode')
    start.add_argument('--capture-only',action='store_true',help='Explicitly disable HID for diagnostics')
    start.add_argument('--certificate-port',type=int,default=27892)
    start.add_argument('--no-certificate-server',action='store_true')
    start.add_argument('--no-print-secrets',action='store_true',help=argparse.SUPPRESS)
    sub.add_parser('devices'); sub.add_parser('mcp'); sub.add_parser('stop')
    sub.add_parser('bluetooth',help='List Bluetooth-connected HID phones')
    for name in ('new','add'):
        add=sub.add_parser(name,help='Select a connected Bluetooth phone and print its private link + QR')
        add.add_argument('--bt',help='Exact Bluetooth peer ID (otherwise choose interactively)')
    link=sub.add_parser('link',help='Show an existing phone sharing link + QR')
    link.add_argument('device')
    remove=sub.add_parser('remove',help='Remove a phone and revoke its sharing link')
    remove.add_argument('device_pos',nargs='?')
    remove.add_argument('--device')
    status=sub.add_parser('status'); status.add_argument('--device',required=True)
    act=sub.add_parser('action'); act.add_argument('--device',required=True)
    act.add_argument('operation'); act.add_argument('--arguments',default='{}')
    register=sub.add_parser('register-agents',help='Register detectable MCP clients and install the agent skill')
    register.add_argument('--home',default=str(Path.home()))
    register.add_argument('--uninstall',action='store_true')
    register.add_argument('--skill',help='Skill source path (setup supplies its checked-out copy)')
    args=parser.parse_args()
    try:
        if args.command=='serve':
            if not args.foreground:

                from .service import daemon
                result=daemon(args,sys.argv[1:],call)
                print_running(result['url'],result,result['log'])
            else: serve(args,parser)
        elif args.command=='stop':
            from .service import stop
            print(json.dumps(stop(args,call),indent=2))
        elif args.command=='register-agents':
            from .registrations import configure
            configure(Path(args.home),Path(sys.executable).parent/('open-iphone-use.exe' if os.name=='nt' else 'open-iphone-use'),args.skill,args.uninstall)
        elif args.command=='devices': print(json.dumps(call(args,'/api/devices'),indent=2))
        elif args.command=='bluetooth': print(json.dumps(call(args,'/api/bluetooth'),indent=2))
        elif args.command in ('new','add'):
            candidates=[p for p in call(args,'/api/bluetooth') if p['available']]
            if not candidates: raise ValueError('No unadded Bluetooth HID phones. Pair/connect in AssistiveTouch first; both keyboard and pointer subscriptions are required.')
            if args.bt:
                selected=next((p for p in candidates if p['id']==args.bt),None)
                if selected is None: raise ValueError('Selected Bluetooth phone is disconnected or already added')
            elif len(candidates)==1: selected=candidates[0]
            else:
                for i,p in enumerate(candidates,1): print(f"{i}. {p['name']} [{p['id']}]",file=sys.stderr)
                if not sys.stdin.isatty(): raise ValueError('Several phones connected; choose explicitly with new --bt <id>')
                try: choice=int(input('Choose phone number: '))
                except (ValueError,EOFError): raise ValueError('Invalid selection') from None
                if not 1<=choice<=len(candidates): raise ValueError('Invalid selection')
                selected=candidates[choice-1]
            result=call(args,'/api/devices',{'bt_id':selected['id']})
            show_link(args,result)
        elif args.command=='link':
            result=next((d for d in call(args,'/api/devices') if d['device_id']==args.device),None)
            if result is None: raise ValueError('Unknown device')
            show_link(args,result)
        elif args.command=='remove':
            device=args.device or args.device_pos
            if not device or not device.isalnum(): raise ValueError('Choose a valid device ID')
            if args.device and args.device_pos and args.device!=args.device_pos: raise ValueError('Conflicting device IDs')
            print(json.dumps(call(args,f'/api/devices/{device}',method='DELETE'),indent=2))
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
