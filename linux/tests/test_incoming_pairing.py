"""Incoming requests require explicit approval and stay scoped to host identity."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QObject, QCoreApplication, QEventLoop, QTimer, pyqtSignal
from monitorize.desktop.incoming_pairing import IncomingPairing
from monitorize.platform import pairing_api as api


ID1, ID2 = 'a1' * 16, 'b2' * 16


def row(instance=1, pairing_id=ID1, name='Tablet'):
    return dict(id=pairing_id, instance=instance, name=name, address='100.64.0.2')


class PairingApiTests(unittest.TestCase):
    def connection(self, body, certificate=b'expected'):
        connection = Mock()
        connection.sock.getpeercert.return_value = certificate
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(body).encode()
        return connection

    def test_pin_never_reaches_a_host_with_a_different_certificate(self):
        connection = self.connection({}, b'wrong')
        with patch.object(api.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                api.PairingHost(2, b'expected').request('POST', {'pin': '0123'})
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_loopback_post_targets_exact_instance_id_and_preserves_leading_zero(self):
        connection = self.connection({'status': True, 'pairing_complete': True})
        with patch.object(api.http.client, 'HTTPSConnection', return_value=connection) as create:
            result = api.PairingHost(2, b'expected').request('POST', {'pairing_id': ID2, 'pin': '0123'})
        self.assertTrue(result['pairing_complete'])
        self.assertEqual(create.call_args.args, ('127.0.0.1', 49090))
        self.assertEqual(connection.request.call_args.args, ('POST', '/api/pin'))
        self.assertEqual(json.loads(connection.request.call_args.kwargs['body']), {'pairing_id': ID2, 'pin': '0123'})

    def test_reject_deletes_only_selected_request(self):
        host = api.PairingHost(2, b'expected')
        with patch.object(api.PairingHost, 'request', return_value={'status': True}) as request:
            self.assertTrue(host.reject(ID2)[0])
        request.assert_called_once_with('DELETE', {'pairing_id': ID2})

    def test_expired_request_never_submits_pin(self):
        host = api.PairingHost(1, b'expected')
        with patch.object(api.PairingHost, 'request', return_value={'pairings': []}) as request:
            self.assertFalse(host.approve(ID1, '0123', 'Tablet')[0])
        request.assert_called_once_with()

    def test_upstream_completion_is_required_and_labels_are_bounded(self):
        host = api.PairingHost(1, b'expected')
        for result in ({'status': True}, {'status': False}, {'status': True, 'pairing_complete': False}):
            with self.subTest(result=result), patch.object(api.PairingHost, 'pending', return_value=[row()]), \
                 patch.object(api.PairingHost, 'request', return_value=result):
                self.assertFalse(host.approve(ID1, '0123', 'Tablet')[0])
        with patch.object(api.PairingHost, 'request', return_value={'pairings': [
                {'id': ID1, 'name': 'é'*200+'\n', 'address': '\n100.64.0.2'},
                {'id': 'invalid'}, None]}):
            pending = host.pending()
        self.assertEqual(len(pending), 1)
        self.assertLessEqual(len(pending[0]['name'].encode()), 128)
        self.assertEqual(pending[0]['address'], '100.64.0.2')

    def test_non_ascii_or_invalid_pin_never_contacts_host(self):
        for pin in ('123', '١٢٣٤', 'abcd'):
            with patch.object(api.PairingHost, 'request') as request:
                self.assertFalse(api.PairingHost(1, b'cert').approve(ID1, pin, 'Tablet')[0])
            request.assert_not_called()

    def test_invalid_and_oversized_responses_are_rejected(self):
        for body in ([], {'pairings': 'bad'}, {'pairings': [{}]*65}):
            connection = self.connection(body)
            with patch.object(api.http.client, 'HTTPSConnection', return_value=connection):
                with self.assertRaises(ValueError):api.PairingHost(1, b'expected').pending()

    def test_service_owned_host_uses_enrolled_certificate_without_child_process(self):
        from types import SimpleNamespace
        # Generate a temporary public certificate only when the test needs a fixture.
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory)/'cert.pem'
            key = Path(directory)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=pairing-test'],
                check=True,capture_output=True)
            settings = SimpleNamespace(enabled=True,uid=os.getuid(),directory=Path(directory))
            with patch('monitorize.remote_login.settings.read_settings',return_value=settings), \
                 patch('monitorize.platform.remote_login_identity.identity_paths',return_value=SimpleNamespace(cert=cert)), \
                 patch.object(api.sunshine,'is_sunshine_running',return_value=False):
                hosts = api.resolve_hosts((1,), True)
            self.assertEqual(hosts, [api.PairingHost(1, ssl.PEM_cert_to_DER_cert(cert.read_text()))])
            settings.uid += 1
            with patch('monitorize.remote_login.settings.read_settings',return_value=settings):
                self.assertEqual(api.resolve_hosts((1,), True), [])


class Backend(QObject):
    uiVisibleChanged = pyqtSignal(bool)
    uiVisible = True
    remoteLoginEnabled = False


class IncomingPairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.backend = Backend()
        self.backend._background_executor = ThreadPoolExecutor(max_workers=1)
        self.controller = IncomingPairing(self.backend)
        self.addCleanup(self.backend._background_executor.shutdown, wait=True)
        self.addCleanup(self.controller.close)
        self.scope = ((1, 2), False)
        scope = patch.object(self.controller, '_scope', return_value=self.scope)
        scope.start();self.addCleanup(scope.stop)
        self.hosts = {1: Mock(instance=1), 2: Mock(instance=2)}

    def receive(self, rows):
        self.controller._receive(self.controller._epoch,
            (self.scope, {(r['instance'],r['id']):r for r in rows}, self.hosts))

    def settle(self):
        loop=QEventLoop();QTimer.singleShot(100,loop.quit);loop.exec()

    def test_accept_opens_pin_without_contacting_host_and_reject_routes_exact_id(self):
        self.receive([row(2, ID2)])
        self.controller.accept()
        self.assertEqual(self.controller.phase, 'pin')
        self.hosts[2].approve.assert_not_called()
        self.hosts[2].reject.return_value = (True,'Rejected')
        with patch('monitorize.desktop.incoming_pairing.resolve_hosts',return_value=list(self.hosts.values())):
            self.controller.reject();self.settle()
        self.hosts[2].reject.assert_called_once_with(ID2)
        self.hosts[1].reject.assert_not_called()
        self.assertEqual(self.controller.phase, 'done')

    def test_two_requests_queue_and_dismissed_request_does_not_repeat_after_outage(self):
        requests=[row(),row(2,ID2)]
        self.receive(requests)
        self.controller.dismiss()
        self.assertEqual(self.controller.request['instance'], 2)
        self.controller.dismiss()
        self.receive([]);self.receive(requests)
        self.assertFalse(self.controller.request)
        self.receive([row(1, 'c3'*16)])
        self.assertTrue(self.controller.request)

    def test_expiration_retires_popup_and_cannot_submit_to_another_request(self):
        self.receive([row()]);self.controller.accept()
        self.receive([row(2,ID2)])
        self.assertEqual(self.controller.phase, 'request')
        self.assertEqual(self.controller.request['id'], ID2)
        self.controller.submit('0123')
        self.hosts[1].approve.assert_not_called()
        self.hosts[2].approve.assert_not_called()

    def test_failed_pin_does_not_report_success_or_route_to_other_instance(self):
        self.receive([row(2, ID2)]);self.controller.accept()
        self.hosts[2].approve.return_value=(False,'Pairing did not complete')
        with patch('monitorize.desktop.incoming_pairing.resolve_hosts',return_value=list(self.hosts.values())):
            self.controller.submit('0123');self.settle()
        self.assertEqual(self.controller.phase,'pin')
        self.assertFalse(self.controller.busy)
        self.hosts[2].approve.assert_called_once_with(ID2,'0123','Tablet')
        self.hosts[1].approve.assert_not_called()

    def test_replaced_host_never_receives_pin(self):
        self.receive([row()]);self.controller.accept()
        with patch('monitorize.desktop.incoming_pairing.resolve_hosts',return_value=[]):
            self.controller.submit('0123');self.settle()
        self.hosts[1].approve.assert_not_called()
        self.assertIn('changed identity',self.controller.message)

    def test_background_and_duplicate_polls_do_not_spawn_requests(self):
        self.backend.uiVisible=False
        with patch.object(self.backend._background_executor,'submit') as submit:
            self.controller.poll()
        submit.assert_not_called()
        self.backend.uiVisible=True
        self.controller._polling=True
        with patch.object(self.backend._background_executor,'submit') as submit:self.controller.poll()
        submit.assert_not_called()

    def test_dismissed_history_is_bounded(self):
        for i in range(1000):self.controller._remember_dismissal((1,str(i)))
        self.assertEqual(len(self.controller._dismissed),512)


class PairingPopupTests(unittest.TestCase):
    def test_popup_is_global_plain_text_and_accept_requires_exact_pin_then_rejects_next_request(self):
        from linux.tests.test_host_configuration_ui import PROGRAM
        prefix = PROGRAM.split('            # Live changes')[0]
        scenario = r'''
            from PyQt6.QtCore import QMetaObject
            view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
            view.resize(900,650);view.show()
            incoming=backend.incomingPairing
            def find(name):return view.rootObject().findChild(QObject,name)
            host1=Mock(instance=1);host2=Mock(instance=2)
            host1.approve.return_value=(True,'Paired successfully.')
            host2.reject.return_value=(True,'Pairing request rejected.')
            rows={(1,'a1'*16):dict(instance=1,id='a1'*16,name='<b>Tablet</b>',address='100.64.0.2'),
                  (2,'b2'*16):dict(instance=2,id='b2'*16,name='Laptop',address='100.64.0.3')}
            scope=((1,2),False)
            with patch.object(incoming,'poll'),patch.object(incoming,'_scope',return_value=scope), \
                 patch('monitorize.desktop.incoming_pairing.resolve_hosts',return_value=[host1,host2]):
                backend.set_ui_visible(True)
                incoming.timer.stop()
                incoming._receive(incoming._epoch,(scope,rows,{1:host1,2:host2}));settle()
                popup=find('incomingPairingPopup')
                assert popup.property('visible')
                assert find('incomingPairingClient').property('text')=='<b>Tablet</b> wants to pair.'
                label=find('incomingPairingClient')
                expression=QQmlExpression(qmlContext(label),label,'textFormat === Text.PlainText')
                value=expression.evaluate()
                assert not expression.hasError() and value[0]
                assert 'Display 1' in find('incomingPairingAddress').property('text')
                QMetaObject.invokeMethod(find('acceptIncomingPairing'),'clicked');settle()
                assert incoming.phase=='pin'
                host1.approve.assert_not_called()
                pin=find('incomingPairingPin')
                assert pin.property('visible')
                pin.setProperty('text','0123')
                QMetaObject.invokeMethod(find('acceptIncomingPairing'),'clicked');settle();settle()
                assert incoming.phase=='done'
                host1.approve.assert_called_once_with('a1'*16,'0123','<b>Tablet</b>')
                assert pin.property('text')==''
                incoming.dismiss();settle()
                assert incoming.request['instance']==2
                QMetaObject.invokeMethod(find('rejectIncomingPairing'),'clicked');settle();settle()
                host2.reject.assert_called_once_with('b2'*16)
                host1.reject.assert_not_called()
                assert incoming.phase=='done'
                incoming.dismiss();settle()
                assert not popup.property('visible')
            view.setSource(QUrl());backend.close()
print('Global incoming pairing popup and exact-instance actions passed')
'''
        result = subprocess.run([sys.executable, '-c', prefix + scenario],
            cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
