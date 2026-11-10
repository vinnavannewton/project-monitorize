"""Temporary Xorg layout control, shared by all native X11 desktops."""
import copy
import hashlib
import math
from pathlib import Path
import re
import subprocess
import time

IDENTITY_TRANSFORM = [1., 0., 0., 0., 1., 0., 0., 0., 1.]


def parse_outputs(text):
    outputs, current, edid = [], None, False
    for line in text.splitlines():
        match = re.match(r'^(\S+)\s+(connected|disconnected)\b(.*)', line)
        if match:
            name, connection, tail = match.groups()
            if name.startswith('-') or len(name) > 128:
                raise ValueError('Invalid XRandR output name.')
            geometry = re.search(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', tail)
            orientation = tail.split('(', 1)[0]
            current = dict(name=name, connected=connection == 'connected', enabled=bool(geometry),
                           priority=1 if re.search(r'\bprimary\b', tail) else 2,
                           primary=bool(re.search(r'\bprimary\b', tail)), connector_id=None,
                           pos={'x': int(geometry[3]), 'y': int(geometry[4])} if geometry else None,
                           mode=None, rate=None, rotation=next((r for r in ('left', 'right', 'inverted')
                               if re.search(r'\b'+r+r'\b', orientation)), 'normal'),
                           reflect='xy' if 'x axis' in orientation and 'y axis' in orientation else
                                   'x' if 'x axis' in orientation else 'y' if 'y axis' in orientation else 'normal',
                           edid='', transform=list(IDENTITY_TRANSFORM))
            outputs.append(current); edid = False
            continue
        if current is None:
            continue
        if line.strip() == 'EDID:':
            edid = True; continue
        if edid:
            if re.fullmatch(r'[0-9a-fA-F]{32}', line.strip()):
                current['edid'] += line.strip().lower(); continue
            edid = False
        match = re.match(r'\s*CONNECTOR_ID:\s*(\d+)\s*$', line)
        if match:
            current['connector_id'] = int(match[1]); continue
        fields = line.split()
        if len(fields) > 1 and not fields[0].startswith('-'):
            rates = [f for f in fields[1:] if '*' in f]
            if rates:
                try:
                    rate = float(rates[0].rstrip('*+'))
                    if not math.isfinite(rate) or rate <= 0:
                        raise ValueError()
                    current.update(mode=fields[0], rate=rate)
                except ValueError:
                    raise ValueError('Invalid XRandR refresh rate.') from None
    names = [o['name'] for o in outputs]
    if not outputs or len(set(names)) != len(names):
        raise ValueError('XRandR returned ambiguous or empty output state.')
    for output in outputs:
        output['identity'] = hashlib.sha256(output['edid'].encode()).hexdigest() if output['edid'] else str(
            (output['name'], output['connector_id']))
    return outputs


def resolve_output(requested, outputs, drm_root=Path('/sys/class/drm'), *, require_connector_id=False):
    """DRM connector IDs take precedence over names recycled by Xorg providers."""
    paths = list(drm_root.glob('card*-'+requested))
    ids = set()
    for path in paths:
        try:
            ids.add(int((path/'connector_id').read_text()))
        except (OSError, ValueError):
            continue
    if len(paths) > 1 or len(ids) > 1:
        raise ValueError('DRM output identity is ambiguous.')
    if require_connector_id and not ids:
        raise ValueError('The reserved DRM connector ID is unavailable.')
    active = [o for o in outputs if o['connected'] and o['enabled']]
    if ids:
        matches = [o for o in active if o['connector_id'] in ids]
        if not matches and not require_connector_id and not requested.startswith('Virtual-'):
            # Explicit physical Mirror selections remain usable on Xorg drivers
            # without this property. Never guess a VKMS name or ignore a conflict.
            matches = [o for o in active if o['name'] == requested and o['connector_id'] is None]
        if len(matches) != 1:
            raise ValueError('The DRM connector does not resolve to one active XRandR output.')
        return matches[0]['name']
    matches = [o for o in active if o['name'] == requested]
    if not matches:
        matches = [o for o in active if re.fullmatch(re.escape(requested)+r'-\d+', o['name'])]
    if len(matches) != 1:
        raise ValueError('X11 display is missing or ambiguous; refusing another capture target.')
    return matches[0]['name']


class XrandrLayout:
    @staticmethod
    def active(output):
        return bool(output.get('connected') and output.get('enabled'))

    def run(self, *args):
        result = subprocess.run(['xrandr', *args], capture_output=True, text=True, timeout=5, check=True)
        if len(result.stdout) > 1048576:
            raise ValueError('XRandR response exceeds its bound.')
        return result.stdout

    def outputs(self):
        outputs = parse_outputs(self.run('--query', '--prop'))
        # Verbose-only transforms must be recovered too. Panning is deliberately
        # refused until an exact recovery representation is implemented.
        verbose = self.run('--verbose'); current = None
        lines = verbose.splitlines()
        for index, line in enumerate(lines):
            header = re.match(r'^(\S+)\s+(?:connected|disconnected)\b', line)
            if header:
                current = next((o for o in outputs if o['name'] == header[1]), None)
            if current is None:
                continue
            if line.strip().startswith('Transform:'):
                try:
                    values = [float(v) for v in (line.split(':', 1)[1]+' '+lines[index+1]+' '+lines[index+2]).split()]
                    if len(values) != 9 or not all(math.isfinite(v) for v in values):
                        raise ValueError()
                except (IndexError, ValueError):
                    raise ValueError('Cannot preserve the XRandR transform.') from None
                current['transform'] = values
            if re.match(r'\s*Panning\b', line, re.I) and not re.match(r'\s*Panning:?\s*0x0(?:\s|\+|$)', line, re.I):
                raise ValueError('Disable XRandR panning before using virtual-only mode.')
        return outputs

    def snapshot(self, outputs, exclude):
        saved = [copy.deepcopy(o) for o in outputs if o['connected'] and o['name'] not in exclude]
        if any(re.fullmatch(r'XWAYLAND\d+', o['name'], re.I) for o in saved):
            raise ValueError('Physical output control requires Xorg, not an Xwayland display.')
        if any(o['enabled'] and (not o['mode'] or not o['rate']) for o in saved):
            raise ValueError('XRandR did not report the active mode needed for recovery.')
        return saved

    def _observe(self, predicate):
        for _ in range(20):
            observed = {o['name']: o for o in self.outputs()}
            if predicate(observed):
                return observed
            time.sleep(.05)
        raise RuntimeError('X11 layout change was not confirmed; retaining virtual output.')

    def policy(self, mode, snapshot, targets):
        if mode != 'virtual_only':
            raise ValueError('This X11 backend supports Extend and Virtual displays only.')
        outputs = {o['name']: o for o in self.outputs()}
        names = list(targets.values())
        if not names or any(not self.active(outputs.get(n, {})) for n in names):
            raise RuntimeError('A virtual X11 output is unavailable.')
        physical = [s['name'] for s in snapshot if s['enabled'] and s['name'] in outputs and outputs[s['name']]['connected']]
        if any(outputs[s['name']]['identity'] != s['identity'] for s in snapshot if s['name'] in physical):
            raise RuntimeError('A physical X11 monitor changed before layout application.')
        args = ['--output', names[0], '--primary']
        for name in physical:
            args += ['--output', name, '--off']
        self.run(*args)
        self._observe(lambda state: all(self.active(state.get(n, {})) for n in names)
                      and state[names[0]]['primary'] and all(not self.active(state.get(n, {})) for n in physical))

    def restore(self, snapshot, owned):
        current = {o['name']: o for o in self.outputs()}
        saved = [s for s in snapshot if s['enabled'] and current.get(s['name'], {}).get('connected')
                 and current[s['name']]['identity'] == s['identity']]
        args = []; fallback = None
        for output in saved:
            args += ['--output', output['name'], '--mode', output['mode'], '--rate', str(output['rate']),
                     '--pos', f"{output['pos']['x']}x{output['pos']['y']}", '--rotate', output['rotation'],
                     '--reflect', output['reflect'], '--transform', ','.join(str(v) for v in output['transform'])]
        if not saved:
            fallback = next((o['name'] for o in current.values() if o['connected'] and o['name'] not in owned), None)
            if fallback is None:
                raise RuntimeError('No physical X11 output can be restored; retaining virtual output.')
            args += ['--output', fallback, '--auto']
        primary = next((s['name'] for s in saved if s['primary']), fallback)
        args += ['--output', primary, '--primary'] if primary else ['--noprimary']
        self.run(*args)
        def confirmed(state):
            if fallback:
                return self.active(state.get(fallback, {}))
            for original in saved:
                output = state.get(original['name'], {})
                if (not self.active(output) or output['identity'] != original['identity']
                        or any(output[key] != original[key] for key in ('mode', 'pos', 'rotation', 'reflect', 'primary'))
                        or not math.isclose(output['rate'], original['rate'], abs_tol=.1)
                        or any(not math.isclose(a, b, abs_tol=.0001) for a, b in zip(output['transform'], original['transform']))):
                    return False
            return True
        self._observe(confirmed)
