"""Exercise guardian ownership with real local sockets and harmless children."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitorize.streaming.recovery_guardian import Guardian, GuardianClient, _frame
from monitorize.platform.session_layout import LayoutTransaction
from monitorize.platform.xrandr_layout import resolve_output
from linux.tests.test_additional_layout_backends import XServer, xoutput
from linux.tests.test_session_layout import SimulatedKde, output


class TestGuardian(Guardian):
    async def spawn_holder(self, args, environment):
        # Never launch a real display backend in automated tests.
        return await asyncio.create_subprocess_exec(sys.executable, '-c',
            "import sys; print('holder-ready', flush=True); sys.stdin.readline(); print('holder-cleaned', flush=True)",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)


class GuardianProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'control.sock')
        self.backend = SimulatedKde()
        self.layout = LayoutTransaction(self.backend, Path(self.temp.name) / 'journal.json', 'virtual_only')
        self.guardian = TestGuardian(self.path, 'secret', 'virtual_only', os.getpid(), self.layout)
        self.server = await asyncio.start_unix_server(self.guardian.handle, self.path)
        self.guardian.server = self.server
        self.relays = []

    async def asyncTearDown(self):
        self.backend.fail_restore = False
        for slot in list(self.guardian.reserved):
            await self.guardian.release(slot)
        for writer in self.relays:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        await asyncio.sleep(.02)
        self.temp.cleanup()

    async def command(self, **request):
        reader, writer = await asyncio.open_unix_connection(self.path)
        writer.write(_frame(dict(token='secret', **request)))
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
        writer.close()
        await writer.wait_closed()
        return reply

    async def test_borrowed_owner_death_restores_without_removing_output(self):
        self.backend.state.append(output('Virtual-1',2))
        self.assertTrue((await self.command(operation='attach',slot=1,name='Virtual-1'))['ok'])
        self.assertTrue((await self.command(operation='stream',slot=1,active=True))['ok'])
        self.guardian.owner=-1
        await asyncio.wait_for(self.guardian.supervise(),2)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertTrue(self.backend.state[1]['enabled'])
        self.assertFalse(self.guardian.reserved)

    async def test_borrowed_remote_output_restores_without_destroying_it(self):
        self.backend.state.append(output('Virtual-1',2))
        result=await self.command(operation='attach',slot=1,name='Virtual-1')
        self.assertTrue(result['ok'],result)
        self.assertNotIn('Virtual-1',[s['name'] for s in self.layout.snapshot])
        result=await self.command(operation='stream',slot=1,active=True)
        self.assertTrue(result['ok'],result)
        self.assertFalse(self.backend.state[0]['enabled'])
        result=await self.command(operation='detach',slot=1)
        self.assertTrue(result['ok'],result)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertTrue(self.backend.state[1]['enabled'])
        self.assertFalse(self.guardian.reserved)

    async def test_x11_borrowed_alias_is_excluded_from_snapshot_and_retained_on_detach(self):
        self.backend = self.layout.backend = XServer()
        virtual = dict(xoutput('Virtual-1-1'), connector_id=48)
        self.backend.state.append(virtual)
        root = Path(self.temp.name)
        connector = root / 'card2-Virtual-1'
        connector.mkdir()
        with patch('monitorize.platform.xrandr_layout.resolve_output',
                   side_effect=lambda name, outputs, **kwargs: resolve_output(name, outputs, root, **kwargs)):
            missing = await self.command(operation='attach', slot=1, name='Virtual-1')
            self.assertFalse(missing['ok'])
            self.assertIn('connector ID', missing['error'])
            self.assertFalse(self.layout.journal.exists())
            (connector / 'connector_id').write_text('48')
            attached = await self.command(operation='attach', slot=1, name='Virtual-1')
        self.assertTrue(attached['ok'], attached)
        self.assertEqual(self.layout.targets, {1: 'Virtual-1-1'})
        self.assertNotIn('Virtual-1-1', [saved['name'] for saved in self.layout.snapshot])
        streamed = await self.command(operation='stream', slot=1, active=True)
        self.assertTrue(streamed['ok'], streamed)
        self.assertFalse(self.backend.state[0]['enabled'])
        self.assertTrue(virtual['primary'])
        detached = await self.command(operation='detach', slot=1)
        self.assertTrue(detached['ok'], detached)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertTrue(self.backend.state[0]['primary'])
        self.assertTrue(virtual['enabled'])
        self.assertFalse(self.layout.journal.exists())

    async def hold(self, slot=1):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.relays.append(writer)
        writer.write(_frame(dict(token='secret', operation='hold', slot=slot,
                                 args=['1920', '1080', '60', 'primary', 'kde', 'native'])))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readline(), 2), b'holder-ready\n')
        self.backend.state.append(output(f'Virtual-{slot}', slot + 1))
        self.assertTrue((await self.command(operation='ready', slot=slot, name=f'Virtual-{slot}'))['ok'])
        return reader, writer

    async def test_disconnect_restores_before_holder_is_reaped(self):
        reader, writer = await self.hold()
        self.assertTrue((await self.command(operation='stream', slot=1, active=True))['ok'])
        self.assertFalse(self.backend.state[0]['enabled'])
        process = self.guardian.holders[1]
        writer.write_eof()
        await asyncio.wait_for(reader.read(), 2)
        self.assertTrue(self.backend.state[0]['enabled'])
        self.assertEqual(process.returncode, 0)
        self.assertNotIn(1, self.guardian.holders)

    async def test_restore_failure_retains_real_holder_until_recovery(self):
        reader, writer = await self.hold()
        await self.command(operation='stream', slot=1, active=True)
        process = self.guardian.holders[1]
        self.backend.fail_restore = True
        writer.write_eof()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
        self.assertFalse(reply['ok'])
        self.assertIsNone(process.returncode)
        self.assertIn(1, self.guardian.pending_release)
        self.backend.fail_restore = False
        await self.guardian.release(1)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(self.backend.state[0]['enabled'])

    async def test_gui_owner_loss_restores_and_cleans_without_gui_cooperation(self):
        owner = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(10)')
        try:
            from monitorize.streaming.recovery_guardian import _identity
            self.guardian.owner = owner.pid
            self.guardian.owner_identity = _identity(owner.pid)
            await self.hold()
            await self.command(operation='stream', slot=1, active=True)
            owner.terminate()
            await owner.wait()
            await asyncio.wait_for(self.guardian.supervise(), 3)
            self.assertTrue(self.backend.state[0]['enabled'])
            self.assertFalse(self.guardian.holders)
        finally:
            if owner.returncode is None:
                owner.kill()
                await owner.wait()

    async def test_unauthorized_request_cannot_create_holder(self):
        reader, writer = await asyncio.open_unix_connection(self.path)
        writer.write(_frame(dict(token='wrong', operation='hold')))
        await writer.drain()
        self.assertFalse(json.loads(await reader.readline())['ok'])
        writer.close()
        await writer.wait_closed()
        self.assertFalse(self.guardian.holders)

    async def test_shared_mode_cannot_change_while_holder_exists(self):
        await self.hold()
        self.assertFalse((await self.command(operation='configure', mode='primary'))['ok'])
        self.assertEqual(self.layout.mode, 'virtual_only')

    async def test_failed_holder_reports_verified_release_before_startup_error(self):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.relays.append(writer)
        writer.write(_frame(dict(token='secret', operation='hold', slot=1,
                                 args=['1920', '1080', '60', 'primary', 'kde', 'vkms'])))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readline(), 2), b'holder-ready\n')
        self.guardian.holders[1].kill()
        line = await asyncio.wait_for(reader.readline(), 2)
        self.assertTrue(line.startswith(b'MONITORIZE_EVENT '))
        receipt = json.loads(line.split(b' ', 1)[1])
        self.assertEqual(receipt, {'type': 'guardian_result', 'cleanup_complete': True})
        failure = json.loads(await asyncio.wait_for(reader.readline(), 2))
        self.assertFalse(failure['ok'])
        self.assertFalse(self.guardian.reserved)
        self.assertFalse(self.layout.journal.exists())

    async def test_failed_holder_keeps_journal_until_leftover_output_is_absent(self):
        reader, _writer = await self.hold()
        await self.command(operation='stream', slot=1, active=True)
        process = self.guardian.holders[1]
        process.kill()
        await process.wait()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
        await asyncio.wait_for(reader.read(), 2)
        self.assertFalse(reply['ok'])
        self.assertTrue(self.layout.journal.exists())
        self.assertTrue(self.backend.state[0]['enabled'])
        self.backend.state = [o for o in self.backend.state if o['name'] != 'Virtual-1']
        await self.guardian.release(1)
        self.assertFalse(self.layout.journal.exists())
        self.assertFalse(self.guardian.holders)


class DetachedGuardianTest(unittest.TestCase):
    def test_detached_manager_exits_after_owner_dies_without_layout_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No holder is requested: no KScreen commands can be run here.
            code = """
