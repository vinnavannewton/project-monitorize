"""Restricted client for the packaged Monitorize system-setup helper."""

from __future__ import annotations

import grp
import json
import os
import pwd
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from monitorize.platform.polkit_errors import authentication_error_message
from monitorize.platform.polkit_agent import run_authorized

HELPER = Path("/usr/libexec/monitorize/monitorize-system-setup")
VKMS_AUTHORIZATION_HELPER = Path('/usr/libexec/monitorize-vkms/monitorize-vkms-authorization')


def source_bootstrap_command(arguments):
    repository=Path(__file__).resolve().parents[3]
    script=repository/'scripts/source-maintenance.py'
    if not script.is_file():return None
    from monitorize.platform.remote_login_repair import owning_package_manager
    if owning_package_manager(HELPER):return None
    pkexec=shutil.which('pkexec')
    return [pkexec,'/usr/bin/python3','-I',str(script),'bootstrap','--',*arguments] if pkexec else None


def setup_helper_command(arguments):
    try:
        info=HELPER.stat()
        valid=info.st_uid==0 and not info.st_mode & 0o022 and not HELPER.is_symlink() and os.access(HELPER,os.X_OK) and "SYSTEM_RECOVERY_PROTOCOL = 1" in HELPER.read_text()
    except OSError:valid=False
    if valid:
        pkexec=shutil.which('pkexec')
        if pkexec:return [pkexec,str(HELPER),*arguments],None
        return None,{'success':False,'message':'Polkit (pkexec) is not installed.'}
    command=source_bootstrap_command(arguments)
    if command:return command,None
    return None,{'success':False,'code':'prerequisite_missing','message':'A trusted installed system setup helper is required. Native package files remain managed by their package manager.'}


def kms_setup_command(binary):
    """Authorize only the selected executable, bound to its current file identity."""
    try:
        helper_stat = HELPER.stat()
        if (helper_stat.st_uid != 0 or helper_stat.st_mode & 0o022
                or HELPER.is_symlink() or not os.access(HELPER, os.X_OK)
                or "KMS_SETUP_PROTOCOL = 1" not in HELPER.read_text()):
            raise ValueError("Install the updated Monitorize system-setup helper and Polkit policy for graphical KMS permission repair.")
        path = Path(binary).resolve(strict=True)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("The selected Sunshine executable is not a regular file.")
    except (OSError, ValueError) as exc:
        try:
            path=Path(binary).resolve(strict=True);info=path.stat()
            command=source_bootstrap_command(['--kms-binary',str(path),'--kms-device',str(info.st_dev),'--kms-inode',str(info.st_ino)])
            if command:return command,None
        except (OSError,ValueError):pass
        return None, {"success": False, "message": str(exc)}
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None, {"success": False, "message": "Polkit (pkexec) is not installed."}
    return [pkexec, str(HELPER), "--kms-binary", str(path),
            "--kms-device", str(info.st_dev), "--kms-inode", str(info.st_ino)], None


def vkms_authorization_command(enabled):
    if not VKMS_AUTHORIZATION_HELPER.is_file():
        return None, {'success': False, 'message': 'Install or upgrade monitorize-vkms on the host first.'}
    pkexec = shutil.which('pkexec')
    if not pkexec:
        return None, {'success': False, 'message': 'Polkit (pkexec) is not installed.'}
    return [pkexec, str(VKMS_AUTHORIZATION_HELPER), 'enable' if enabled else 'disable'], None


def _input_status() -> dict[str, object]:
    configured = False
    current_member = False
    try:
        group = grp.getgrnam('monitorize-input')
        user = pwd.getpwuid(os.getuid())
        current_member = group.gr_gid in {os.getgid(), *os.getgroups()}
        configured = current_member or getattr(user, 'pw_gid', None) == group.gr_gid or user.pw_name in group.gr_mem
    except (KeyError, OSError):
        pass
    try:
        ready = stat.S_ISCHR(os.stat('/dev/uinput').st_mode) and os.access('/dev/uinput', os.R_OK | os.W_OK)
        if ready:
            descriptor = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            os.close(descriptor)
    except OSError:
        ready = False
    logout_required = configured and not current_member and not ready
    if ready:
        message = 'Touch and input are ready in this session.'
    elif logout_required:
        message = 'Input permission is configured. Log out and back in to enable input in this session.'
    elif configured:
        message = 'This session has input permission, but /dev/uinput is unavailable. Run input setup again to check the module and device permissions.'
    else:
        message = 'Run input setup to configure access to /dev/uinput.'
    return {'input_ready': ready, 'input_configured': configured,
            'logout_required': logout_required, 'input_message': message}


