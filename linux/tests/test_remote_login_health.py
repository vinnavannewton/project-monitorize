import os
from pathlib import Path
import tempfile
import unittest
from monitorize.remote_login.health import inspect_startup,write_health,read_health


class HealthTests(unittest.TestCase):
    def test_nvenc_exit_explains_source_and_package_repair_without_exposing_log(self):
        from monitorize.remote_login.health import startup_failure
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'desktop-console.log'
            log.write_text('untrusted private text\nFatal: MONITORIZE_STRICT_SELECTION_FAILED: requested encoder [nvenc] is unavailable\n')
            error=startup_failure(log,1)
            self.assertIn('NVIDIA',error)
            self.assertIn('CUDA',error)
            self.assertIn('remote-login',error)
            self.assertIn('package',error)
            self.assertNotIn('untrusted private text',error)
            log.write_text('unrelated startup error')
            self.assertIn('status 7',startup_failure(log,7))
            log.unlink()
            self.assertIn('status 9',startup_failure(log,9))

    def test_port_collision_reports_endpoint_without_private_log_text(self):
        from monitorize.remote_login.health import startup_failure
        with tempfile.TemporaryDirectory() as d:
            log = Path(d)/'desktop-console.log'
            log.write_text("private text\nFatal: Couldn't bind RTSP server to port [50110], Address already in use\n")
            error = startup_failure(log, 0)
            self.assertIn('port 50110', error)
            self.assertNotIn('private text', error)

    def test_no_stale_startup_or_wrong_output_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'engine.log'
            log.write_text("Mapped 'Virtual-4' to card7\nFound H.264 encoder: libx264 [software]\nConfiguration UI available\n")
            self.assertTrue(inspect_startup(log,0,'Virtual-4')['capture_ready'])
            self.assertFalse(inspect_startup(log,log.stat().st_size,'Virtual-4')['capture_ready'])
            self.assertFalse(inspect_startup(log,0,'Virtual-5')['capture_ready'])
            with log.open('a') as stream:stream.write('MONITORIZE_STRICT_SELECTION_FAILED\n')
            self.assertFalse(inspect_startup(log,0,'Virtual-4')['capture_ready'])

    def test_health_requires_current_unit_pid_boot_and_recent_time(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'health.json'
            write_health(path,dict(pid=123,boot_id='boot',time=100,capture_ready=True))
            def read(**kw):return read_health(path,**dict(dict(uid=os.getuid(),pid=123,boot_id='boot',now=101),**kw))
            self.assertTrue(read()['capture_ready'])
            for kw in ({'pid':124},{'boot_id':'old'},{'now':104},{'now':99}):self.assertEqual(read(**kw),{})

    def test_listener_scan_advances_past_old_failure_and_large_log(self):
        from monitorize.remote_login.health import ListenerEvidence
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';path.write_bytes(b'MONITORIZE_STRICT_FAILED\n'+b'x'*140000+b'\nConfiguration UI available\n')
            evidence=ListenerEvidence()
            self.assertFalse(evidence.read(path));self.assertTrue(evidence.read(path))
            self.assertTrue(evidence.read(path))
            self.assertFalse(ListenerEvidence().listening)
