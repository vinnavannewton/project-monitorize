"""Instance-scoped Sunshine approval API with the managed host certificate pinned."""
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import re
import ssl

from monitorize.platform import sunshine_service as sunshine


def client_text(value, fallback):
    """Client labels are untrusted plain text; bound their size and remove controls."""
    if not isinstance(value, str):
        return fallback
    text = ''.join(c for c in value if c.isprintable()).strip()
    return text.encode('utf-8')[:128].decode('utf-8', errors='ignore') or fallback


@dataclass(frozen=True)
class PairingHost:
    instance: int
    certificate: bytes

    def request(self, method='GET', payload=None):
        """Only loopback, no proxies/redirects; send a PIN only to the pinned host."""
        connection = http.client.HTTPSConnection('127.0.0.1',
            sunshine.get_sunshine_https_port(self.instance),
            timeout=15 if method == 'POST' else 1,
            context=ssl._create_unverified_context())
        try:
            connection.connect()
            if connection.sock.getpeercert(binary_form=True) != self.certificate:
                raise ValueError('Sunshine identity changed. Request a new PIN in Moonlight.')
            data = json.dumps(payload).encode() if payload is not None else None
            connection.request(method, '/api/pin', body=data,
                               headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ValueError('Sunshine pairing API is unavailable. Try its Sunshine settings page.')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('Sunshine returned an invalid pairing response.')
            return body
        finally:
            connection.close()

    def pending(self):
        """Expose only valid approval IDs and bounded display labels."""
        rows = self.request().get('pairings')
        if not isinstance(rows, list) or len(rows) > 64:
            raise ValueError('Sunshine returned an invalid pending-client list.')
        result = []
        for row in rows:
            if not isinstance(row, dict) or not valid_id(row.get('id')):
                continue
            result.append(dict(id=row['id'], instance=self.instance,
                name=client_text(row.get('name'), 'Unknown client'),
                address=client_text(row.get('address'), 'Unknown address')))
        return result

    def approve(self, pairing_id, pin, name):
        if not valid_id(pairing_id) or not isinstance(pin, str) or not re.fullmatch(r'[0-9]{4}', pin):
            return False, 'Enter the four-digit PIN shown in Moonlight.'
        if not any(row['id'] == pairing_id for row in self.pending()):
            return False, 'This pairing request expired or was cancelled. Request a new PIN in Moonlight.'
        result = self.request('POST', dict(pairing_id=pairing_id, pin=pin,
                                         name=client_text(name, 'Moonlight client')))
        if result.get('status') is True and result.get('pairing_complete') is True:
            return True, 'Paired successfully.'
        return False, 'Pairing did not complete. Request a new PIN in Moonlight and try again.'

    def reject(self, pairing_id):
        if not valid_id(pairing_id):
            return False, 'This pairing request is no longer available.'
        result = self.request('DELETE', {'pairing_id': pairing_id})
        return (True, 'Pairing request rejected.') if result.get('status') is True else (
            False, 'This pairing request expired or was already cancelled.')


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{32}', value) is not None


def resolve_hosts(instances, remote=False):
    """Resolve only owned hosts' public TLS certificates, never password/private keys."""
    hosts = []
    for instance in instances:
        try:
            if instance == 1 and remote:
                from monitorize.remote_login.settings import read_settings
                from monitorize.platform.remote_login_identity import identity_paths
                settings = read_settings()
                if not settings.enabled or settings.uid != os.getuid():
                    continue
                cert = identity_paths(settings.directory).cert
            else:
                if not sunshine.is_sunshine_running(instance):
                    continue
                config = sunshine.get_saved_sunshine_config(instance)
                identity = sunshine._enrolled_manual_identity(instance)
                default = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config')) / 'monitorize' / f'sunshine-profile-{instance}' / 'sunshine' / 'credentials' / 'cacert.pem'
                cert = identity.cert if identity else Path(config.get('cert') or default)
            with Path(cert).open('r', encoding='ascii') as source:
                pem = source.read(65537)
            if len(pem) > 65536:
                continue
            hosts.append(PairingHost(instance, ssl.PEM_cert_to_DER_cert(pem)))
        except (OSError, ValueError, RuntimeError):
            continue
    return hosts
