"""Desktop-only layout bridge for the coordinator's borrowed VKMS output."""
import os
import tempfile
from monitorize.platform.session_layout import borrowed_output_name, current_layout_backend
from monitorize.streaming.recovery_guardian import GuardianClient


class RemoteLayout:
    def __init__(self, *, backend=None, guardian=None):
        self.desktop='hyprland' if 'hyprland' in os.environ.get('XDG_CURRENT_DESKTOP','').lower() else 'kde'
        if 'gnome' in os.environ.get('XDG_CURRENT_DESKTOP','').lower().split(':'):self.desktop='gnome'
        self.backend=backend or current_layout_backend()
        self.guardian=guardian
        self.target=None
        self.mode=None

    def restore(self):
        if self.target is not None:
            # Detach restores physical displays but never removes reserved VKMS.
            self.guardian.command('detach',slot=1)
            self.target=None

    def update(self, mode, active, target=None):
        if mode not in ('extend','primary','virtual_only'):
            raise ValueError('Unsupported remote desktop display behavior.')
        if not active or mode=='extend':
            self.restore()
            return
        if not target:raise RuntimeError('Remote desktop output identity is unavailable.')
        if self.target==target and self.mode==mode:
            self.guardian.start()
            if 1 in self.guardian.command('ping').get('borrowed',[]):return
            # A restarted guardian recovers its durable journal before listening.
            self.target=None
        self.restore()
        outputs=self.backend.outputs()
        name=borrowed_output_name(self.backend,target,outputs)
        if not any(o['name']==name and self.backend.active(o) for o in outputs):
            raise RuntimeError('Remote desktop output is not active.')
        if not any(o['name']!=name and self.backend.active(o) for o in outputs):
            # Headless: there is no physical layout to disable or restore.
            return
        if self.guardian is None:
            self.guardian=GuardianClient(mode,self.desktop,os.environ.get('XDG_RUNTIME_DIR',tempfile.gettempdir()))
        self.guardian.start()
        self.guardian.command('configure',mode=mode)
        self.guardian.command('attach',slot=1,name=target)
        self.target=target;self.mode=mode
        try:self.guardian.command('stream',slot=1,active=True)
        except Exception:
            self.restore()
            raise
