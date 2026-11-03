import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .core import Registry
from .http import Server
from .state import StateLease

DEFAULT_PORT = 27893


def connection(args):
    file=Path(args.state)/'connection.json'
    saved=json.loads(file.read_text()) if file.exists() else {}
    return (args.url or os.environ.get('OIU_URL') or saved.get('url', f'http://127.0.0.1:{DEFAULT_PORT}')).rstrip('/')


def call(args,path,data=None,method=None):
    request=Request(connection(args)+path,data=json.dumps(data).encode() if data is not None else None,method=method,
                    headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=95) as response: return json.load(response)
    except HTTPError as error:
        try: message=json.load(error).get('error',str(error))
        except ValueError: message=str(error)
        finally: error.close()
        raise ValueError(message) from None


def print_running(url,log):
    print('Open iPhone Use is running')
    print('Web UI:  '+url)
    print('Add a phone: connect it by USB, then open-iphone-use new')
    print('Stop:      open-iphone-use stop')
    print('Log:       '+str(log),flush=True)


def choose(candidates):
    if len(candidates)==1: return candidates[0]
    for i,p in enumerate(candidates,1): print(f"{i}. {p.get('name') or 'iPhone'} ({p['connection']}) [{p['udid']}]",file=sys.stderr)
    if not sys.stdin.isatty(): raise ValueError('Several phones connected; choose explicitly with new --udid <UDID>')
    try: choice=int(input('Choose phone number: '))
    except (ValueError,EOFError): raise ValueError('Invalid selection') from None
    if not 1<=choice<=len(candidates): raise ValueError('Invalid selection')
    return candidates[choice-1]


def serve(args):
    lease=StateLease(args.state)
    registry=None; server=None
    try:
        registry=Registry(args.state)
        registry.instance_id=os.environ.get("OIU_DAEMON_START_ID") or uuid.uuid4().hex
        def backend(device):
            from .coredevice import CoreDeviceBackend
            return CoreDeviceBackend(device,device.udid)
        if not args.no_control:
            registry.backend_factory=backend
            for device in registry.devices.values():
                try: device.backend=backend(device)
                except ValueError as error: print(f'{device.name}: {error}',file=sys.stderr,flush=True)
        server=Server(('127.0.0.1',args.port),registry)
        url=f'http://127.0.0.1:{server.server_port}'
        from .files import atomic_json
        atomic_json(registry.directory/'connection.json',dict(url=url,instance_id=registry.instance_id))
        registry.shutdown=server.shutdown
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
        print_running(url,registry.directory/'server.log' if os.environ.get('OIU_DAEMON_START_ID') else 'terminal (--foreground)')
        server.serve_forever()
    finally:
        if server: server.server_close()
        if registry: registry.close()
        lease.close()


def main():
    parser=argparse.ArgumentParser(description='Open iPhone Use — let AI agents use your iPhone over USB or Wi-Fi, via MCP')
    parser.add_argument('--state',default=os.environ.get('OIU_STATE',str(Path.home()/'.open-iphone-use')))
    parser.add_argument('--url',default=None)
    sub=parser.add_subparsers(dest='command',required=True)
    start=sub.add_parser('serve')
    mode=start.add_mutually_exclusive_group()
    mode.add_argument('--foreground',action='store_true',help='Run in the terminal for debugging (default: background)')
    mode.add_argument('--daemon',action='store_true',help=argparse.SUPPRESS)
    start.add_argument('--port',type=int,default=DEFAULT_PORT,help='Web UI port on 127.0.0.1')
    start.add_argument('--no-control',action='store_true',help='Diagnostics: list phones without connecting to them')
    sub.add_parser('devices',help='List added phones'); sub.add_parser('mcp'); sub.add_parser('stop')
    sub.add_parser('candidates',help='List connected phones (USB or Wi-Fi) that can be added')
    for name in ('new','add'):
        add=sub.add_parser(name,help='Add a connected phone')
        add.add_argument('--udid',help='Exact phone UDID (otherwise choose interactively)')
    remove=sub.add_parser('remove',help='Remove a phone and stop its control')
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
                print_running(result['url'],result['log'])
            else: serve(args)
        elif args.command=='stop':
            from .service import stop
            print(json.dumps(stop(args,call),indent=2))
        elif args.command=='register-agents':
            from .registrations import configure
            configure(Path(args.home),Path(sys.executable).parent/'open-iphone-use',args.skill,args.uninstall)
        elif args.command=='devices': print(json.dumps(call(args,'/api/devices'),indent=2))
        elif args.command=='candidates': print(json.dumps(call(args,'/api/candidates'),indent=2))
        elif args.command in ('new','add'):
            candidates=[p for p in call(args,'/api/candidates') if p['available']]
            if args.udid:
                selected=next((p for p in candidates if p['udid']==args.udid),None)
                if selected is None: raise ValueError('That phone is not connected or is already added')
            elif not candidates: raise ValueError('No phones to add. Connect the iPhone by cable and trust this Mac.')
            else: selected=choose(candidates)
            result=call(args,'/api/devices',{'udid':selected['udid']})
            print(f"{result['name']} [{result['device_id']}]: {result['control_status']}")
            print('Watch and control it in the Web UI; agents use it through MCP.')
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
