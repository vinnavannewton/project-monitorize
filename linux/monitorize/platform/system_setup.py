"""Restricted client for the packaged Monitorize system-setup helper."""

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

from monitorize.platform.polkit_errors import authentication_error_message

HELPER = Path("/usr/libexec/monitorize/monitorize-system-setup")
VKMS_AUTHORIZATION_HELPER = Path('/usr/libexec/monitorize-vkms/monitorize-vkms-authorization')


def vkms_authorization_command(enabled):
    if not VKMS_AUTHORIZATION_HELPER.is_file():
        return None, {'success': False, 'message': 'Install or upgrade monitorize-vkms on the host first.'}
    pkexec = shutil.which('pkexec')
    if not pkexec:
        return None, {'success': False, 'message': 'Polkit (pkexec) is not installed.'}
    return [pkexec, str(VKMS_AUTHORIZATION_HELPER), 'enable' if enabled else 'disable'], None


def _input_ready() -> bool:
    try:
        group = grp.getgrnam("monitorize-input")
        username = pwd.getpwuid(os.getuid()).pw_name
        return os.getgid() == group.gr_gid or username in group.gr_mem
    except (KeyError, OSError):
        return False


def get_system_setup_status() -> dict[str, object]:
    """Return availability and current input permission without escalating."""
    return {
        "available": HELPER.is_file() and os.access(HELPER, os.X_OK),
        "input_ready": _input_ready(),
    }


def apply_system_setup(enable_input: bool, enable_firewall: bool) -> dict[str, object]:
    """Authorize and run only the selected packaged setup actions via polkit."""
    command, error = system_setup_command(enable_input, enable_firewall)
    if error:
        return error

    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "System setup timed out."}
    except OSError as exc:
        return {"success": False, "message": f"Could not start system setup: {exc}"}
    return parse_system_setup_result(result.returncode, result.stdout, result.stderr)


def system_setup_command(enable_input: bool, enable_firewall: bool, enable_vkms=False):
    """Build the fixed privileged command without starting it."""
    status = get_system_setup_status()
    if not status["available"]:
        return None, {"success": False, "message": "System setup is available only from the RPM or DEB package."}
    if not enable_input and not enable_firewall and not enable_vkms:
        return None, {"success": False, "message": "Select at least one system setup action."}

    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None, {"success": False, "message": "Polkit (pkexec) is not installed."}

    command = [pkexec, str(HELPER)]
    if enable_input:
        command.append("--input")
    if enable_firewall:
        command.append("--firewall")
    if enable_vkms:
        command.append('--unattended-vkms')

    return command, None


def parse_system_setup_result(returncode, stdout, stderr):
    """Apply the same response rules to synchronous and Qt process callers."""
    if returncode != 0:
        detail = stderr.strip()
        if not detail:
            try:
                failure = json.loads(stdout)
                if isinstance(failure, dict) and failure.get('success') is False:
                    detail = str(failure.get('message') or '')
            except (TypeError, ValueError):
                pass
        detail = detail or "Authorization was cancelled or setup failed."
        detail = authentication_error_message(detail) or detail
        return {"success": False, "message": detail}

    try:
        response = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "message": "System setup returned an invalid response."}
    if not isinstance(response, dict):
        return {"success": False, "message": "System setup returned an invalid response."}
    response["success"] = bool(response.get("success", False))
    return response


