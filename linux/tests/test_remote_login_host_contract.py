"""Optional cross-repository execution; set MONITORIZE_VKMS_SOURCE to a host checkout."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
HOST=Path(os.environ.get('MONITORIZE_VKMS_SOURCE',ROOT.parent/'remote-login-host'))
PROGRAM=r'''
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from monitorize_vkms import boot_runtime,lease
from monitorize.remote_login.wayland_greeter import activate_greeter
with TemporaryDirectory() as directory,patch.object(lease,'OWNER_UID',os.getuid()):
    root=Path(directory);connector=root/'connector0';connector.mkdir()
    for name,value in dict(status='2',enabled='1',dynamic='1',edid='',edid_enabled='1').items():
        (connector/name).write_text(value)
    state=root/'state'
    reserved=boot_runtime.reserve_boot_output(state=state,connector=connector,uid=1000,boot_id='boot-a',sessions=[])
    drm=root/'card2-Virtual-2';drm.mkdir();(drm/'enabled').write_text('enabled')
    output=dict(name='Virtual-2',card='card2',connector_id=41,display='mon1',status='connected',
                edid_sha256=reserved['edid_sha256'],path=str(drm))
    def worker(uid,environment,payload,timeout):
        assert uid==42
        assert payload['name']=='Virtual-2' and payload['connector_id']==41
        assert payload['mode']==[1920,1080,60] and timeout<=8
        return dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))
    def activate(output,remaining):
        return activate_greeter(SimpleNamespace(manager='sddm.service'),dict(greeter_uid=42,session='g1'),
            'g1',dict(XDG_SESSION_TYPE='wayland'),output,remaining,
            SimpleNamespace(_run_worker=worker),lambda:None)
    with patch.object(boot_runtime,'discover',return_value={'Virtual-2':output}), \
         patch('monitorize.remote_login.wayland_greeter.greeter_compositor',return_value='kde'):
        active=boot_runtime.prepare_reserved(state=state,connector=connector,lease=reserved,activate=activate,context_check=lambda:None)
        assert active['state']=='active' and active['connector_id']==41
        idle=boot_runtime.idle_reserved(state=state,connector=connector,lease=active)
        assert idle['state']=='reserved' and (connector/'status').read_text()=='2'
    print('Cross-repository greeter activation and idle contract passed')
'''


@unittest.skipUnless((HOST/'monitorize_vkms/boot_display.py').is_file(),'Compatible host source checkout not supplied')
class HostContractTests(unittest.TestCase):
    def test_app_activation_evidence_satisfies_host_lifecycle(self):
        env=dict(os.environ,PYTHONPATH=str(ROOT/'linux')+os.pathsep+str(HOST))
        result=subprocess.run([sys.executable,'-c',PROGRAM],cwd=ROOT,env=env,
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('contract passed',result.stdout)
