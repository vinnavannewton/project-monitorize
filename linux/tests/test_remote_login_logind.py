import unittest
from monitorize.remote_login.logind import LogindSessions


class Bus:
    uid=1000;pid=1234
    def __init__(self):
        self.session=dict(User=(1000,'/user'),Id='s1',Seat=('seat0','/seat'),Active=True,
                          Remote=False,Type='wayland',Class='user',LockedHint=False)
    def call(self,path,interface,method,*args):
        if method=='GetConnectionUnixUser':return self.uid
        if method=='GetConnectionUnixProcessID':return self.pid
        if method=='GetSessionByPID':return '/session'
        if method=='ListSessions':return [('s1',1000,'account','seat0','/session')]
        raise AssertionError(method)
    def properties(self,path,interface):return self.session


class LogindTests(unittest.TestCase):
    def test_caller_identity_comes_from_bus_and_logind(self):
        reader=LogindSessions(Bus())
        self.assertEqual(reader.authenticate(':1.50',1000),{'uid':1000,'pid':1234,'session':'s1'})

    def test_reject_uid_remote_inactive_tty_and_other_seat(self):
        for change in ({'User':(1001,'/user')},{'Remote':True},{'Active':False},{'Type':'tty'},
                       {'Seat':('seat1','/seat')},{'Class':'greeter'}):
            bus=Bus();bus.session.update(change)
            with self.assertRaises(PermissionError):LogindSessions(bus).authenticate(':1.50',1000)
        with self.assertRaises(PermissionError):LogindSessions(Bus()).authenticate(':1.50',1001)

    def test_well_known_or_untrusted_sender_is_rejected(self):
        for sender in ('org.fake.Name','',None):
            with self.assertRaises(PermissionError):LogindSessions(Bus()).authenticate(sender,1000)

    def test_snapshot_includes_locked_desktop_but_no_greeter(self):
        bus=Bus();bus.session['LockedHint']=True
        snapshot=LogindSessions(bus).snapshot()
        self.assertFalse(snapshot['greeter_ready']);self.assertTrue(snapshot['sessions'][0]['locked'])

    def test_greeter_is_separate_from_user_sessions(self):
        bus=Bus();bus.session.update(Class='greeter',User=(971,'/user'))
        snapshot=LogindSessions(bus).snapshot()
        self.assertTrue(snapshot['greeter_ready']);self.assertEqual(snapshot['sessions'],[])

    def test_missing_class_is_not_mistaken_for_empty_seat(self):
        bus=Bus();del bus.session['Class']
        with self.assertRaises(ValueError):LogindSessions(bus).snapshot()