def remote_login_setup_status():
    """Read enrollment metadata only; does not contact or activate a service."""
    from monitorize.remote_login.settings import SETTINGS,read_settings
    entry=Path('/usr/libexec/monitorize/remote-login/entry.py')
    from monitorize.remote_login.display_managers import manager_capabilities
    capability=manager_capabilities()
    from monitorize.platform.remote_login_host import host_readiness
    host=host_readiness()
    result={'available':host['available'],'enabled':False,'login_primary_display':False,'display_idle_timeout_seconds':20,'reason':host['reason'],'protocols':host['protocols'],
            'supported':capability['supported'],'manager':capability['manager'],
            'compositor':capability['compositor'],'validated':capability['validated'],
            'message':capability['message'] if host['available'] else host['message']}
    if capability['manager'] in ('sddm.service','gdm.service'):
        installed=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/display_managers.py')
        try:current=('GDM_WAYLAND_PROTOCOL = 1' if capability['manager']=='gdm.service' else 'SDDM_WAYLAND_PROTOCOL = 1') in installed.read_text()
        except OSError:current=False
        if not current:result.update(available=False,message='Install the '+('GDM' if capability['manager']=='gdm.service' else 'SDDM')+' Wayland remote-login service update before setup.')
    if SETTINGS.exists():
        try:
            settings=read_settings()
            if settings.uid!=os.getuid():
                result.update(available=False,message='Remote login belongs to another local account.')
            else:
                result['enabled']=settings.enabled
                result['login_primary_display']=getattr(settings,'login_primary_display',False)
                result['display_idle_timeout_seconds']=getattr(settings,'display_idle_timeout_seconds',20)
                if getattr(settings,'manager','plasmalogin.service')!=capability['manager']:
                    result.update(supported=False,message='Display manager changed after enrollment. Revalidate remote-login setup.')
        except ValueError:result.update(available=False,message='Remote-login configuration needs administrator repair.')
    return result


def apply_remote_login_setup(enabled):
    status=remote_login_setup_status()
    if not status['available']:return {'success':False,'message':status['message']}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Install Polkit and run an authentication agent in your desktop.'}
    try:
        result=subprocess.run([pkexec,str(HELPER),'--remote-login','enable' if enabled else 'disable'],
            text=True,capture_output=True,timeout=120,check=False)
    except subprocess.TimeoutExpired:return {'success':False,'message':'Remote-login setup timed out. Inspect its enrollment status before retrying.'}
    except OSError:return {'success':False,'message':'Could not start the installed administrator setup helper.'}
    return parse_system_setup_result(result.returncode,result.stdout,result.stderr)


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
        result=subprocess.run([pkexec,str(HELPER),'--remote-login','fallback'],capture_output=True,text=True,timeout=90,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):return {'success':False,'message':'Desktop fallback authorization or ownership release failed; inspect the remote-login journal.'}


def apply_login_primary_display(enabled):
    """Save an enrolled account's boot-only display preference through Polkit."""
    if type(enabled) is not bool:return {'success':False,'message':'Invalid login-screen display preference.'}
    status=remote_login_setup_status()
    if not status.get('available') or not status.get('enabled'):
        return {'success':False,'message':'Enable Start at boot before changing login-screen capture.'}
    if enabled and status.get('manager')!='gdm.service':
        return {'success':False,'message':'Primary login-screen display capture currently requires GNOME with GDM.'}
    installed=Path('/usr/lib/monitorize-remote-login/monitorize/remote_login/enrollment.py')
    try:current='LOGIN_PRIMARY_DISPLAY_PROTOCOL = 1' in installed.read_text()
    except OSError:current=False
    if not current:return {'success':False,'message':'Install the login-screen primary display service update before changing this option.'}
    pkexec=shutil.which('pkexec')
    if not pkexec:return {'success':False,'message':'Login-screen settings need administrator authorization through Polkit.'}
    try:
        result=subprocess.run([pkexec,str(HELPER),'--remote-login','primary-on' if enabled else 'primary-off'],
            capture_output=True,text=True,timeout=120,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):
        return {'success':False,'message':'Login-screen settings authorization failed or timed out. Check the saved option before retrying.'}


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
        result=subprocess.run([pkexec,str(HELPER),'--remote-login','timeout','--display-idle-timeout',str(seconds)],
                              capture_output=True,text=True,timeout=60,check=False)
        return parse_system_setup_result(result.returncode,result.stdout,result.stderr)
    except (OSError,subprocess.TimeoutExpired):
        return {'success':False,'message':'Display timeout authorization failed or timed out. Refresh Settings before retrying.'}