def _input_ready() -> bool:
    return bool(_input_status()['input_ready'])


def get_system_setup_status() -> dict[str, object]:
    """Separate saved enrollment, current session membership and device access."""
    return {
        'available': (HELPER.is_file() and os.access(HELPER, os.X_OK)) or source_bootstrap_command([]) is not None,
        **_input_status(),
    }


def apply_system_setup(enable_input: bool, enable_firewall: bool) -> dict[str, object]:
    """Authorize and run only the selected packaged setup actions via polkit."""
    command, error = system_setup_command(enable_input, enable_firewall)
    if error:
        return error

    try:
        result = run_authorized(command, text=True, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "System setup timed out."}
    except OSError as exc:
        return {"success": False, "message": f"Could not start system setup: {exc}"}
    return parse_system_setup_result(result.returncode, result.stdout, result.stderr)


def system_setup_command(enable_input: bool, enable_firewall: bool, enable_vkms=False):
    """Build the fixed privileged command without starting it."""
    if not enable_input and not enable_firewall and not enable_vkms:
        return None, {"success": False, "message": "Select at least one system setup action."}
    options=[]
    if enable_input:options.append('--input')
    if enable_firewall:options.append('--firewall')
    if enable_vkms:options.append('--unattended-vkms')
    return setup_helper_command(options)


def parse_system_setup_result(returncode, stdout, stderr):
    """Apply the same response rules to synchronous and Qt process callers."""
    if returncode != 0:
        response = {}
        try:
            failure = json.loads(stdout)
            if isinstance(failure, dict) and failure.get('success') is False:
                response = failure
        except (TypeError, ValueError):
            pass
        if returncode == 126 and not response:
            response['cancelled'] = True
        detail = str(response.get('message') or stderr.strip() or 'Authorization was cancelled or setup failed.')
        response.update(success=False, message=authentication_error_message(detail) or detail)
        return response

    try:
        response = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "outcome_unknown": True, "message": "System setup returned an invalid response. Refresh status before retrying."}
    if not isinstance(response, dict):
        return {"success": False, "outcome_unknown": True, "message": "System setup returned an invalid response. Refresh status before retrying."}
    response["success"] = response.get("success") is True
    return response