class UserServiceTests(unittest.TestCase):
    def bus(self):
        class NoSession(Exception):
            def get_dbus_name(self):return 'org.freedesktop.login1.NoSessionForPID'
        class ServiceBus(Bus):
            def call(self,path,interface,method,*args):
                if method=='GetSessionByPID':raise NoSession()
                if method=='GetUserByPID':return '/user'
                return super().call(path,interface,method,*args)
            def properties(self,path,interface):
                if interface.endswith('.User'):return {'UID':1000,'Display':('s1','/session')}
                return self.session
        return ServiceBus()
    def test_fixed_user_controller_uses_verified_active_display_session(self):
        group='0::/user.slice/user-1000.slice/user@1000.service/app.slice/monitorize-remote-controller.service\n'
        result=LogindSessions(self.bus(),cgroup_reader=lambda pid:group).authenticate(':1.50',1000)
        self.assertEqual(result['session'],'s1')

    def test_desktop_application_and_nested_terminal_scopes_use_active_display(self):
        prefix='0::/user.slice/user-1000.slice/user@1000.service/app.slice/'
        for scope in ('app-monitorize-123.scope','app-org.kde.konsole-14288.scope/tab(14400).scope',
                      'app-org.example.Monitorize.service'):
            bus=self.bus()
            result=LogindSessions(bus,cgroup_reader=lambda pid:prefix+scope).authenticate(':1.50',1000)
            self.assertEqual(result['session'],'s1')
            bus.session['Active']=False
            with self.assertRaises(PermissionError):
                LogindSessions(bus,cgroup_reader=lambda pid:prefix+scope).authenticate(':1.50',1000)

    def test_other_user_units_and_wrong_uid_cgroups_are_rejected(self):
        for group in ('0::/user.slice/user-1000.slice/user@1000.service/app.slice/other.service',
                      '0::/user.slice/user-1001.slice/user@1001.service/app.slice/monitorize-remote-controller.service'):
            with self.assertRaises(PermissionError):
                LogindSessions(self.bus(),cgroup_reader=lambda pid:group).authenticate(':1.50',1000)

    def test_uwsm_graphical_application_uses_verified_active_display(self):
        prefix='0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-graphical.slice/'
        group=prefix+r'app-Hyprland-gtk\x2dlaunch-9f755744.scope'
        self.assertEqual(LogindSessions(self.bus(),cgroup_reader=lambda pid:group).authenticate(':1.50',1000)['session'],'s1')
        for change in ({'User':(1001,'/user')},{'Active':False},{'Remote':True},
                       {'Seat':('seat1','/seat')},{'Class':'manager'},{'Type':'tty'}):
            bus=self.bus();bus.session.update(change)
            with self.subTest(change=change),self.assertRaises(PermissionError):
                LogindSessions(bus,cgroup_reader=lambda pid:group).authenticate(':1.50',1000)

    def test_uwsm_does_not_allow_arbitrary_slices_or_units(self):
        prefix='0::/user.slice/user-1000.slice/user@1000.service/'
        for scope in ('app.slice/app-graphical.slice/other.service',
                      'app.slice/app-graphical.slice',
                      'app.slice/app-other.slice/app-monitorize.scope',
                      'app.slice/app-graphical.slice/other.slice/app-monitorize.scope',
                      'session.slice/app-graphical.slice/app-monitorize.scope'):
            with self.subTest(scope=scope),self.assertRaises(PermissionError):
                LogindSessions(self.bus(),cgroup_reader=lambda pid:prefix+scope).authenticate(':1.50',1000)

    def test_manager_session_resolves_only_verified_graphical_app_to_display(self):
        bus=self.bus()
        original_call=bus.call
        original_properties=bus.properties
        bus.call=lambda path,interface,method,*args: '/manager' if method=='GetSessionByPID' else original_call(path,interface,method,*args)
        bus.properties=lambda path,interface: dict(Class='manager',Type='unspecified') if path=='/manager' else original_properties(path,interface)
        prefix='0::/user.slice/user-1000.slice/user@1000.service/'
        for scope in ('app.slice/monitorize-remote-controller.service',
                      'app.slice/app-graphical.slice/app-Hyprland-monitorize.scope'):
            group=prefix+scope
            self.assertEqual(LogindSessions(bus,cgroup_reader=lambda pid:group).authenticate(':1.50',1000)['session'],'s1')
        for group in (prefix+'background.slice/other.service',
                      prefix.replace('1000','1001')+'app.slice/app-monitorize.scope'):
            with self.subTest(group=group),self.assertRaises(PermissionError):
                LogindSessions(bus,cgroup_reader=lambda pid:group).authenticate(':1.50',1000)
        bus.session['Active']=False
        with self.assertRaises(PermissionError):
            LogindSessions(bus,cgroup_reader=lambda pid:prefix+'app.slice/app-monitorize.scope').authenticate(':1.50',1000)

    def test_locked_keeps_ownership_but_inactive_other_account_does_not(self):
        group='0::/user.slice/user-1000.slice/user@1000.service/app.slice/monitorize-remote-controller.service'
        bus=self.bus();bus.session['LockedHint']=True
        self.assertEqual(LogindSessions(bus,cgroup_reader=lambda pid:group).authenticate(':1.50',1000)['uid'],1000)
        bus.session['Active']=False
        with self.assertRaises(PermissionError):LogindSessions(bus,cgroup_reader=lambda pid:group).authenticate(':1.50',1000)


class ConsoleScopeTests(unittest.TestCase):
    bus=UserServiceTests.bus
    scope=r'app-dbus\x2d:1.2\x2dorg.gnome.Console.slice/vte-spawn-67aeeca0-325d-47da-ac07-0b2369d173b2.scope'
    prefix='0::/user.slice/user-1000.slice/user@1000.service/app.slice/'

    def test_gnome_console_nested_scope_uses_verified_active_graphical_session(self):
        bus=self.bus()
        reader=LogindSessions(bus,cgroup_reader=lambda pid:self.prefix+self.scope)
        self.assertEqual(reader.authenticate(':1.50',1000)['session'],'s1')
        for change in ({'User':(1001,'/user')},{'Active':False},{'Remote':True},
                       {'Seat':('seat1','/seat')},{'Class':'greeter'},{'Type':'tty'}):
            other=self.bus();other.session.update(change)
            with self.subTest(change=change),self.assertRaises(PermissionError):
                LogindSessions(other,cgroup_reader=lambda pid:self.prefix+self.scope).authenticate(':1.50',1000)

    def test_console_scope_does_not_accept_other_slices_units_or_scope_shapes(self):
        for scope in (self.scope.replace('org.gnome.Console','org.example.Other'),
                      self.scope.replace('vte-spawn-','other-'),self.scope+'/child.scope',
                      self.scope.replace('67aeeca0-325d-47da-ac07-0b2369d173b2','invalid'),
                      'other.slice/'+self.scope,self.scope.replace('.Console.slice','.ConsoleOther.slice')):
            with self.subTest(scope=scope),self.assertRaises(PermissionError):
                LogindSessions(self.bus(),cgroup_reader=lambda pid:self.prefix+scope).authenticate(':1.50',1000)
        with self.assertRaises(PermissionError):
            LogindSessions(self.bus(),cgroup_reader=lambda pid:(self.prefix+self.scope).replace('1000','1001')).authenticate(':1.50',1000)

    def test_authentication_failures_have_a_distinct_dbus_error(self):
        from monitorize.remote_login.logind import CallerAuthenticationError
        from monitorize.remote_login.dbus_service import ExportedService,AuthenticationFailed
        def denied():raise CallerAuthenticationError('rejected')
        with self.assertRaises(AuthenticationFailed):ExportedService._invoke(None,denied)