import os,json
from unittest.mock import patch
from monitorize.streaming.recovery_guardian import GuardianClient
client=GuardianClient('primary','kde',os.environ['TEST_RUNTIME'])
with patch('monitorize.streaming.recovery_guardian.layout_supported',return_value=True):
    client.start()
print(json.dumps({'pid':client.process.pid,'path':client.path}),flush=True)
"""
            env = {**os.environ, 'TEST_RUNTIME': tmp, 'XDG_STATE_HOME': tmp,
                   'XDG_SESSION_ID': 'monitorize-test', 'XDG_SESSION_TYPE': 'wayland'}
            result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=8, check=True)
            data = json.loads(result.stdout)
            import time
            deadline = time.monotonic() + 4
            while Path(data['path']).exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(Path(data['path']).exists(), 'Detached guardian failed to notice owner exit')
            self.assertFalse(list(Path(tmp).rglob('*.json')))

    def test_sigkill_gui_retains_independent_holder_until_physical_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            manager_file = folder / 'manager.py'
            events = folder / 'events'
            path = str(folder / 'guardian.sock')
            manager_file.write_text('''
import asyncio,os,sys
sys.path.insert(0,os.getcwd())
from pathlib import Path
from monitorize.streaming.recovery_guardian import Guardian
from monitorize.platform.session_layout import LayoutTransaction
from linux.tests.test_session_layout import SimulatedKde, output
path, event_path, journal, owner = sys.argv[1:]
class Backend(SimulatedKde):
    def apply(self, settings):
        super().apply(settings)
        if 'output.eDP-1.enable' in settings:
            with open(event_path,'a') as f: f.write('physical-restored\\n')
class Manager(Guardian):
    async def spawn_holder(self,args,environment):
        self.layout.backend.state.append(output('Virtual-1',2))
        code="import sys; print('holder-ready',flush=True); sys.stdin.readline(); open(sys.argv[1],'a').write('holder-cleaned\\\\n')"
        return await asyncio.create_subprocess_exec(sys.executable,'-c',code,event_path,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
layout=LayoutTransaction(Backend(),journal,'virtual_only')
manager=Manager(path,'token','virtual_only',int(owner),layout)
asyncio.run(manager.run())
''')
            gui_code = '''
import json,os,socket,subprocess,sys,time
manager,path,events,journal=sys.argv[1:]
p=subprocess.Popen([sys.executable,manager,path,events,journal,str(os.getpid())],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
def command(operation,**values):
    peer=socket.socket(socket.AF_UNIX); peer.connect(path)
    peer.sendall((json.dumps(dict(token='token',operation=operation,**values))+'\\n').encode())
    reply=peer.makefile('rb').readline()
    assert json.loads(reply)['ok'],reply
    peer.close()
for i in range(200):
    if os.path.exists(path):break
    time.sleep(.01)
holder=socket.socket(socket.AF_UNIX); holder.connect(path)
holder.sendall((json.dumps(dict(token='token',operation='hold',slot=1,args=['1920','1080','60','primary','kde','native']))+'\\n').encode())
assert holder.makefile('rb').readline()==b'holder-ready\\n'
command('ready',slot=1,name='Virtual-1')
command('stream',slot=1,active=True)
print(p.pid,flush=True)
time.sleep(30)
'''
            gui = subprocess.Popen([sys.executable, '-c', gui_code, str(manager_file), path,
                                    str(events), str(folder / 'journal.json')],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            manager_pid = None
            try:
                import select
                self.assertTrue(select.select([gui.stdout], [], [], 5)[0], 'Fake GUI did not start')
                line = gui.stdout.readline().strip()
                if not line:
                    self.fail(gui.stderr.read())
                manager_pid = int(line)
                gui.kill()
                gui.wait(timeout=2)
                import time
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if events.exists() and 'holder-cleaned' in events.read_text():
                        break
                    time.sleep(.05)
                recorded = events.read_text()
                self.assertIn('holder-cleaned', recorded)
                self.assertLess(recorded.index('physical-restored'), recorded.index('holder-cleaned'))
            finally:
                if gui.poll() is None:
                    gui.kill()
                gui.wait(timeout=2)
                gui.stdout.close()
                gui.stderr.close()
                if manager_pid:
                    try:
                        os.kill(manager_pid, 15)
                    except ProcessLookupError:
                        pass
