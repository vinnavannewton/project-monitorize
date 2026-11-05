"""Transactional KDE layout policy, used only by the independent guardian."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import hashlib

MODES = ('extend', 'primary', 'virtual_only')


def output_identity(output):
    """Prefer physical EDID identity; avoid replaying geometry onto replacements."""
    for connector in sorted(Path('/sys/class/drm').glob('card*-' + output['name'])):
        try:
            data = (connector / 'edid').read_bytes()
            if data:
                return hashlib.sha256(data).hexdigest()
        except OSError:
            pass
    return json.dumps([output['name'], output.get('type'), output.get('sizeMM')], sort_keys=True)


def layout_modes(desktop):
    if os.path.isfile('/.flatpak-info'):
        return ('extend',)
    session = os.environ.get('XDG_SESSION_TYPE', '').lower()
    if session == 'x11' and os.environ.get('DISPLAY') and shutil.which('xrandr'):
        return ('extend', 'virtual_only')
    if desktop == 'niri' and session == 'wayland' and os.environ.get('NIRI_SOCKET') and shutil.which('niri'):
        return ('extend', 'virtual_only')
    supported = (desktop in ('kde','hyprland','gnome')
            and os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
            and (desktop=='gnome' or bool(shutil.which('hyprctl' if desktop=='hyprland' else 'kscreen-doctor'))))
    return MODES if supported else ('extend',)


def layout_supported(desktop):
    return len(layout_modes(desktop)) > 1


def guarded_holder_supported(desktop, creator):
    if os.environ.get('XDG_SESSION_TYPE', '').lower() == 'x11':
        return creator == 'vkms'
    return (desktop in ('kde', 'hyprland') and creator in ('native', 'vkms')
            or desktop in ('gnome', 'niri') and creator == 'vkms')


class KdeLayout:
    def outputs(self):
        result = subprocess.run(['kscreen-doctor', '-j'], capture_output=True, text=True, timeout=4, check=True)
        outputs = json.loads(result.stdout)['outputs']
        if not outputs:
            raise RuntimeError('KDE returned no displays.')
        return outputs

    def apply(self, settings):
        if settings:
            subprocess.run(['kscreen-doctor', *settings], capture_output=True, text=True, timeout=4, check=True)

    @staticmethod
    def active(output):
        if output.get('connected') is not True:
            return False
        if 'enabled' in output:
            return output['enabled'] is True
        # KScreen omits enabled when the output does not advertise Disable.
        priority = output.get('priority')
        return type(priority) is int and priority > 0

    def restore(self, snapshot, owned):
        current = {o['name']: o for o in self.outputs()}
        commands, visible = [], []
        for saved in snapshot:
            output = current.get(saved['name'])
            if not output or not output.get('connected') or not saved['enabled']:
                continue
            name = output['name']
            commands.append(f'output.{name}.enable')
            if (not output.get('enabled') and saved.get('identity') == output_identity(output)
                    and saved.get('pos') is not None):
                position = saved['pos']
                commands.append(f'output.{name}.position.{int(position["x"])},{int(position["y"])}')
            visible.append(name)
        if not visible:
            # A saved monitor may have been unplugged. Recover visibility on a
            # newly connected non-owned output, without replaying old geometry.
            fallback = next((o for o in current.values() if o.get('connected') and o['name'] not in owned), None)
            if not fallback:
                raise RuntimeError('No physical display can be restored; keeping virtual displays alive.')
            visible = [fallback['name']]
            commands.append(f'output.{visible[0]}.enable')
        priorities = {o['name']: o['priority'] for o in snapshot}
        visible.sort(key=lambda name: priorities.get(name, 100))
        commands += [f'output.{name}.priority.{index + 1}' for index, name in enumerate(visible)]
        for index, name in enumerate(owned):
            if name in current and self.active(current[name]):
                commands.append(f'output.{name}.priority.{len(visible) + index + 1}')
        self.apply(commands)
        observed = self.outputs()
        confirmed = {o['name'] for o in observed if self.active(o)}
        if not set(visible).issubset(confirmed):
            raise RuntimeError('Physical display restoration could not be verified; virtual displays retained.')
        expected_primary = visible[0]
        if not any(o['name'] == expected_primary and int(o.get('priority') or 0) == 1 for o in observed):
            raise RuntimeError('Physical primary restoration could not be verified.')

    def policy(self, mode, snapshot, targets):
        outputs = {o['name']: o for o in self.outputs()}
        names = list(targets.values())
        if not names or any(name not in outputs or not self.active(outputs[name]) for name in names):
            states = {
                name: {key: output.get(key) for key in ('connected', 'enabled', 'priority')}
                for name, output in outputs.items()
            }
            raise RuntimeError(
                f'A streaming virtual display is unavailable. '
                f'Expected KScreen outputs: {names!r}; observed: {states!r}.'
            )
        commands = [f'output.{name}.priority.{index + 1}' for index, name in enumerate(names)]
        for index, saved in enumerate(snapshot):
            name = saved['name']
            if name not in outputs or not outputs[name].get('connected') or not saved['enabled']:
                continue
            commands.append(f'output.{name}.disable' if mode == 'virtual_only' else f'output.{name}.priority.{len(names) + index + 1}')
        self.apply(commands)
        observed = {o['name']: o for o in self.outputs()}
        if any(name not in observed or not self.active(observed[name]) for name in names):
            raise RuntimeError('Virtual display was lost during layout change.')
        if int(observed[names[0]].get('priority') or 0) != 1:
            raise RuntimeError('Virtual primary selection was not applied.')
        if mode == 'virtual_only' and any(self.active(observed[s['name']]) for s in snapshot if s['name'] in observed and s['enabled']):
            raise RuntimeError('Physical outputs were not disabled.')


class LayoutTransaction:
    """One baseline per session, durable before the first layout mutation."""
    def __init__(self, backend, journal, mode):
        if mode not in MODES:
            raise ValueError('Unsupported display behavior.')
        self.backend, self.journal, self.mode = backend, Path(journal), mode
        self.snapshot = []
        self.targets = {}
        self.streaming = set()
        self.reservations = set()

    def _write(self):
        data = {'version': 1, 'snapshot': self.snapshot, 'owned': list(self.targets.values())}
        temporary = self.journal.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.journal)
        directory = os.open(self.journal.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def recover(self):
        if self.journal.exists():
            with self.journal.open() as source:
                data = json.load(source)
            if data.get('version') != 1:
                raise RuntimeError('Unsupported display recovery journal.')
            self.backend.restore(data['snapshot'], data['owned'])
            self.journal.unlink()

    def reserve(self, slot, exclude=()):
        if not self.snapshot:
            outputs = self.backend.outputs()
            if any('priority' not in o for o in outputs if o.get('connected')):
                raise RuntimeError('This KDE version does not report display priorities for safe restoration.')
            self.snapshot = [dict(name=o['name'], enabled=bool(o.get('enabled')),
                                  priority=int(o.get('priority') or 1), pos=o.get('pos'),
                                  mode=o.get('mode'),scale=o.get('scale'),
                                  identity=output_identity(o)) for o in outputs if o.get('connected') and o['name'] not in exclude]
            if hasattr(self.backend,'snapshot'):
                self.snapshot=self.backend.snapshot(outputs,exclude)
            if not any(o['enabled'] for o in self.snapshot):
                self.snapshot = []
                raise RuntimeError('A physical display baseline is required before using this mode.')
            self._write()
        self.reservations.add(slot)

    def ready(self, slot, name):
        if not name or any(o['name'] == name for o in self.snapshot):
            raise RuntimeError('Virtual output must be separate from the saved physical displays.')
        self.targets[slot] = name
        self._write()

    def stream(self, slot, active):
        if slot not in self.targets:
            raise RuntimeError('Virtual display is not ready for layout changes.')
        if active:
            self.streaming.add(slot)
        else:
            self.streaming.discard(slot)
        self.reconcile()

    def reconcile(self, excluding=()):
        if not self.snapshot:
            return
        if not self.streaming:
            self.backend.restore(self.snapshot, self.targets.values())
            return
        try:
            # Stable slot 1 is primary whenever it is streaming, otherwise slot 2.
            active = {key: self.targets[key] for key in sorted(self.streaming)}
            active.update({key: value for key, value in self.targets.items() if key not in active and key not in excluding})
            self.backend.policy(self.mode, self.snapshot, active)
        except Exception:
            self.backend.restore(self.snapshot, self.targets.values())
            raise

    def prepare_release(self, slot):
        # Reconcile before the holder is allowed to destroy its output.
        old_streaming = set(self.streaming)
        self.streaming.discard(slot)
        try:
            self.reconcile(excluding=(slot,))
        except Exception:
            self.streaming = old_streaming
            raise

    def release(self, slot):
        self.prepare_release(slot)
        self.targets.pop(slot, None)
        self.reservations.discard(slot)
        if self.targets or self.reservations:
            self._write()
        elif self.snapshot:
            self.journal.unlink(missing_ok=True)
            self.snapshot = []

    def restore(self):
        if self.snapshot:
            self.backend.restore(self.snapshot, self.targets.values())


def current_layout_backend():
    if os.environ.get('XDG_SESSION_TYPE', '').lower() == 'x11':
        from .xrandr_layout import XrandrLayout
        return XrandrLayout()
    if 'niri' in os.environ.get('XDG_CURRENT_DESKTOP', '').lower() or os.environ.get('NIRI_SOCKET'):
        from .niri_layout import NiriLayout
        return NiriLayout()
    if 'gnome' in os.environ.get('XDG_CURRENT_DESKTOP','').lower().split(':'):
        from .gnome_layout import GnomeLayout
        return GnomeLayout()
    if ('hyprland' in os.environ.get('XDG_CURRENT_DESKTOP','').lower()
            or os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')):
        from .hyprland_layout import HyprlandLayout
        return HyprlandLayout()
    return KdeLayout()
