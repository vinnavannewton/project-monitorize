"""Draft/applied streaming configuration, independent of live process state."""
import copy
import re
from monitorize.config.settings import CAPTURE_MODES, _normalize_display_settings


def capture_compatibility_error(desktop, display_type, creator, capture):
    if desktop in ('kde', 'gnome', 'hyprland', 'sway') and display_type == 'Extend' and creator == 'native' and capture == 'kms':
        return 'KMS capture requires a VKMS display. Select Auto capture for a compositor-created virtual display.'
    return ''


class ConfigurationState:
    def __init__(self, primary, second, general):
        self.applied = copy.deepcopy(dict(primary=primary, second=second,
            display_behavior=general.get('display_behavior', 'extend'),
            streaming_backend=general.get('streaming_backend', 'sunshine')))
        self.saved = copy.deepcopy(self.applied)
        self.draft = copy.deepcopy(self.applied)
        self.revision = 0

    @property
    def dirty(self):
        return self.draft != self.saved

    def update(self, group, values):
        self.draft[group].update(copy.deepcopy(values))

    def reconcile_desktop_capture(self, desktop):
        """Release reserved-output settings without discarding unrelated edits."""
        if desktop != 'gnome':return
        for bundle in (self.applied, self.saved, self.draft):
            primary=bundle['primary']
            if primary.get('display_type') != 'Extend' or primary.get('virtual_display_creator','native') != 'native':
                continue
            for raw in (primary, bundle['second']):
                if raw.get('sunshine_capture') == 'kms':raw['sunshine_capture']='auto'

    def candidate(self, *, desktop='', remote_login=False):
        bundle = copy.deepcopy(self.draft)
        if bundle['streaming_backend'] not in ('none', 'sunshine'):
            raise ValueError('Select a supported streaming backend.')
        primary = bundle['primary']
        if primary.get('display_type') not in ('Extend', 'Mirror'):
            raise ValueError('Select Extend or Mirror.')
        if primary.get('display_type') == 'Mirror':
            if bundle['streaming_backend'] == 'none':
                raise ValueError('Mirror mode requires Sunshine.')
            if not primary.get('mirror_output'):
                raise ValueError('Select an existing screen for Mirror.')
        for label, raw in [('Display 1', primary), ('Display 2', bundle['second'])]:
            if label == 'Display 2' and not raw.get('enabled'):
                continue
            if raw.get('sunshine_encoder', 'Auto') not in ('Auto','NVIDIA','VA-API','Vulkan','Software'):
                raise ValueError(label + ': unsupported encoder.')
            if raw.get('sunshine_codec', 'Auto') not in ('Auto','H.264','HEVC','AV1'):
                raise ValueError(label + ': unsupported codec.')
            if raw.get('sunshine_capture', 'auto') not in CAPTURE_MODES:
                raise ValueError(label + ': unsupported capture method.')
            if not remote_login and bundle['streaming_backend'] == 'sunshine':
                error=capture_compatibility_error(desktop, primary.get('display_type'),
                    primary.get('virtual_display_creator','native'),raw.get('sunshine_capture'))
                if error:raise ValueError(label + ': ' + error)
            if primary.get('display_type') == 'Mirror' or (raw.get('use_client_display_settings') and bundle['streaming_backend'] == 'sunshine'):
                continue
            resolution = str(raw.get('resolution','')).split(' ')[0]
            if resolution == 'Custom...':
                resolution = str(raw.get('custom_w',''))+'x'+str(raw.get('custom_h',''))
            match = re.fullmatch(r'(\d+)x(\d+)',resolution)
            if not match or not (320 <= int(match[1]) <= 7680 and 240 <= int(match[2]) <= 4320):
                raise ValueError(label + ': enter a valid resolution.')
            rate = raw.get('custom_fps') if raw.get('fps') == 'Custom...' else raw.get('fps')
            try:
                if not str(rate).isdigit() or not 24 <= int(rate) <= 240:
                    raise ValueError()
            except (TypeError,ValueError):
                raise ValueError(label + ': enter a valid refresh rate.') from None
        if bundle['display_behavior'] not in ('extend','primary','virtual_only'):
            raise ValueError('Unsupported display behavior.')
        return bundle

    @property
    def restart_required(self):
        return self.saved != self.applied

    def save_later(self, bundle):
        self.saved = copy.deepcopy(bundle)
        self.draft = copy.deepcopy(bundle)
        self.revision += 1

    def discard(self):
        self.draft = copy.deepcopy(self.saved)
        self.revision += 1

    def accept(self, bundle):
        self.applied = copy.deepcopy(bundle)
        self.saved = copy.deepcopy(bundle)
        self.draft = copy.deepcopy(bundle)
        self.revision += 1


def save_bundle(bundle):
    """Publish primary, secondary and general streaming keys with one atomic sync."""
    from PyQt6.QtCore import QSettings
    from monitorize.config.settings import _get_settings
    settings = _get_settings()
    settings.setAtomicSyncRequired(True)
    previous = {key: settings.value(key) for key in settings.allKeys()}
    for group, values in [('display', bundle['primary']), ('second_display', bundle['second'])]:
        values = _normalize_display_settings(values)
        for key,value in values.items():
            settings.setValue(group+'/'+key,value)
    for key in ('display_behavior','streaming_backend'):
        settings.setValue('general/'+key,bundle[key])
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        # Keep the QSettings cache consistent with the previous accepted revision.
        for key in settings.allKeys():
            if key not in previous:
                settings.remove(key)
        for key,value in previous.items():
            settings.setValue(key,value)
        raise OSError('Could not save the streaming configuration.')
