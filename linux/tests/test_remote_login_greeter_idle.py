"""Run the actual daemon greeter closure across an identity-lock transaction."""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from monitorize.remote_login import daemon
from monitorize.remote_login.engine_gate import EngineGate


class GreeterIdleTests(unittest.TestCase):
    def test_greeter_idle_revalidates_sender_without_reacquiring_host_identity_lock(self):
        source=Path(daemon.__file__).read_text()
        tree=ast.parse(source)
        factory=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='build_service')
        operation=next(node for node in factory.body if isinstance(node,ast.FunctionDef) and node.name=='greeter_display')
        with TemporaryDirectory() as directory:
            root=Path(directory);group=root/'engine';group.mkdir()
            (group/'cgroup.procs').write_text('123\n')
            busy=[False]
            class Bus:
                def call(self,path,interface,method,*args):
                    return {'GetConnectionUnixUser':1000,'GetConnectionUnixProcessID':123,'GetUnit':'/unit'}[method]
                def properties(self,*args):return dict(MainPID=123,ControlGroup='/engine')
            gate=EngineGate(1000,Bus(),lambda:dict(uid=1000,session='g1',generation='a'*32),
                lambda *args:True,lambda:not busy[0],session_check=lambda *args:True,cgroup_root=root,phase='boot')
            context=dict(session='g1',greeter_uid=42)
            def boot_gate(sender,transport,*,idle):
                gate.check(sender,'a'*32,idle=idle)
                return gate,context
            connector=root/'status';connector.write_text('1')
            def host_operation(operation,*,context_check,activate):
                self.assertEqual(operation,'idle')
                busy[0]=True
                try:
                    context_check()
                    connector.write_text('2')
                    context_check()
                    return dict(connector='Virtual-2')
                finally:busy[0]=False
            namespace=dict(worker_context=lambda:(SimpleNamespace(close=lambda:None),None,None),
                __name__=daemon.__name__,__package__=daemon.__package__,
                boot_gate=boot_gate,helper=SimpleNamespace(_active_session=lambda uid:('g1',{})),
                settings=SimpleNamespace(),service=SimpleNamespace(boot_generation='a'*32),
                greeter_operation_installed=host_operation)
            exec(compile(ast.Module(body=[operation],type_ignores=[]),daemon.__file__,'exec'),namespace)
            with patch('monitorize.remote_login.wayland_greeter.greeter_compositor',return_value='gnome'):
                result=namespace['greeter_display'](':1.5','idle')
            self.assertEqual(connector.read_text(),'2')
            self.assertEqual(result['output_name'],'Virtual-2')
