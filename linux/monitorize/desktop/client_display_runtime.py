"""Private launch-time display broker for independently managed Sunshine slots.

Only a paired-client identity supplied by our managed engine may own a slot.
The socket is deliberately not an externally reachable control API.
"""
from dataclasses import dataclass, field
from monitorize.platform.sunshine_ports import sunshine_port_conflict

import copy
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer

from monitorize.platform.sunshine_service import (
    get_sunshine_candidates, sunshine_supports_client_display_protocol,
    get_sunshine_process, get_sunshine_exit_code,
    start_sunshine, stop_sunshine, sync_sunshine_stream_config, save_sunshine_config,
    get_sunshine_config_dir, get_sunshine_log_size, get_sunshine_startup_status,
    is_sunshine_running, is_sunshine_settings_instance,
)
from monitorize.platform.utils import LINUX_DIR
from monitorize.platform.gpu_discovery import resolve_encoding_gpu

MAX_MESSAGE = 65536


def supports_control_protocol(instance=1, *, marker=b'MONITORIZE_CONTROL_PROTOCOL=1'):
    """Fail closed for old engines, scanning in bounded chunks without spawning tools."""
    return any(sunshine_supports_client_display_protocol(command[0], marker)
               for command in get_sunshine_candidates(instance))


@dataclass
class ClientSlot:
    instance: int
    config: dict
    status: str = 'waiting'
    owner: str = ''
    request_id: str = ''
    error: str = ''
    target: dict = field(default_factory=dict)
    process: object = None
    pending: list = field(default_factory=list)
    buffer: str = ''
    requested: tuple = ()
    deadline: object = None
    grace: object = None
    retry: object = None
    failures: int = 0
    manual: bool = False
    log_offset: int = 0
    log_identity: tuple = ()
    startup_deadline: float = 0
    intentional_recycle: bool = False
    healthy_since: float = 0
    permanent_failure: bool = False
    ended_requests: list = field(default_factory=list)
    preparation_error: str = ''


