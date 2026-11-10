"""Independent layout owner and display-holder supervisor for native KDE.

The GUI uses disposable relay processes. Only this detached manager owns holder
stdin and parent-death relationships, so GUI death cannot remove the last screen
before physical restoration. No shell commands or privileged execution are used.
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

from monitorize.platform.session_layout import KdeLayout, LayoutTransaction, borrowed_output_name, layout_supported, current_layout_backend, guarded_holder_supported

MAX_FRAME = 65536


def recovery_paths():
    session = '|'.join(os.environ.get(key, '') for key in ('XDG_SESSION_ID', 'WAYLAND_DISPLAY', 'DISPLAY'))
    suffix = hashlib.sha256(session.encode()).hexdigest()[:16]
    directory = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'monitorize' / 'recovery'
    return directory, suffix


def recover_before_cleanup():
    """Never let legacy stale-output cleanup race the independent owner."""
    directory, suffix = recovery_paths()
    if not directory.exists():
        return True
    lock = os.open(directory / f'{suffix}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        LayoutTransaction(current_layout_backend(), directory / f'{suffix}.json', 'primary').recover()
        return True
    finally:
        os.close(lock)


def _identity(pid):
    try:
        # comm can contain spaces and parentheses; starttime is field 22.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def _frame(data):
    return json.dumps(data, separators=(',', ':')).encode() + b'\n'


class GuardianClient:
    def __init__(self, mode, desktop, runtime_dir):
        self.mode, self.desktop = mode, desktop
        self.directory = Path(tempfile.mkdtemp(prefix='guardian-', dir=runtime_dir))
        self.directory.chmod(0o700)
        self.path = str(self.directory / 'control.sock')
        self.token = secrets.token_hex(32)
        self.process = None

    def start(self):
        if self.process and self.process.poll() is None:
            return
        if not layout_supported(self.desktop):
            raise RuntimeError('Display recovery is unavailable for the current display server.')
        Path(self.path).unlink(missing_ok=True)
        args = [sys.executable, '-m', __name__, 'serve', self.path, self.token, self.mode, str(os.getpid())]
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Display recovery guardian could not start. Check the recovery log.')
            if Path(self.path).exists():
                self.command('ping')
                return
            time.sleep(.02)
        raise RuntimeError('Display recovery guardian did not become ready.')

    def command(self, operation, **values):
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(30)
            peer.connect(self.path)
            peer.sendall(_frame(dict(token=self.token, operation=operation, **values)))
            data = b''
            while b'\n' not in data and len(data) <= MAX_FRAME:
                chunk = peer.recv(4096)
                if not chunk:
                    break
                data += chunk
            reply = json.loads(data)
            if not reply.get('ok'):
                raise RuntimeError(reply.get('error') or 'Display recovery guardian failed.')
            return reply

    def relay_args(self, slot, args):
        return ['-m', __name__, 'hold', self.path, self.token, str(slot), *args]


class Guardian:
    def __init__(self, path, token, mode, owner, transaction):
        self.path, self.token, self.owner = path, token, owner
        self.owner_identity = _identity(owner)
        self.layout = transaction
        self.lock = asyncio.Lock()
        self.holders = {}
        self.reserved = set()
        self.borrowed = set()
        self.pending_release = set()
        self.stopping = False
        self.recovery_pending = False
        self.server = None

    async def _layout(self, method, *args):
        return await asyncio.to_thread(getattr(self.layout, method), *args)

    async def release(self, slot):
        self.pending_release.add(slot)
        async with self.lock:
            await self._layout('prepare_release', slot)
            process = self.holders.get(slot)
            if process:
                if process.returncode is None:
                    try:
                        process.stdin.write(b'quit\n')
                        await process.stdin.drain()
                        process.stdin.close()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    # Holder owns compositor/VKMS cleanup. Never force-kill it.
                    try:
                        await asyncio.wait_for(process.wait(), 8)
                    except asyncio.TimeoutError:
                        raise RuntimeError('Display cleanup remains pending; guardian is retaining ownership.')
                if process.returncode:
                    observed = await asyncio.to_thread(self.layout.backend.outputs)
                    name = self.layout.targets.get(slot)
                    known = {o['name'] for o in self.layout.snapshot}
                    known.update(value for key, value in self.layout.targets.items() if key != slot)
                    leftovers = [o for o in observed if o.get('connected') and
                                 (o['name'] == name if name else o['name'] not in known)]
                    if leftovers:
                        raise RuntimeError('Display holder cleanup failed; inspect the recovery log.')
                    print(f'Guardian verified failed holder {slot} left no connected output.', flush=True)
                self.holders.pop(slot, None)
            await self._layout('release', slot)
            self.reserved.discard(slot)
            self.borrowed.discard(slot)
            self.pending_release.discard(slot)

    async def handle(self, reader, writer):
        slot = None
        hold = False
        holder_error = None
        try:
            raw = writer.get_extra_info('socket')
            _pid, uid, _gid = struct.unpack('3i', raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            request = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if uid != os.getuid() or not secrets.compare_digest(str(request.get('token', '')), self.token):
                raise RuntimeError('Unauthorized guardian connection.')
            operation = request.get('operation')
            if operation == 'ping':
                pass
            elif operation == 'configure':
                async with self.lock:
                    mode = request.get('mode')
                    if mode not in ('primary', 'virtual_only') or (self.reserved and self.layout.mode != mode):
                        raise RuntimeError('Stop all displays before changing display behavior.')
                    self.layout.mode = mode
            elif operation == 'attach':
                # Borrow only layout control; the root coordinator retains the
                # VKMS reservation and is solely responsible for its removal.
                slot=request.get('slot');name=request.get('name')
                if type(slot) is not int or slot!=1 or not isinstance(name,str) or not name.startswith('Virtual-') or not name[8:].isdigit():
                    raise ValueError('Invalid remote display identity.')
                async with self.lock:
                    if self.stopping or slot in self.reserved:
                        raise RuntimeError('Display layout is already owned or recovering.')
                    outputs=await asyncio.to_thread(self.layout.backend.outputs)
                    name=await asyncio.to_thread(borrowed_output_name,self.layout.backend,name,outputs)
                    if not any(o['name']==name and self.layout.backend.active(o) for o in outputs):
                        raise RuntimeError('Remote display is not active.')
                    await self._layout('reserve',slot,(name,),outputs)
                    await self._layout('ready',slot,name)
                    self.reserved.add(slot);self.borrowed.add(slot)
            elif operation == 'detach':
                slot=request.get('slot')
                if type(slot) is not int or slot!=1 or slot in self.holders:
                    raise ValueError('Unknown borrowed display.')
                if slot in self.borrowed:await self.release(slot)
            elif operation == 'hold':
                slot = request['slot']
                args = request['args']
                if type(slot) is not int or slot not in (1, 2) or not isinstance(args, list) or len(args) != 6:
                    raise ValueError('Invalid display holder request.')
                if not guarded_holder_supported(args[4], args[5]):
                    raise ValueError('Unsupported guarded display backend.')
                async with self.lock:
                    if self.stopping or slot in self.reserved:
                        raise RuntimeError('Display holder is already owned or recovering.')
                    await self._layout('reserve', slot)
                    self.reserved.add(slot)
                    # A small whitelist preserves per-slot VKMS routing hints.
                    environment = dict(os.environ)
                    for key, value in request.get('environment', {}).items():
                        if key in ('MONITORIZE_VKMS_CAPTURE', 'MONITORIZE_GNOME_PRIMARY_OUTPUT', 'MONITORIZE_VKMS_UNATTENDED'):
                            environment[key] = str(value)
                    process = await self.spawn_holder(args, environment)
                    self.holders[slot] = process
                hold = True
                await self.forward(slot, reader, writer, process)
                return
            elif operation in ('ready', 'stream'):
                slot = request['slot']
                async with self.lock:
                    if slot not in self.holders and slot not in self.borrowed:
                        raise RuntimeError('Unknown display holder generation.')
                    if operation == 'ready':
                        await self._layout('ready', slot, request['name'])
                    else:
                        try:
                            await self._layout('stream', slot, bool(request['active']))
                        except Exception:
                            self.recovery_pending = True
                            raise
            else:
                raise ValueError('Unknown guardian operation.')
            writer.write(_frame({'ok': True, 'borrowed': sorted(self.borrowed)}))
            await writer.drain()
        except Exception as exc:
            if slot in self.reserved and not hold:
                self.pending_release.add(slot)
            print(f'Guardian: {exc}', flush=True)
            if hold:
                holder_error = str(exc)
            else:
                try:
                    writer.write(_frame({'ok': False, 'error': str(exc)}))
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if hold:
                try:
                    await self.release(slot)
                except Exception as exc:
                    print(f'Guardian retaining display {slot}: {exc}', flush=True)
                    receipt = _frame({'ok': False, 'error': str(exc)})
                else:
                    receipt = b'MONITORIZE_EVENT ' + _frame({'type': 'guardian_result', 'cleanup_complete': True})
                    if holder_error:
                        receipt += _frame({'ok': False, 'error': holder_error})
                try:
                    writer.write(receipt)
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            writer.close()

    async def spawn_holder(self, args, environment):
        return await asyncio.create_subprocess_exec(sys.executable, '-m',
            'monitorize.streaming.headless_virtual_display', *map(str, args),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=environment, limit=MAX_FRAME)

    async def forward(self, slot, reader, writer, process):
        async def output():
            while line := await process.stdout.readline():
                writer.write(line)
                await writer.drain()
            await process.wait()
            if process.returncode:
                raise RuntimeError(f'Display holder exited with code {process.returncode}.')

        async def input_closed():
            await reader.read(1024)

        tasks = [asyncio.create_task(output()), asyncio.create_task(input_closed())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def supervise(self):
        while True:
            await asyncio.sleep(.5)
            if _identity(self.owner) == self.owner_identity and not self.stopping:
                # A relay can disappear while recovery failed. Keep retrying
                # restoration before allowing any retained holder to disappear.
                if self.recovery_pending:
                    try:
                        async with self.lock:
                            await self._layout('restore')
                        self.recovery_pending = False
                    except Exception as exc:
                        print(f'Guardian layout recovery pending: {exc}', flush=True)
                for slot in list(self.pending_release):
                    try:
                        await self.release(slot)
                    except Exception as exc:
                        print(f'Guardian release pending: {exc}', flush=True)
                continue
            self.stopping = True
            try:
                async with self.lock:
                    await self._layout('restore')
                for slot in list(self.reserved):
                    await self.release(slot)
                self.server.close()
                return
            except Exception as exc:
                print(f'Guardian recovery pending: {exc}', flush=True)
                await asyncio.sleep(2)

    async def run(self):
        await self._layout('recover')
        self.server = await asyncio.start_unix_server(self.handle, self.path, limit=MAX_FRAME)
        os.chmod(self.path, 0o600)
        await self.supervise()
        await self.server.wait_closed()


def _serve(args):
    # Session-scoped journal/lock prevents two guardians replaying each other's
    # layout. A restarted GUI waits/fails until the old owner has restored it.
    directory, suffix = recovery_paths()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(directory / f'{suffix}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with (directory / f'{suffix}.log').open('a') as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        transaction = LayoutTransaction(current_layout_backend(), directory / f'{suffix}.json', args.mode)
        guardian = Guardian(args.path, args.token, args.mode, args.owner, transaction)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, lambda: setattr(guardian, 'stopping', True))
        try:
            loop.run_until_complete(guardian.run())
        finally:
            loop.close()
            Path(args.path).unlink(missing_ok=True)
            try:
                Path(args.path).parent.rmdir()
            except OSError:
                pass
            os.close(lock)


def _hold(args):
    # This relay may die with the GUI; the guardian and real holder must not.
    with socket.socket(socket.AF_UNIX) as peer:
        peer.connect(args.path)
        peer.sendall(_frame(dict(token=args.token, operation='hold', slot=args.slot,
            args=args.holder_args, environment={key: os.environ[key] for key in
                ('MONITORIZE_VKMS_CAPTURE', 'MONITORIZE_GNOME_PRIMARY_OUTPUT', 'MONITORIZE_VKMS_UNATTENDED') if key in os.environ})))
        buffer = b''

        def output(data):
            nonlocal buffer
            buffer += data
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if len(line) > MAX_FRAME:
                    raise RuntimeError('Guardian output frame exceeds limit.')
                if line.startswith(b'{'):
                    response = json.loads(line)
                    if response.get('ok') is False:
                        raise RuntimeError(response.get('error', 'Guardian failed.'))
                sys.stdout.buffer.write(line + b'\n')
                sys.stdout.buffer.flush()
            if len(buffer) > MAX_FRAME:
                raise RuntimeError('Guardian output frame exceeds limit.')

        while True:
            readable, _, _ = select.select([peer, sys.stdin.buffer], [], [], .5)
            if sys.stdin.buffer in readable:
                # EOF and explicit quit both request ordered cleanup.
                sys.stdin.buffer.readline()
                peer.shutdown(socket.SHUT_WR)
                break
            if peer in readable:
                data = peer.recv(MAX_FRAME)
                if not data:
                    return
                output(data)
        while data := peer.recv(MAX_FRAME):
            output(data)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('serve', 'hold'):
        child = sub.add_parser(operation)
        child.add_argument('path')
        child.add_argument('token')
        if operation == 'serve':
            child.add_argument('mode', choices=('primary', 'virtual_only'))
            child.add_argument('owner', type=int)
        else:
            child.add_argument('slot', type=int)
            child.add_argument('holder_args', nargs=6)
    args = parser.parse_args()
    if args.operation == 'serve':
        _serve(args)
    else:
        _hold(args)


if __name__ == '__main__':
    main()
