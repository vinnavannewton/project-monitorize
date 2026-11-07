"""Same-user command transport for the Monitorize desktop controller."""

import configparser
import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time


_MAX_MESSAGE = 8192
_ACTIONS = ('start', 'stop', 'reload', 'status', 'preset')
_UNIT = 'app-monitorize-host-cli.service'
_APP_ID = 'com.vinnavan.Monitorize'


def _flatpak():
    return Path('/.flatpak-info').is_file()


def _flatpak_instance():
    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read('/.flatpak-info')
    try:
        return metadata['Instance']
    except KeyError as error:
        raise RuntimeError('The current Monitorize Flatpak deployment could not be identified.') from error


def _profile_identity():
    from monitorize.config.host_config import config_path
    from monitorize.platform.utils import LINUX_DIR

    build = str(Path(LINUX_DIR).resolve())
    if _flatpak():
        build = _flatpak_instance().get('app-path', build)
    return [build, str(config_path())]


def _build():
    from monitorize.platform.utils import LINUX_DIR

    if _flatpak():
        return 'flatpak'
    return 'source' if (Path(LINUX_DIR).parent / 'pyproject.toml').is_file() else 'native'


def _endpoint():
    directory = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    return directory / 'app' / _APP_ID / 'monitorize-host-v1.sock'


def claim_desktop():
    """Prevent competing builds from owning displays in the same user session."""
    directory = _endpoint().parent
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(directory / 'desktop.lock', os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'r+b')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    except OSError:
        lock.close()
        raise
    return lock


def _peer_uid(peer):
    size = struct.calcsize('3i')
    return struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size))[1]


def _message(value):
    return (json.dumps(value, ensure_ascii=True, separators=(',', ':')) + '\n').encode()


def _request(action, restart, preset):
    path = _endpoint()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Monitorize control socket has an unexpected type or owner.')
    try:
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(250 if action == 'reload' else 125)
            peer.connect(str(path))
            if _peer_uid(peer) != os.getuid():
                raise RuntimeError('Monitorize control process has an unexpected owner.')
            peer.sendall(_message(dict(action=action, restart=restart, preset=preset,
                                       profile=_profile_identity())))
            with peer.makefile('rb') as reader:
                line = reader.readline(_MAX_MESSAGE + 1)
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    except OSError as error:
        raise RuntimeError(f'Monitorize desktop controller did not reply: {error}') from error
    try:
        if not line.endswith(b'\n') or len(line) > _MAX_MESSAGE:
            raise ValueError()
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError()
    except (UnicodeError, ValueError) as error:
        raise RuntimeError('Invalid response from the Monitorize desktop controller.') from error
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


def _launch():
    from monitorize.platform.utils import LINUX_DIR

    unit = _UNIT
    host = []
    options = []
    if _flatpak():
        unit = 'app-monitorize-flatpak-host-cli.service'
        host = ['flatpak-spawn', '--host', '--directory=/']
        from monitorize.desktop.controller_launcher import require_local_graphical_session

        require_local_graphical_session(host=host)
        instance = _flatpak_instance()
        branch, arch, commit = (instance.get(key) for key in ('branch', 'arch', 'app-commit'))
        if not all((branch, arch, commit)):
            raise RuntimeError('The current Monitorize Flatpak branch or revision could not be identified.')
        launch = ['flatpak', 'run', f'--branch={branch}', f'--arch={arch}', f'--commit={commit}',
                  _APP_ID, '--host-controller', '--start-in-tray']
    else:
        launch = [sys.executable, '-m', 'monitorize.desktop.controller_launcher', '--host-controller']
        options.append(f'--working-directory={LINUX_DIR}')
        for name in ('MONITORIZE_SUNSHINE_BIN', 'MONITORIZE_SUNSHINE_ASSETS_DIR',
                     'SUNSHINE_CONFIG_DIR', 'XDG_CONFIG_HOME', 'PATH', 'PYTHONPATH',
                     'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH'):
            if name in os.environ:
                options.append(f'--setenv={name}={os.environ[name]}')
    command = [*host, 'systemd-run', '--user', '--collect', '--slice=app.slice',
               f'--unit={unit}', *options, '--', *launch]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'Cannot launch the Monitorize desktop controller: {error}') from error
    if result.returncode and 'already exists' not in result.stderr:
        raise RuntimeError(result.stderr.strip() or 'Could not launch the Monitorize desktop controller.')
    return unit