class ClientDisplayRuntime(QObject):
    changed = pyqtSignal()
    logAppended = pyqtSignal(str, str)

    def __init__(self, parent=None, *, runtime_dir=None):
        super().__init__(parent)
        self.slots = {}
        self.retiring = {}
        self.cleanup_blocked = {}
        self._clean_holders = set()
        self._outcome_buffers = {}
        self.guardian = None
        self.error = ''
        self._enrollment_pause = None
        self.token = secrets.token_hex(32)
        base = runtime_dir or os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir()
        self._directory = tempfile.mkdtemp(prefix='monitorize-client-', dir=base)
        os.chmod(self._directory, 0o700)
        self.socket_path = os.path.join(self._directory, 'control.sock')
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._connections = {}
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_engines)

    @property
    def active(self):
        return bool(self.slots)

    @property
    def running(self):
        return self.active

    @property
    def busy(self):
        return any(s.status in ('preparing', 'starting', 'stopping') for s in self.slots.values())

    @property
    def cards(self):
        return [dict(instance=s.instance, status=s.status, client_id=s.owner, error=s.error,
                     target=dict(s.target)) for s in self.slots.values()]

    @staticmethod
    def configured_mode(config):
        resolution=str(config.get('resolution',config.get('res','1920x1080')))
        if resolution.lower()=='custom':
            width,height=int(config['custom_w']),int(config['custom_h'])
        else:width,height=map(int,resolution.lower().split('x'))
        fps=float(config.get('custom_fps') if str(config.get('fps')).lower()=='custom' else config.get('fps',60))
        if not 320<=width<=16384 or not 200<=height<=16384 or not math.isfinite(fps) or not 1<=fps<=1000:
            raise ValueError('Unsupported configured display mode.')
        if config.get('virtual_display_creator','native')!='vkms' and fps!=int(fps):
            raise ValueError('Fractional refresh requires VKMS.')
        return width,height,fps

    def start_slot(self, instance, config):
        if not self._validate_layout(config, instance):
            return False
        if instance not in (1, 2):
            self.error = 'Only display slots 1 and 2 are supported.'
            return False
        if instance in self.cleanup_blocked:
            self.error = self.cleanup_blocked[instance]
            return False
        if instance in self.retiring:
            self.error = 'Previous display cleanup is still pending.'
            return False
        if instance in self.slots:
            return True
        if config.get('use_client_display_settings',True) is False:
            try:self.configured_mode(config)
            except (ValueError,KeyError,TypeError) as error:
                self.error=str(error);return False
        creator = config.get('virtual_display_creator', 'native')
        if creator == 'vkms':
            from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient, MonitorizeVkmsError
            host = MonitorizeVkmsClient()
            try:
                host.require_unattended()
                if config.get('de') in ('hyprland', 'x11'):
                    host.require_managed_backend(config['de'])
                if config.get('de') == 'hyprland':
                    from monitorize.platform.hyprland_layout import HyprlandLayout
                    HyprlandLayout().require_vkms_lifecycle()
            except (MonitorizeVkmsError, RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                return False
            finally:
                host.close()
        de = str(config.get('de', '')).lower()
        if os.path.isfile('/.flatpak-info') or de not in ('kde', 'gnome', 'hyprland', 'sway', 'niri', 'cinnamon', 'cosmic', 'x11'):
            self.error = 'Client-controlled displays require a supported native desktop backend.'
            return False
        if creator != 'vkms' and de not in ('kde', 'gnome', 'hyprland', 'sway'):
            self.error = 'This desktop requires the VKMS display backend.'
            return False
        if not supports_control_protocol(instance):
            self.error = 'Rebuild or install Monitorize Sunshine with client display protocol v1.'
            return False
        if not supports_control_protocol(instance,marker=b'MONITORIZE_CODEC_NEGOTIATION=1'):
            self.error='Rebuild or install Monitorize Sunshine with codec negotiation protocol v1.'
            return False
        os.makedirs(self._directory, mode=0o700, exist_ok=True)
        if not self._server.isListening() and not self._server.listen(self.socket_path):
            self.error = self._server.errorString()
            return False
        if not self._claim_instance(instance):
            return False
        slot = ClientSlot(instance, dict(config))
        self.slots[instance] = slot
        if not self._launch(slot):
            if slot.permanent_failure:
                self.slots.pop(instance)
                self.changed.emit()
                return False
            slot.status = 'recovering'
        self._watchdog.start()
        self.changed.emit()
        return True

    def start_manual_slot(self, instance, config):
        """Prepare a fixed-mode card alongside client-controlled listeners."""
        if not self._validate_layout(config, instance):
            return False
        if not self._check_capture_setup(instance, config):
            return False
        if not supports_control_protocol(instance):
            self.error = 'Mixed display sessions require the Monitorize Sunshine control protocol v1 engine.'
            return False
        if instance in self.cleanup_blocked:
            self.error = self.cleanup_blocked[instance]
            return False
        if instance in self.retiring:
            self.error = 'Previous display cleanup is still pending.'
            return False
        if instance not in (1, 2) or instance in self.slots:
            return instance in self.slots
        if config.get('display_behavior', 'extend') != 'extend':
            os.makedirs(self._directory, mode=0o700, exist_ok=True)
            if not self._server.isListening() and not self._server.listen(self.socket_path):
                self.error = self._server.errorString()
                return False
        try:
            resolution = str(config.get('resolution', '1920x1080'))
            if resolution.lower() == 'custom':
                width, height = int(config['custom_w']), int(config['custom_h'])
            else:
                width, height = map(int, resolution.lower().split('x'))
            fps = float(config.get('custom_fps') if str(config.get('fps')).lower() == 'custom' else config.get('fps', 60))
            if not 320 <= width <= 16384 or not 200 <= height <= 16384 or not math.isfinite(fps) or not 1 <= fps <= 1000:
                raise ValueError('Unsupported manual mode.')
            if config.get('virtual_display_creator', 'native') != 'vkms' and fps != int(fps):
                raise ValueError('Fractional refresh requires VKMS.')
            if config.get('de') not in ('kde', 'gnome', 'hyprland', 'sway') and config.get('virtual_display_creator') != 'vkms':
                raise ValueError('Unsupported native display backend.')
        except (ValueError, KeyError, TypeError) as exc:
            self.error = str(exc)
            return False
        if not self._claim_instance(instance):
            return False
        slot = ClientSlot(instance, dict(config), status='preparing', manual=True)
        slot.requested = width, height, fps
        self.slots[instance] = slot
        self._prepare(slot)
        self.changed.emit()
        return True

    def _validate_layout(self, config, instance=1):
        from monitorize.platform.session_layout import layout_modes, guarded_holder_supported
        mode = config.get('display_behavior', 'extend')
        if mode not in layout_modes(config.get('de')):
            self.error = 'This display behavior is unavailable for the current display server.'
            return False
        if mode!='extend' and config.get('de')=='gnome' and config.get('virtual_display_creator','native')!='vkms':
            self.error='GNOME guarded primary and virtual-only modes currently require VKMS.'
            return False
        if mode != 'extend' and not guarded_holder_supported(config.get('de'), config.get('virtual_display_creator', 'native')):
            self.error = 'This display behavior requires the VKMS display backend.'
            return False
        if mode != 'extend' and not supports_control_protocol(instance, marker=b'MONITORIZE_LAYOUT_RECOVERY=1'):
            self.error = 'Rebuild Monitorize Sunshine with layout recovery protocol v1 before using this display behavior.'
            return False
        if self.slots and any(s.config.get('display_behavior', 'extend') != mode for s in self.slots.values()):
            self.error = 'All displays in a session must use the same display behavior.'
            return False
        return True

    def _claim_instance(self, instance):
        if is_sunshine_running(instance):
            if not is_sunshine_settings_instance(instance):
                self.error = f'Sunshine instance {instance} is already owned by an active session.'
                return False
            stop_sunshine(instance, clear_output_name=False)
        return True

    def _startup_status(self, slot):
        if slot.manual:
            return get_sunshine_startup_status(slot.instance)
        try:
            with open(os.path.join(get_sunshine_config_dir(slot.instance), 'sunshine.log'), 'rb') as source:
                info = os.fstat(source.fileno())
                rotated = slot.log_identity and slot.log_identity != (info.st_dev, info.st_ino)
                source.seek(0 if rotated or info.st_size < slot.log_offset else slot.log_offset)
                log = source.read(MAX_MESSAGE).decode('utf-8', 'replace')
        except OSError:
            return 'pending', ''
        if 'MONITORIZE_CODEC_PROBE_FAILED' in log:
            slot.permanent_failure = True
            return 'failed', f"Selected encoder/codec ({slot.config.get('encoder', 'Auto')} / {slot.config.get('codec', 'Auto')}) failed its codec test. Inspect Sunshine logs; no codec fallback was used."
        conflict = sunshine_port_conflict(log)
        if conflict:
            slot.permanent_failure = True
            return 'failed', conflict
        if 'Fatal:' in log:
            return 'failed', 'Sunshine reported a fatal startup error; inspect its log.'
        if 'MONITORIZE_CONTROL_PROTOCOL=1' in log and 'Configuration UI available at' in log:
            return 'ready', ''
        return 'pending', ''

    def _mark_log_start(self, slot):
        slot.log_offset = get_sunshine_log_size(slot.instance)
        try:
            info = os.stat(os.path.join(get_sunshine_config_dir(slot.instance), 'sunshine.log'))
            slot.log_identity = info.st_dev, info.st_ino
        except OSError:
            slot.log_identity = ()

    def _startup_complete(self, slot):
        state, message = self._startup_status(slot)
        if state == 'ready':
            slot.status = 'streaming' if slot.manual else 'waiting'
            slot.intentional_recycle = False
            slot.healthy_since = time.monotonic()
            slot.error = ''
            return True
        slot.status = 'starting'
        if state == 'failed' or time.monotonic() >= slot.startup_deadline:
            stop_sunshine(slot.instance)
            self._cleanup(slot)
            slot.error = self.error = message or 'Sunshine listener startup timed out.'
            slot.status = 'recovering' if not slot.manual and not slot.permanent_failure and (slot.intentional_recycle or slot.config.get('client_restart_on_crash', False)) else 'error'
        return False

    def _gpu(self, config):
        gpu_id = config.get('gpu_id', '')
        device = resolve_encoding_gpu(config.get('encoder', 'Auto'), gpu_id)
        if gpu_id and not device:
            raise ValueError(f'Selected encoding GPU {gpu_id} is unavailable.')
        adapter = device.get('render_node', '') if device else ''
        environment = {}
        if device and str(config.get('encoder', '')).lower() in ('nvidia', 'nvenc') and device.get('cuda_index'):
            environment['CUDA_VISIBLE_DEVICES'] = str(device['cuda_index'])
        return adapter, environment

    def _check_capture_setup(self, instance, config):
        from monitorize.desktop.configuration_state import capture_compatibility_error
        error=capture_compatibility_error(config.get('de',''),config.get('display_type','Extend'),
            config.get('virtual_display_creator','native'),config.get('capture','auto'))
        if error:
            self.error=error
            self.logAppended.emit('SUNSHINE','ERROR: '+error)
            return False
        from monitorize.desktop.streaming_controller import _sunshine_capture_method
        from monitorize.platform.sunshine_service import get_sunshine_kms_setup_error
        requested = config.get('capture', 'auto')
        capture = (_sunshine_capture_method(config.get('de', ''),
                   vkms=config.get('virtual_display_creator') == 'vkms')
                   if requested == 'auto' else requested)
        if capture == 'kms':
            message = get_sunshine_kms_setup_error(instance)
            if message:
                self.error = message
                self.logAppended.emit('SUNSHINE', 'ERROR: ' + message)
                return False
        return True

    def _launch(self, slot):
        if not self._check_capture_setup(slot.instance, slot.config):
            slot.permanent_failure = True
            slot.error = self.error
            slot.status = 'error'
            return False
        if slot.config.get('virtual_display_creator') == 'vkms':
            from monitorize.platform.monitorize_vkms_dbus import MonitorizeVkmsClient, MonitorizeVkmsError
            try:
                MonitorizeVkmsClient().require_unattended()
            except MonitorizeVkmsError as exc:
                slot.permanent_failure = True
                slot.error = self.error = str(exc)
                slot.status = 'error'
                return False
        if not supports_control_protocol(slot.instance):
            slot.permanent_failure = True
            slot.error = self.error = 'Monitorize Sunshine control protocol v1 is unavailable; rebuild or reinstall the engine.'
            slot.status = 'error'
            return False
        if not slot.manual and not supports_control_protocol(slot.instance,marker=b'MONITORIZE_CODEC_NEGOTIATION=1'):
            slot.permanent_failure=True
            slot.error=self.error='Rebuild or install Monitorize Sunshine with codec negotiation protocol v1.'
            slot.status='error'
            return False
        if not self._claim_instance(slot.instance):
            slot.error = self.error
            slot.status = 'error'
            return False
        config = slot.config
        try:
            adapter, environment = self._gpu(config)
        except ValueError as exc:
            slot.error = self.error = str(exc)
            slot.status = 'error'
            return False
        ok, message = sync_sunshine_stream_config('', config.get('encoder', 'Auto'),
            config.get('codec', 'Auto'), config.get('native_pen_touch', True),
            instance=slot.instance, adapter_name=adapter, capture='')
        if ok:
            ok, message = save_sunshine_config({'stream_audio': 'enabled' if config.get('enable_audio', True) else 'disabled'}, instance=slot.instance)
        if ok:
            self._mark_log_start(slot)
            slot.startup_deadline = time.monotonic() + 90
            ok, message = start_sunshine(instance=slot.instance, extra_environment={**environment,
                'MONITORIZE_CONTROL_SOCKET': self.socket_path,
                'MONITORIZE_CONTROL_TOKEN': self.token,
                'MONITORIZE_CONTROL_INSTANCE': str(slot.instance),
                'MONITORIZE_REQUIRE_CODEC_NEGOTIATION': '1',
                'MONITORIZE_REQUIRE_LAYOUT_RECOVERY': '1' if config.get('display_behavior', 'extend') != 'extend' else '0',
            })
        if not ok:
            self.error = slot.error = message
            slot.status = 'error'
        else:
            self._startup_complete(slot)
        return ok

    def _accept(self):
        while self._server.hasPendingConnections():
            peer = self._server.nextPendingConnection()
            self._connections[peer] = bytearray()
            peer.readyRead.connect(lambda p=peer: self._read(p))
            peer.disconnected.connect(lambda p=peer: self._disconnected(p))
            QTimer.singleShot(22000, lambda p=peer: p.abort() if p in self._connections else None)

    def _disconnected(self, peer):
        self._connections.pop(peer, None)
        for slot in list(self.slots.values()):
            if peer in slot.pending:
                slot.pending.remove(peer)
                if not slot.pending and slot.status == 'preparing':
                    self._cleanup(slot)
        peer.deleteLater()

    def _reply(self, peer, ok=True, **fields):
        if peer not in self._connections:
            return
        peer.write(json.dumps(dict(version=1, ok=ok, **fields), allow_nan=False).encode() + b'\n')
        peer.flush()
        peer.disconnectFromServer()

    def _read(self, peer):
        data = self._connections.get(peer)
        if data is None:
            return
        data.extend(bytes(peer.readAll()))
        if len(data) > MAX_MESSAGE:
            self._reply(peer, False, error='Request exceeds 64 KiB.')
            return
        if b'\n' not in data:
            return
        try:
            if data.count(b'\n') != 1 or not data.endswith(b'\n'):
                raise ValueError('One request is permitted per connection.')
            request = json.loads(bytes(data))
            self._request(peer, request)
        except (ValueError, TypeError, KeyError, OverflowError, RuntimeError, OSError) as exc:
            self._reply(peer, False, error=str(exc))

    def _request(self, peer, request):
        if self._enrollment_pause is not None:
            raise ValueError('Remote-login setup is pausing this listener; retry later.')
        if not isinstance(request, dict) or type(request.get('version')) is not int or request['version'] != 1:
            raise ValueError('Unsupported protocol version.')
        token = request.get('token')
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            raise ValueError('Unauthorized control request.')
        instance = request.get('instance')
        if type(instance) is not int or instance not in self.slots:
            raise ValueError('Unknown display slot.')
        slot = self.slots[instance]
        # Dup the descriptor: closing this Python socket must not close Qt's socket.
        with socket.fromfd(int(peer.socketDescriptor()), socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            pid, uid, _gid = struct.unpack('3i', raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        process = get_sunshine_process(instance)
        if uid != os.getuid() or process is None or not process.pid or pid != process.pid:
            raise ValueError('Control peer is not the managed Sunshine process.')
        client = request.get('client_id')
        rid = request.get('request_id')
        if not isinstance(client, str) or not client or len(client) > 256 or not isinstance(rid, str) or not rid or len(rid) > 256:
            raise ValueError('Missing authenticated client or request identity.')
        if request.get('operation') == 'stream_ready' and rid in slot.ended_requests:
            raise ValueError('This stream has already disconnected.')
        if slot.manual:
            self._manual_lifecycle(peer, slot, request, client, rid)
            return
        if slot.owner and slot.owner != client:
            raise ValueError('Display slot is busy with another client.')
        operation = request.get('operation')
        if operation == 'prepare':
            if instance in self.cleanup_blocked:
                raise ValueError(self.cleanup_blocked[instance])
            if instance in self.retiring:
                raise ValueError('Previous display cleanup is still pending.')
            width, height, fps = request.get('width'), request.get('height'), request.get('fps')
            if type(width) is not int or type(height) is not int or not 320 <= width <= 16384 or not 200 <= height <= 16384 or type(fps) not in (float, int) or not math.isfinite(fps) or not 1 <= fps <= 1000:
                raise ValueError('Unsupported requested display mode.')
            if slot.config.get('use_client_display_settings',True) is False:
                width,height,fps=self.configured_mode(slot.config)
            mode = width, height, fps
            if slot.owner:
                if slot.requested != mode:
                    raise ValueError('A display request is already active.')
                slot.request_id = rid
                if slot.grace:
                    slot.grace.stop()
                if slot.target:
                    self._reply(peer, target=slot.target)
                else:
                    slot.pending.append(peer)
                return
            if slot.config.get('virtual_display_creator', 'native') != 'vkms' and fps != int(fps):
                raise ValueError('Fractional refresh requires the VKMS backend.')
            slot.owner, slot.request_id, slot.requested = client, rid, mode
            slot.status = 'preparing'
            slot.pending.append(peer)
            self._prepare(slot)
        elif slot.owner and slot.request_id and slot.request_id != rid:
            raise ValueError('Stale request identity.')
        elif operation in ('release', 'abort'):
            self._cleanup(slot)
            self._reply(peer)
        elif operation == 'stream_started':
            if not slot.target:
                raise ValueError('Display is not ready.')
            if slot.grace:
                slot.grace.stop()
            slot.ended_requests = [value for value in slot.ended_requests if value != rid]
            slot.status = 'streaming'
            self._reply(peer)
        elif operation == 'stream_ready':
            if not slot.target:
                raise ValueError('Display is not ready.')
            if self.guardian and slot.config.get('display_behavior', 'extend') != 'extend':
                self.guardian.command('stream', slot=instance, active=True)
            slot.status = 'streaming'
            self._reply(peer, layout_changed=bool(self.guardian and slot.config.get('display_behavior', 'extend') != 'extend'))
        elif operation == 'stream_stopped':
            if not slot.target:
                raise ValueError('No active display.')
            if self.guardian and slot.config.get('display_behavior', 'extend') != 'extend':
                self.guardian.command('stream', slot=instance, active=False)
            slot.status = 'reconnecting'
            slot.ended_requests = (slot.ended_requests + [rid])[-32:]
            if slot.grace:
                slot.grace.stop()
                slot.grace.deleteLater()
            slot.grace = QTimer(self)
            slot.grace.setSingleShot(True)
            slot.grace.timeout.connect(lambda: self._expire_grace(slot))
            from monitorize.config.settings import load_display_idle_timeout
            slot.grace.start(load_display_idle_timeout() * 1000)
            self._reply(peer)
        else:
            raise ValueError('Unknown control operation.')
        self.changed.emit()

    def _manual_lifecycle(self, peer, slot, request, client, rid):
        operation = request.get('operation')
        if operation not in ('stream_started', 'stream_ready', 'stream_stopped'):
            raise ValueError('Manual display slots do not accept client display requests.')
        if not slot.target or not self.guardian:
            raise ValueError('Fixed display is not ready for guarded streaming.')
        if slot.owner and (slot.owner != client or slot.request_id != rid):
            raise ValueError('Fixed display is already owned by another stream.')
        if operation in ('stream_started', 'stream_ready'):
            slot.owner, slot.request_id = client, rid
        if operation == 'stream_started':
            slot.ended_requests = [value for value in slot.ended_requests if value != rid]
        if operation == 'stream_ready':
            self.guardian.command('stream', slot=slot.instance, active=True)
            slot.status = 'streaming'
        elif operation == 'stream_stopped':
            self.guardian.command('stream', slot=slot.instance, active=False)
            slot.owner = slot.request_id = ''
            slot.status = 'ready'
            slot.ended_requests = (slot.ended_requests + [rid])[-32:]
        self._reply(peer, layout_changed=operation == 'stream_ready')
        self.changed.emit()

    def _prepare(self, slot):
        process = QProcess(self)
        slot.process = process
        slot.buffer = ''
        slot.preparation_error = ''
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONUNBUFFERED', '1')
        if slot.config.get('virtual_display_creator') == 'vkms':
            from monitorize.desktop.streaming_controller import _sunshine_capture_method
            requested = slot.config.get('capture', 'auto')
            capture = _sunshine_capture_method(slot.config['de'], vkms=True) if requested == 'auto' else requested
            environment.insert('MONITORIZE_VKMS_CAPTURE', capture)
            if not slot.manual:
                environment.insert('MONITORIZE_VKMS_UNATTENDED', '1')
        if slot.instance == 2 and 1 in self.slots and self.slots[1].target:
            environment.insert('MONITORIZE_GNOME_PRIMARY_OUTPUT', self.slots[1].target['output_name'])
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(LINUX_DIR)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_holder(slot, process))
        process.finished.connect(lambda *_: self._holder_failed(slot, process, 'Display holder exited.'))
        process.errorOccurred.connect(lambda *_: self._holder_failed(slot, process, 'Display holder failed.'))
        slot.deadline = QTimer(self)
        slot.deadline.setSingleShot(True)
        slot.deadline.timeout.connect(lambda: self._holder_failed(slot, process, 'Display preparation timed out.'))
        slot.deadline.start(20000)
        width, height, fps = slot.requested
        refresh = str(float(fps)) if slot.config.get('virtual_display_creator') == 'vkms' else str(int(fps))
        args = [str(width), str(height), refresh, 'primary' if slot.instance == 1 else 'additional',
                slot.config['de'], slot.config.get('virtual_display_creator', 'native')]
        if slot.config.get('display_behavior', 'extend') != 'extend':
            from monitorize.streaming.recovery_guardian import GuardianClient
            try:
                if self.guardian is None:
                    self.guardian = GuardianClient(slot.config['display_behavior'], slot.config['de'],
                                                   os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir())
                self.guardian.start()
                self.guardian.command('configure', mode=slot.config['display_behavior'])
                process.start(sys.executable, self.guardian.relay_args(slot.instance, args))
            except (RuntimeError, OSError) as exc:
                self._holder_failed(slot, process, str(exc))
        else:
            process.start(sys.executable, ['-m', 'monitorize.streaming.headless_virtual_display', *args])

    def _read_holder(self, slot, process):
        raw = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace')
        self.logAppended.emit('DISPLAY', raw)
        pending = self._outcome_buffers.get(process, '') + raw
        lines = pending.split('\n')
        self._outcome_buffers[process] = lines.pop()[-MAX_MESSAGE:]
        for line in lines:
            if line.startswith('MONITORIZE_EVENT '):
                try:
                    outcome = json.loads(line.split(' ', 1)[1])
                    if outcome.get('type') == 'headless_result' and outcome.get('cleanup_complete') is True:
                        self._clean_holders.add(process)
                except (ValueError, TypeError):
                    pass
        if self.slots.get(slot.instance) is not slot or slot.process is not process:
            return
        for diagnostic in raw.splitlines():
            if '[ERROR] ' in diagnostic:
                slot.preparation_error = diagnostic.split('[ERROR] ', 1)[1]
        slot.buffer += raw
        if len(slot.buffer) > MAX_MESSAGE:
            self._holder_failed(slot, process, 'Display holder output exceeds limit.')
            return
        while '\n' in slot.buffer:
            line, slot.buffer = slot.buffer.split('\n', 1)
            if line.startswith('MONITORIZE_EVENT '):
                try:
                    event = json.loads(line.split(' ', 1)[1])
                    if event.get('type') == 'headless_ready':
                        self._ready(slot, event)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
                    self._holder_failed(slot, process, str(exc))

    def _ready(self, slot, event):
        from monitorize.desktop.streaming_controller import _sunshine_capture_method
        output = str(event.get('name') or '')
        requested = slot.config.get('capture', 'auto')
        capture = _sunshine_capture_method(slot.config['de'], pipewire_node=event.get('node_id'), vkms=bool(event.get('vkms'))) if requested == 'auto' else requested
        # Recheck after display preparation in case the executable was replaced
        # (and lost its file capability) while the listener was waiting.
        if capture == 'kms' and not self._check_capture_setup(slot.instance, {**slot.config, 'capture': capture}):
            raise ValueError(self.error)
        if capture == 'x11':
            from monitorize.desktop.streaming_controller import _x11_capture_output
            output = _x11_capture_output(output)
        node = int(event.get('node_id') or 0)
        if not output or capture == 'portal' or (capture == 'pipewire_node' and node <= 0):
            raise ValueError('Backend did not provide an exact unattended capture target.')
        width, height, fps = slot.requested
        actual_width, actual_height = int(event.get('width') or width), int(event.get('height') or height)
        actual_fps = float(event.get('fps') or fps)
        if actual_width != width or actual_height != height or not math.isfinite(actual_fps) or not math.isclose(actual_fps, fps, rel_tol=0.001, abs_tol=0.01):
            raise ValueError('Display backend did not apply the requested mode.')
        slot.target = dict(output_name=output, capture=capture, pipewire_node=node,
            width=int(event.get('width') or width), height=int(event.get('height') or height),
            fps=float(event.get('fps') or fps), offset_x=int(event.get('offset_x') or 0), offset_y=int(event.get('offset_y') or 0))
        if self.guardian and slot.config.get('display_behavior', 'extend') != 'extend':
            self.guardian.command('ready', slot=slot.instance, name=output)
        slot.deadline.stop()
        slot.status = 'ready'
        if slot.manual:
            if not self._claim_instance(slot.instance):
                raise ValueError(self.error)
            config = slot.config
            adapter, environment = self._gpu(config)
            ok, message = sync_sunshine_stream_config(output, config.get('encoder', 'Auto'),
                config.get('codec', 'Auto'), config.get('native_pen_touch', True),
                instance=slot.instance, adapter_name=adapter, capture=capture)
            if ok:
                ok, message = save_sunshine_config({'stream_audio': 'enabled' if config.get('enable_audio', True) else 'disabled'}, instance=slot.instance)
            if ok:
                self._mark_log_start(slot)
                slot.startup_deadline = time.monotonic() + 20
                if self.guardian and slot.config.get('display_behavior', 'extend') != 'extend':
                    environment.update(MONITORIZE_CONTROL_SOCKET=self.socket_path,
                        MONITORIZE_CONTROL_TOKEN=self.token, MONITORIZE_CONTROL_INSTANCE=str(slot.instance),
                        MONITORIZE_LIFECYCLE_ONLY='1', MONITORIZE_REQUIRE_LAYOUT_RECOVERY='1')
                ok, message = start_sunshine(instance=slot.instance,
                    pipewire_node=node if capture == 'pipewire_node' else None,
                    offset_x=slot.target['offset_x'], offset_y=slot.target['offset_y'],
                    width=slot.target['width'], height=slot.target['height'],
                    extra_environment={**environment, 'MONITORIZE_CAPTURE_OUTPUT': output,
                        'MONITORIZE_REQUIRE_EXACT_OUTPUT': '1', 'SUNSHINE_PORTAL_TOKEN_SCOPE': 'extend'})
            if not ok:
                self._holder_failed(slot, slot.process, message)
                return
            self._startup_complete(slot)
            self._watchdog.start()
        pending, slot.pending = slot.pending, []
        for peer in pending:
            self._reply(peer, target=slot.target)
        self.changed.emit()

    def _holder_failed(self, slot, process, message):
        if self.slots.get(slot.instance) is slot and slot.process is process:
            if process:
                self._read_holder(slot, process)
            message = slot.preparation_error or message
            pending, slot.pending = slot.pending, []
            for peer in pending:
                self._reply(peer, False, error=message)
            stop_sunshine(slot.instance)
            slot.intentional_recycle = not slot.manual
            self._cleanup(slot)
            slot.error = message
            if slot.manual:
                slot.status = 'error'
            else:
                self._schedule_retry(slot, 0)
            self.changed.emit()

    def _cleanup(self, slot):
        for timer in (slot.deadline, slot.grace):
            if timer:
                timer.stop()
                timer.deleteLater()
        slot.deadline = slot.grace = None
        process, slot.process = slot.process, None
        if process and process.state() != QProcess.ProcessState.NotRunning:
            self.retiring[slot.instance] = process
            process.finished.connect(lambda code, _status: self._retired(slot.instance, process, code))
            process.write(b'quit\n')
            process.closeWriteChannel()
            if slot.config.get('display_behavior', 'extend') == 'extend':
                process.terminate()
            # Keep the holder alive until graceful backend cleanup has run. No
            # destructive forced VKMS cleanup or compositor policy bypass here.
            QTimer.singleShot(5000, lambda p=process: self._holder_cleanup_timeout(p))
        elif process and process.exitCode() != 0 and process not in self._clean_holders:
            self._block_cleanup(slot.instance)
            process.deleteLater()
        pending, slot.pending = slot.pending, []
        for peer in pending:
            self._reply(peer, False, error='Display request cancelled.')
        slot.owner = slot.request_id = ''
        slot.target = {}
        slot.requested = ()
        slot.healthy_since = 0
        slot.status = 'error' if slot.instance in self.cleanup_blocked else 'stopping' if slot.instance in self.retiring else 'waiting'
        self.changed.emit()

    def _block_cleanup(self, instance):
        message = f'Display {instance} cleanup or recovery failed. Inspect the display and recovery logs before starting another session.'
        self.cleanup_blocked[instance] = self.error = message
        self.logAppended.emit('DISPLAY', message)
        slot = self.slots.get(instance)
        if slot:
            slot.error, slot.status = message, 'error'

    def _retired(self, instance, process, exit_code=0):
        if self.retiring.get(instance) is not process:
            process.deleteLater()
            return
        slot = self.slots.get(instance)
        if slot:
            self._read_holder(slot, process)
        self.retiring.pop(instance)
        self._outcome_buffers.pop(process, None)
        if exit_code != 0 and process not in self._clean_holders:
            self._block_cleanup(instance)
        self._clean_holders.discard(process)
        slot = self.slots.get(instance)
        if slot and slot.status == 'stopping':
            slot.status = 'recovering' if slot.intentional_recycle else 'waiting'
        process.deleteLater()
        self.changed.emit()

    def _holder_cleanup_timeout(self, process):
        if process not in self.retiring.values():
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            self.error = 'Display cleanup is still pending; inspect the display holder log.'
            self.logAppended.emit('DISPLAY', self.error)
            self.changed.emit()

    def _expire_grace(self, slot):
        if self.slots.get(slot.instance) is not slot:
            return
        # Sunshine retains the hosted app/launch lease after network disconnect.
        # Recycle that one managed engine to release both sides together.
        self.restart_slot(slot.instance)

    def restart_slot(self, instance):
        slot = self.slots.get(instance)
        if slot is None:
            self.error = 'This display slot is not active.'
            return False
        if slot.manual:
            self.error = 'Stop and start the session to restart this fixed display.'
            return False
        if instance in self.cleanup_blocked or slot.permanent_failure:
            self.error = self.cleanup_blocked.get(instance) or slot.error
            return False
        if slot.retry:
            slot.retry.stop()
            slot.retry.deleteLater()
            slot.retry = None
        slot.intentional_recycle = True
        stop_sunshine(slot.instance)
        self._cleanup(slot)
        self._schedule_retry(slot, 0)
        return True

    def _schedule_retry(self, slot, delay):
        slot.status = 'recovering'
        slot.retry = QTimer(self)
        slot.retry.setSingleShot(True)
        slot.retry.timeout.connect(lambda s=slot: self._retry(s))
        slot.retry.start(delay * 1000)
        self.changed.emit()

    def _check_engines(self):
        if self.guardian and self.guardian.process and self.guardian.process.poll() is not None:
            from monitorize.streaming.recovery_guardian import recover_before_cleanup
            try:
                if not recover_before_cleanup():
                    raise RuntimeError('Independent display recovery is still pending.')
            except Exception as exc:
                self.error = str(exc)
                self.logAppended.emit('DISPLAY', self.error)
                return
        for slot in list(self.slots.values()):
            if slot.retry or slot.status in ('error', 'preparing', 'stopping'):
                continue
            if slot.status == 'starting':
                if get_sunshine_exit_code(slot.instance) is None:
                    self._startup_complete(slot)
                    self.changed.emit()
                    continue
            if slot.status != 'recovering' and get_sunshine_exit_code(slot.instance) is None:
                if slot.healthy_since and time.monotonic() - slot.healthy_since >= 60:
                    slot.failures = 0
                continue
            self._cleanup(slot)
            if slot.manual or not (slot.intentional_recycle or slot.config.get('client_restart_on_crash', False)):
                slot.status = 'error'
                slot.error = 'Sunshine exited unexpectedly.'
                self.changed.emit()
                continue
            delay = (2, 5, 15, 60)[min(slot.failures, 3)]
            slot.failures += 1
            self._schedule_retry(slot, delay)

    def _retry(self, slot):
        slot.retry.deleteLater()
        slot.retry = None
        if self.slots.get(slot.instance) is not slot:
            return
        if slot.instance in self.cleanup_blocked:
            slot.error = self.error = self.cleanup_blocked[slot.instance]
            slot.status = 'error'
            self.changed.emit()
            return
        if slot.instance in self.retiring:
            slot.retry = QTimer(self)
            slot.retry.setSingleShot(True)
            slot.retry.timeout.connect(lambda s=slot: self._retry(s))
            slot.retry.start(1000)
            return
        if not self._launch(slot) and not slot.permanent_failure:
            # Let the next watchdog cycle schedule the next bounded retry.
            slot.status = 'recovering'
        self.changed.emit()

    def stop_slot(self, instance):
        slot = self.slots.pop(instance, None)
        if not slot:
            return
        if slot.retry:
            slot.retry.stop()
            slot.retry.deleteLater()
        stop_sunshine(instance)
        self._cleanup(slot)
        if not self.slots:
            self._watchdog.stop()
        self.changed.emit()

    def pause_idle_for_enrollment(self):
        if self._enrollment_pause is not None:
            raise ValueError('Remote-login setup is already running.')
        if 2 in self.slots:
            raise ValueError('Stop Display 2 before enabling remote login.')
        if self.retiring or self.cleanup_blocked:
            raise ValueError('Wait for display cleanup before enabling remote login.')
        for slot in self.slots.values():
            if (slot.instance != 1 or slot.status != 'waiting' or slot.manual or slot.owner
                    or slot.request_id or slot.target or slot.pending or slot.process or slot.grace or slot.deadline):
                raise ValueError('Stop the current stream before enabling remote login.')
        snapshot={'configs':{i:copy.deepcopy(s.config) for i,s in self.slots.items()}}
        self._enrollment_pause=snapshot
        self._watchdog.stop()
        for instance in list(self.slots):self.stop_slot(instance)
        return snapshot

    def enrollment_pause_complete(self,snapshot):
        return (self._enrollment_pause is snapshot and not self.retiring and not self.cleanup_blocked
                and not any(is_sunshine_running(i) for i in snapshot['configs']))

    def resume_enrollment_pause(self,snapshot,*,instances=(1,)):
        if self._enrollment_pause is not snapshot:return
        self._enrollment_pause=None
        for instance,config in snapshot['configs'].items():
            if instance in instances:self.start_slot(instance,config)

    def stop(self):
        self._enrollment_pause=None  # Explicit Stop/Quit cancels restoration intent.
        for instance in list(self.slots):
            self.stop_slot(instance)
        for peer in list(self._connections):
            peer.abort()
        self._server.close()
        try:
            Path(self.socket_path).unlink(missing_ok=True)
            Path(self._directory).rmdir()
        except OSError:
            pass
