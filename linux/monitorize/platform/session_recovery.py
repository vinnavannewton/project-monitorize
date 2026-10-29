"""Explicit, confirmed session-ending actions through login1 authorization."""
import os
import re


def end_session(operation):
    if operation not in ('reboot','logout'):raise ValueError('Unsupported session action.')
    try:
        import dbus
        manager=dbus.Interface(dbus.SystemBus().get_object('org.freedesktop.login1','/org/freedesktop/login1'),
                               'org.freedesktop.login1.Manager')
        if operation=='reboot':manager.Reboot(True)
        else:
            session=os.environ.get('XDG_SESSION_ID','')
            if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',session):raise ValueError('This desktop session could not be identified.')
            manager.TerminateSession(session)
        return dict(success=True,message='Restart requested.' if operation=='reboot' else 'Logout requested.')
    except Exception as error:return dict(success=False,message='Session action was not completed: '+str(error))