def _validate_action(action, restart, preset):
    if (action not in _ACTIONS or type(restart) is not bool or (restart and action != 'reload')
            or (action == 'preset' and (not isinstance(preset, str) or not 1 <= len(preset.strip()) <= 32))
            or (action != 'preset' and preset is not None)):
        raise ValueError('Unsupported desktop control request.')


def request(action, *, restart=False, preset=None):
    """Contact the owning desktop or persist settings while it is stopped."""
    _validate_action(action, restart, preset)
    result = _request(action, restart, preset)
    if result is not None:
        return result
    if action in ('start', 'preset'):
        unit = _launch()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(0.15)
            result = _request(action, restart, preset)
            if result is not None:
                return result
        raise RuntimeError(
            'No Monitorize controller became available. Check for an active local graphical session '
            f'and inspect journalctl --user -u {unit}.'
        )
    from monitorize.config import host_config

    status = dict(state='stopped', controller=False, ready=False, instances=[],
                  remote_login=False, listeners=[], restart_pending=False, running_revision=None,
                  build=_build(), profile=str(host_config.config_path()))
    if action == 'reload':
        result = host_config.reload_config(restart=restart)
        return dict(status, saved_revision=result['saved_revision'],
                    message='Configuration saved for the next start.')
    return dict(status, saved_revision=host_config.saved_revision())