def remote_login_setup_status():
    """Read enrollment metadata only; does not contact or activate a service."""
    from monitorize.remote_login.settings import SETTINGS,read_settings
    entry=Path('/usr/libexec/monitorize/remote-login/entry.py')
    from monitorize.remote_login.display_managers import manager_capabilities
    capability=manager_capabilities()
    from monitorize.platform.remote_login_host import host_readiness
    host=host_readiness()
    source_stage=Path(__file__).resolve().parents[3]/'scripts/stage-remote-login.py'
    source_install=source_stage.is_file()
    repair=('Use Repair remote login in Settings to install matching service files with administrator authorization.'
            if source_install else 'The remote-login service files are managed by the Monitorize native package.')
    live=host_readiness(require_live=True)
    result={'available':host['available'],'live_available':bool(live['available'] and capability['supported']),
            'live_message':live['message'],'enabled':False,'create_login_virtual_display':False,'login_display_policy_available':login_display_policy_available(),'display_idle_timeout_seconds':20,'reason':host['reason'],'protocols':host['protocols'],
            'supported':capability['supported'],'manager':capability['manager'],
            'compositor':capability['compositor'],'validated':capability['validated'],
            'message':capability['message'] if host['available'] else host['message']}
    if host['reason']=='missing_assets':
        result['message']='Monitorize remote-login system assets are missing. '+repair
    if host['available']:
        from monitorize.platform import remote_login_host
        installed_checker=Path('/usr/lib/monitorize-remote-login/monitorize/platform/remote_login_host.py')
        try:matching=installed_checker.read_bytes()==Path(remote_login_host.__file__).read_bytes()
        except OSError:matching=False
        if not matching:
            result.update(available=False,reason='service_update_required',
                          message='The app and installed remote-login setup helper have different compatibility checks. '+repair)
    if result['available'] and capability['manager'] in ('sddm.service','gdm.service'):
        installed=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/display_managers.py')
        try:current=('GDM_WAYLAND_PROTOCOL = 1' if capability['manager']=='gdm.service' else 'SDDM_WAYLAND_PROTOCOL = 1') in installed.read_text()
        except OSError:current=False
        if not current:result.update(available=False,reason='service_update_required',message='Install the '+('GDM' if capability['manager']=='gdm.service' else 'SDDM')+' Wayland remote-login service update before setup. '+repair)
    if SETTINGS.exists():
        try:
            settings=read_settings()
            if settings.uid!=os.getuid():
                result.update(available=False,message='Remote login belongs to another local account.')
            else:
                result['enabled']=settings.enabled
                result['create_login_virtual_display']=getattr(settings,'create_login_virtual_display',False)
                result['display_idle_timeout_seconds']=getattr(settings,'display_idle_timeout_seconds',20)
                if getattr(settings,'manager','plasmalogin.service')!=capability['manager']:
                    result.update(supported=False,message='Display manager changed after enrollment. Revalidate remote-login setup.')
        except ValueError:result.update(available=False,message='Remote-login configuration needs administrator repair.')
    if not result['available']:
        result.update(live_available=False,live_message=result['message'])
    from monitorize.platform.remote_login_host import _constant
    try:
        result['recovery_available']=(_constant(Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py').read_text(),'SETUP_RECOVERY_PROTOCOL')==1
            and _constant(HELPER.read_text(),'REMOTE_LOGIN_RECOVERY_PROTOCOL')==1)
    except (OSError,ValueError,SyntaxError):result['recovery_available']=False
    try:result['maintenance_pending']=Path('/var/lib/monitorize/remote-login-package.json').exists()
    except OSError:result['maintenance_pending']=False
    return result


def apply_remote_login_setup(enabled):
    status=remote_login_setup_status()
    # Compatibility is an enablement requirement. The installed root disable
    # action authenticates the enrolled owner and must remain a recovery path
    # when the host is missing, mixed or too old for new enrollment.
    if enabled and not status['available']:return {'success':False,'message':status['message']}
    if enabled and not status.get('live_available'):
        return {'success':False,'configured':bool(status.get('enabled')),'message':status.get('live_message','Install live-enrollment service updates first.')}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Install Polkit and run an authentication agent in your desktop.'}
    try:
        result=run_authorized([pkexec,str(HELPER),'--remote-login','enable-live' if enabled else 'disable'],
            text=True,capture_output=True,check=False)
    except subprocess.TimeoutExpired:return {'success':False,'outcome_unknown':True,'message':'Remote-login setup outcome is unknown. Refresh startup status before retrying.'}
    except OSError:return {'success':False,'message':'Could not start the installed administrator setup helper.'}
    response=parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    if response.get('action','enable-live' if enabled else 'disable')!=('enable-live' if enabled else 'disable'):
        return {'success':False,'outcome_unknown':True,'message':'The administrator helper reported a different startup action. Refresh startup status before retrying.'}
    return response


def apply_remote_login_recovery(disable_boot=False):
    status=remote_login_setup_status()
    if not status.get('recovery_available'):
        return {'success':False,'message':'The installed remote-login helper does not support maintenance recovery.'}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Polkit (pkexec) is not installed.'}
    action='recover-disable' if disable_boot else 'recover'
    try:
        result=run_authorized([pkexec,str(HELPER),'--remote-login',action],capture_output=True,text=True,check=False)
        response=parse_system_setup_result(result.returncode,result.stdout,result.stderr)
        if response.get('action',action)!=action:
            return {'success':False,'outcome_unknown':True,'message':'The administrator helper did not confirm the requested recovery action. Refresh startup status.'}
        return {**response,'request_completed':bool(disable_boot and response.get('success'))}
    except OSError:return {'success':False,'message':'Could not start administrator recovery.'}



def prepare_desktop_fallback():
    """Explicit administrator handoff; no persistent boot preference changes."""
    installed=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py')
    try:
        if 'DESKTOP_FALLBACK_PROTOCOL = 1' not in installed.read_text():
            return {'success':False,'message':'Install the updated remote-login system assets once to enable safe desktop fallback.'}
    except OSError:return {'success':False,'message':'Install remote-login system assets to enable safe desktop fallback.'}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Desktop fallback needs administrator authorization through Polkit.'}
    try:
        result=run_authorized([pkexec,str(HELPER),'--remote-login','fallback'],capture_output=True,text=True,timeout=90,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):return {'success':False,'message':'Desktop fallback authorization or ownership release failed; inspect the remote-login journal.'}


def login_display_policy_available():
    from monitorize.platform.remote_login_host import _constant
    try:
        enrollment=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py').read_text()
        capture=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/login_primary.py').read_text()
        return (_constant(enrollment,'LOGIN_DISPLAY_POLICY_PROTOCOL')==1
                and _constant(capture,'LOGIN_DISPLAY_POLICY_PROTOCOL')==1
                and 'login-virtual-on' in HELPER.read_text()
                and 'setup-login-virtual-on' in Path('/usr/libexec/monitorize/remote-login/entry.py').read_text())
    except (OSError,ValueError,SyntaxError):return False


def apply_login_virtual_display(enabled):
    """Save an enrolled account's boot-only display preference through Polkit."""
    if type(enabled) is not bool:return {'success':False,'message':'Invalid login-screen display preference.'}
    status=remote_login_setup_status()
    if not status.get('available') or not status.get('enabled'):
        return {'success':False,'message':'Enable Start at boot before changing login-screen capture.'}
    if not login_display_policy_available():
        return {'success':False,'message':'Install the login-screen display policy service update before changing this option.'}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Login-screen settings need administrator authorization through Polkit.'}
    try:
        result=run_authorized([pkexec,str(HELPER),'--remote-login','login-virtual-on' if enabled else 'login-virtual-off'],
            capture_output=True,text=True,timeout=120,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):
        return {'success':False,'message':'Login-screen settings authorization failed or timed out. Check the saved option before retrying.'}


def apply_login_primary_display(enabled):
    if type(enabled) is not bool:return {'success':False,'message':'Invalid login-screen display preference.'}
    return apply_login_virtual_display(not enabled)


def apply_display_idle_timeout(seconds):
    from monitorize.remote_login.settings import validate_idle_timeout
    try:validate_idle_timeout(seconds)
    except ValueError as error:return {'success':False,'message':str(error)}
    status=remote_login_setup_status()
    if not status.get('available') or not status.get('enabled'):
        return {'success':False,'message':'Enable Start at boot before changing the display timeout.'}
    installed=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py')
    try:current='DISPLAY_IDLE_TIMEOUT_PROTOCOL = 1' in installed.read_text()
    except OSError:current=False
    if not current:return {'success':False,'message':'Install the display timeout service update before changing this option.'}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Display timeout settings need administrator authorization through Polkit.'}
    try:
        result=run_authorized([pkexec,str(HELPER),'--remote-login','timeout','--display-idle-timeout',str(seconds)],
                              capture_output=True,text=True,timeout=60,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):
        return {'success':False,'message':'Display timeout authorization failed or timed out. Refresh Settings before retrying.'}


def apply_general_display_idle_timeout(seconds):
    from monitorize.remote_login.settings import validate_idle_timeout
    from monitorize.config.settings import save_display_idle_timeout
    try:
        seconds = validate_idle_timeout(seconds)
        status = remote_login_setup_status()
        if status.get('enabled') and status.get('display_idle_timeout_seconds') != seconds:
            result = apply_display_idle_timeout(seconds)
            if not result.get('success'):
                return result
        save_display_idle_timeout(seconds)
    except (ValueError, OSError):
        return {'success': False, 'message': 'Could not save the disconnect timer. Enter a value from 1 to 600 seconds and retry.'}
    return {'success': True, 'display_idle_timeout_seconds': seconds,
            'message': 'Disconnect timer saved. Applies after future disconnects; the current stream is unchanged.'}


def recover_networkmanager():
    command,error=setup_helper_command(['--recover-networkmanager'])
    if error:return error
    try:
        result=run_authorized(command,capture_output=True,text=True,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except OSError:return {'success':False,'message':'NetworkManager recovery could not start.'}


def recover_vkms_host():
    command,error=setup_helper_command(['--recover-host'])
    if error:return error
    try:
        result=run_authorized(command,capture_output=True,text=True,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except OSError:return {'success':False,'message':'Host recovery could not start.'}


def restart_vkms_host():
    try:current='VKMS_MANAGEMENT_PROTOCOL = 1' in HELPER.read_text()
    except OSError:current=False
    if not current:
        command=source_bootstrap_command(['--restart-host'])
        if not command:return {'success':False,'message':'Update Monitorize system helpers before using VKMS Restart.'}
        error=None
    else:command,error=setup_helper_command(['--restart-host'])
    if error:return error
    try:
        result=run_authorized(command,capture_output=True,text=True,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except OSError:return {'success':False,'message':'VKMS service restart could not start.'}
