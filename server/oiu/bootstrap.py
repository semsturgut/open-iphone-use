"""Runtime discovery, per-installation CA and renewed LAN HTTPS certificates."""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def private_file(path, data):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as file: file.write(data)
    os.replace(temporary, path)


def lan_address():
    # UDP connect selects the route without transmitting application data.
    candidates = []
    for target in ('192.0.2.1', '1.1.1.1'):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
                channel.connect((target, 9))
                candidates.append(channel.getsockname()[0])
        except OSError: pass
    try: candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError: pass
    for candidate in candidates:
        address = ipaddress.ip_address(candidate)
        if address.is_private and not address.is_loopback and not address.is_link_local: return candidate
    raise OSError('No private LAN IPv4 address found. Join the iPhone’s Wi-Fi or choose serve --host YOUR_LAN_IP. Use --local-http only for local development')


def certificates(directory, host):
    directory = Path(directory)/'certificates'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_key,root_pem = directory/'root-ca.key',directory/'root-ca.pem'
    now=datetime.now(timezone.utc)
    if root_key.exists() != root_pem.exists():
        raise ValueError('CA certificate/key pair is incomplete. Restore its backup or move the certificates directory aside and re-trust a new CA')
    if root_pem.exists():
        key=serialization.load_pem_private_key(root_key.read_bytes(),password=None)
        ca=x509.load_pem_x509_certificate(root_pem.read_bytes())
        if ca.not_valid_after_utc <= now+timedelta(days=7): raise ValueError('Local CA is expiring. Renew it and explicitly trust the replacement on each iPhone')
        if key.public_key().public_numbers()!=ca.public_key().public_numbers(): raise ValueError('CA key does not match certificate')
    else:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Open iPhone Use Local Root CA')])
        ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
            .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False).sign(key,hashes.SHA256()))
        private_file(root_key,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        private_file(root_pem,ca.public_bytes(serialization.Encoding.PEM))
    public=directory/'root-ca.cer'
    private_file(public,ca.public_bytes(serialization.Encoding.DER))
    names=[x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    try: names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError: names.append(x509.DNSName(host))
    cert_path,key_path=directory/'server.pem',directory/'server.key'
    renew=True
    if cert_path.exists() and key_path.exists():
        cert=x509.load_pem_x509_certificate(cert_path.read_bytes())
        renew=(cert.not_valid_after_utc<=now+timedelta(days=7) or
               cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value != x509.SubjectAlternativeName(names))
    if renew:
        server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Open iPhone Use')]))
              .issuer_name(ca.subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
              .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
              .add_extension(x509.SubjectAlternativeName(names),critical=False)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
              .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),critical=True)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),critical=False).sign(key,hashes.SHA256()))
        private_file(key_path,server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        private_file(cert_path,cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_path),str(key_path),str(root_pem),str(public)


class LANOrigin:
    """Refresh auto-detected capture addresses and the live TLS context together.

    Called by the authenticated setup endpoint. Explicit hosts/custom certificates
    remain fixed. The CA stays the same, so owner trust does not need to change.
    """
    def __init__(self, directory, host, port, context, changed):
        self.directory, self.host, self.port = directory, host, port
        self.context, self.changed = context, changed
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            host = lan_address()
            if host != self.host:
                cert, key, _, _ = certificates(self.directory, host)
                self.context.load_cert_chain(cert, key)
                origin = f'https://{host}:{self.port}'
                self.changed(origin)
                self.host = host
            return f'https://{self.host}:{self.port}'


def mac_helper():
    selected=os.environ.get('OIU_MAC_HELPER')
    candidates=[Path(selected)] if selected else []
    # setup places a native executable beside its runtime (no application bundle needed).
    candidates.append(Path(sys.executable).parent/'open-iphone-use-headless')
    root=Path(__file__).resolve().parents[2]
    for configuration in ('release','debug'):
        candidates.append(root/'macos'/'.build'/configuration/'open-iphone-use-headless')
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate,os.X_OK): return str(candidate.resolve())
    if (root/'macos'/'Package.swift').exists() and shutil.which('swift'):
        print('Building the headless macOS Bluetooth helper…',file=sys.stderr)
        subprocess.run(['swift','build','--package-path',str(root/'macos'),'-c','release','--product','open-iphone-use-headless'],check=True)
        return str((root/'macos'/'.build'/'release'/'open-iphone-use-headless').resolve())
    raise OSError('Native macOS helper not found. Run sh setup.sh from this checkout, or set OIU_MAC_HELPER to a built open-iphone-use-headless executable')