def serve(window):
    """Install the command endpoint on the already-claimed desktop singleton."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket

    from monitorize.config.host_config import saved_revision, streaming_revision, sunshine_revision
    from monitorize.platform.sunshine_service import is_sunshine_running, is_sunshine_settings_instance
    from monitorize.platform.sunshine_ports import get_sunshine_port

    backend = window.backend
    identity = _profile_identity()
    path = _endpoint()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if path.exists():
        try:
            info = path.lstat()
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.2)
                probe.connect(str(path))
        except ConnectionRefusedError:
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                QLocalServer.removeServer(str(path))
        except OSError:
            pass
    if not server.listen(str(path)):
        raise RuntimeError(server.errorString())

    running_revision = saved_revision()
    running_streaming_revision = streaming_revision()
    running_sunshine_revision = sunshine_revision()
    active_preset = None
    operation = None
    timer = QTimer(server)
    timer.setInterval(100)

    def status():
        ready = backend.sessionRunning
        remote = backend.remoteLoginEnabled and ready
        instances = [i for i in (1, 2) if is_sunshine_running(i) and not is_sunshine_settings_instance(i)]
        if remote:
            instances = [1]
        return dict(controller=True, ready=ready, remote_login=remote, instances=instances,
                    listeners=[dict(instance=i, address=backend.localIp, port=get_sunshine_port(i))
                               for i in instances] if ready and backend.localIp else [],
                    build=_build(), profile=identity[1],
                    state=('ready' if ready else 'busy' if backend.sessionBusy or operation else
                           'waiting' if backend.isStreaming else 'stopped'),
                    saved_revision=saved_revision(),
                    running_revision=running_revision if ready and active_preset is None else None,
                    active_preset=active_preset if ready else None,
                    restart_pending=(backend.configurationRestartRequired
                                     or (ready and running_streaming_revision != streaming_revision())))

    def reply(peer, result):
        if peer is not None and peer.state() == QLocalSocket.LocalSocketState.ConnectedState:
            peer.write(_message(result))
            peer.disconnectFromServer()

    def finish(error=None):
        nonlocal operation, running_revision, running_streaming_revision, running_sunshine_revision, active_preset
        active, operation = operation, None
        timer.stop()
        if not error:
            active_preset = active['preset']
            if active['action'] != 'stop':
                running_revision = active['revision']
                running_streaming_revision = active['streaming_revision']
                running_sunshine_revision = active['sunshine_revision']
        if error and active['action'] == 'reload' and active['sunshine_revision'] != running_sunshine_revision:
            error += ' Advanced Sunshine edits are still saved and may be used by the relaunched engine.'
        reply(active['peer'], dict(error=error) if error else status())

    def advance():
        if operation is None:
            return
        try:
            action = operation['action']
            if action == 'reload':
                result = operation.get('result')
                if result is not None:
                    success, message = result
                    finish(None if success else message)
                    return
                if not backend._restart.busy:
                    finish('The configuration restart was cancelled. Your saved changes are retained.')
                    return
            if action == 'stop' and backend._owned_session_stopped():
                finish()
                return
            if action in ('start', 'preset') and backend._configuration_ready():
                if action == 'start':
                    backend.markHostSessionVerified()
                finish()
                return
            if action in ('start', 'preset') and not backend.sessionBusy:
                error = backend._configuration_failure()
                if error:
                    backend.stopSession()
                    finish(error)
                    return
            if time.monotonic() >= operation['deadline']:
                if action in ('start', 'preset'):
                    backend.stopSession()
                finish(f'Monitorize {action} did not finish in time. Check status and the application log.')
        except (OSError, RuntimeError, ValueError) as error:
            finish(str(error))

    def restart_finished(success, message):
        nonlocal running_revision, running_streaming_revision, running_sunshine_revision, active_preset
        if operation is not None and operation['action'] == 'reload':
            operation['result'] = success, message
        elif success:
            active_preset = None
            running_revision = saved_revision()
            running_streaming_revision = streaming_revision()
            running_sunshine_revision = sunshine_revision()

    backend._restart.finished.connect(restart_finished)
    timer.timeout.connect(advance)

    def dispatch(peer, line):
        nonlocal operation, running_revision, running_streaming_revision, running_sunshine_revision
        try:
            data = json.loads(line)
            if not isinstance(data, dict) or set(data) != {'action', 'profile', 'restart', 'preset'}:
                raise ValueError('Unsupported desktop control request.')
            _validate_action(data['action'], data['restart'], data['preset'])
            if data['profile'] != identity:
                raise ValueError('A different Monitorize installation owns this desktop session.')
            action = data['action']
            if action == 'status':
                reply(peer, status())
                return
            if operation is not None or backend.applyingConfiguration or backend.systemSetupRunning:
                raise RuntimeError('Monitorize is completing another operation. Check status and retry.')
            if action == 'start' and backend.sessionRunning:
                reply(peer, status())
                return
            if action == 'stop' and backend._owned_session_stopped():
                reply(peer, status())
                return
            if action != 'stop' and backend.sessionBusy:
                raise RuntimeError('Monitorize is still changing the session. Check status and retry.')
            operation = dict(action=action, peer=peer, preset=data['preset'],
                             deadline=time.monotonic() + (240 if action == 'reload' else 110),
                             revision=saved_revision(), streaming_revision=streaming_revision(),
                             sunshine_revision=sunshine_revision())
            if action == 'reload':
                result = backend.reloadHostConfiguration(
                    restart=data['restart'], streaming_revision=running_streaming_revision)
                operation['revision'] = result['saved_revision']
                operation['streaming_revision'] = result['streaming_revision']
                operation['sunshine_revision'] = result['sunshine_revision']
                if not result['restarting']:
                    operation = None
                    if not result.get('restart_pending'):
                        running_revision = result['saved_revision']
                        running_streaming_revision = result['streaming_revision']
                        running_sunshine_revision = result['sunshine_revision']
                    reply(peer, dict(status(), message=result['message']))
                    return
            elif action == 'start':
                backend.startHostSession()
            elif action == 'preset':
                backend.startHostPreset(data['preset'])
            else:
                backend.stopSession()
            timer.start()
            advance()
        except (OSError, RuntimeError, ValueError) as error:
            if operation is not None and operation['peer'] is peer:
                operation = None
                timer.stop()
            reply(peer, dict(error=str(error) or 'Invalid Monitorize control request.'))

    def accept():
        while server.hasPendingConnections():
            peer = server.nextPendingConnection()
            try:
                with socket.fromfd(int(peer.socketDescriptor()), socket.AF_UNIX, socket.SOCK_STREAM) as raw:
                    if _peer_uid(raw) != os.getuid():
                        raise PermissionError()
            except (OSError, TypeError, ValueError):
                peer.abort()
                peer.deleteLater()
                continue
            peer.setReadBufferSize(_MAX_MESSAGE + 1)
            expiry = QTimer(peer)
            expiry.setSingleShot(True)
            expiry.timeout.connect(peer.abort)
            expiry.start(2000)

            def forget(peer=peer):
                if operation is not None and operation['peer'] is peer:
                    operation['peer'] = None
                peer.deleteLater()

            def receive(peer=peer, expiry=expiry):
                if peer.canReadLine():
                    line = bytes(peer.readLine(_MAX_MESSAGE + 1))
                    expiry.stop()
                    peer.readyRead.disconnect()
                    if len(line) <= _MAX_MESSAGE and line.endswith(b'\n'):
                        dispatch(peer, line)
                    else:
                        peer.abort()
                elif peer.bytesAvailable() > _MAX_MESSAGE:
                    peer.abort()

            peer.disconnected.connect(forget)
            peer.readyRead.connect(receive)
            receive()

    server.newConnection.connect(accept)
    accept()
    return server
