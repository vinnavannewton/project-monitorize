#!/usr/bin/python3 -I
"""Fixed installed entry point. Never execute a user-writable checkout as root."""
import sys
sys.path.insert(0,'/usr/lib/monitorize-remote-login')
if sys.argv[1:]==['service']:
    from monitorize.remote_login.daemon import main
elif sys.argv[1:]==['boot']:
    from monitorize.remote_login.runner import boot_main as main
elif sys.argv[1:]==['desktop']:
    from monitorize.remote_login.runner import desktop_main as main
elif sys.argv[1:] in (['setup-enable'],['setup-enable-live'],['setup-disable'],['setup-recover'],['setup-recover-disable'],['setup-recover-host'],['setup-remove'],['setup-fallback'],['setup-primary-on'],['setup-primary-off'],['setup-login-virtual-on'],['setup-login-virtual-off']):
    from monitorize.remote_login.enrollment import main as setup
    def main():return setup(sys.argv[1].removeprefix('setup-'))
elif len(sys.argv)==3 and sys.argv[1]=='setup-timeout' and sys.argv[2].isdigit() and 1<=int(sys.argv[2])<=600:
    from monitorize.remote_login.enrollment import main as setup
    def main():return setup('timeout',int(sys.argv[2]))
else:
    raise SystemExit('Use the installed systemd unit.')
raise SystemExit(main())
