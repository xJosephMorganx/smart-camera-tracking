"""Local API, lifecycle and virtual-output checks without moving hardware."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import tracking_service as service_module
from tracking_service import TrackingService
from tracking_targets import TrackingTarget
from web_app import create_app


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.settings_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_directory.cleanup)
        self.service = TrackingService(preview_only=True, settings_path=Path(self.settings_directory.name) / 'settings.json')
        self.app = create_app(self.service)
        self.client = self.app.test_client()
        page = self.client.get('/').data.decode()
        self.token = re.search(r'name="control-token" content="([^"]+)"', page)[1]
        self.headers = {'X-Control-Token': self.token}

    def test_page_and_status_do_not_open_hardware(self):
        self.assertIsNone(self.service.thread)
        self.assertEqual(self.client.get('/api/status').json['phase'], 'idle')
        for asset in ('app.js', 'app.css'):
            with self.client.get('/static/' + asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_camera_selection_preserves_mode_and_pauses_motion(self):
        self.service.state['phase'] = 'running'
        self.service.settings.update(mode='face', tracking=True)
        cameras = [{'id': '/dev/video0', 'label': 'USB camera'}]
        with patch.object(service_module, 'list_cameras', return_value=cameras):
            response = self.client.post('/api/camera', json={'camera': '/dev/video0'}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['camera'], '/dev/video0')
            self.assertEqual(response.json['mode'], 'face')
            self.assertFalse(response.json['tracking'])
            for source in ('/dev/video10', None, 0):
                response = self.client.post('/api/camera', json={'camera': source}, headers=self.headers)
                self.assertEqual(response.status_code, 400)
            self.service.state['phase'] = 'starting'
            response = self.client.post('/api/camera', json={'camera': '/dev/video0'}, headers=self.headers)
            self.assertEqual(response.status_code, 409)

    def test_home_requires_running_hardware_session_and_pauses_tracking(self):
        self.assertEqual(self.client.post('/api/home', headers=self.headers).status_code, 409)
        self.service.preview_only = False
        self.assertEqual(self.client.post('/api/home', headers=self.headers).status_code, 409)
        self.service.state['phase'] = 'running'
        self.service.settings.update(mode='face', tracking=True)
        response = self.client.post('/api/home', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['homing'])
        self.assertEqual(response.json['mode'], 'static')
        self.assertFalse(response.json['tracking'])
        self.assertEqual(self.client.post('/api/settings', json={'mode': 'face'}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/api/home').status_code, 403)
        self.service.stop()
        self.assertFalse(self.service.state['homing'])
        self.assertEqual(self.service.home_remaining, 0)

    def test_home_sequence_is_paced_and_covers_full_firmware_range(self):
        self.service.preview_only = False
        self.service.state['phase'] = 'running'
        self.service.home()
        connection = MagicMock()
        with patch.object(service_module.time, 'perf_counter') as clock:
            for tick in range(50):
                clock.return_value = 1 + tick * 0.1
                self.service._send_home_tick(connection)
                self.service._send_home_tick(connection)
            self.assertEqual(connection.write.call_count, 50)
            self.assertTrue(all(call.args == (b'H\n',) for call in connection.write.call_args_list))
            clock.return_value = 7
            self.service._send_home_tick(connection)
        self.assertFalse(self.service.state['homing'])
        # Match the supplied firmware, including its angle limits.
        for initial in range(181):
            for target in (80, 100):
                position = initial
                for _ in range(connection.write.call_count):
                    position += max(-2, min(2, target - position))
                self.assertEqual(position, target)

    def test_hidden_preview_skips_jpeg_encoding_without_disabling_outputs(self):
        self.service.state['phase'] = 'running'
        self.service.settings.update(mode='face', virtual=True)
        response = self.client.post('/api/settings', json={'preview': False}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['preview'])
        self.assertTrue(response.json['virtual'])
        self.assertEqual(response.json['mode'], 'face')
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        with patch.object(service_module.cv2, 'imencode') as encode:
            self.service._frame(frame)
            encode.assert_not_called()
        self.assertEqual(self.service.snapshot()['frame_width'], 1280)
        self.client.post('/api/settings', json={'preview': True}, headers=self.headers)
        sequence = self.service.sequence
        self.service._frame(frame)
        self.assertGreater(self.service.sequence, sequence)

    def test_controls_require_token_and_same_origin(self):
        self.assertEqual(self.client.post('/api/settings', json={'mode': 'hand'}).status_code, 403)
        headers = dict(self.headers, Origin='https://example.com')
        self.assertEqual(self.client.post('/api/settings', json={'mode': 'hand'}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/status', headers={'Host': 'example.com'}).status_code, 400)

    def test_settings_validation_and_preview_mode(self):
        self.assertEqual(self.client.post('/api/settings', json={'mode': 'hand'}, headers=self.headers).status_code, 409)
        self.service.state['phase'] = 'running'
        for payload in ({'mode': 'invalid'}, {'virtual': 'yes'}, {'unknown': 1}, [], None):
            self.assertEqual(self.client.post('/api/settings', json=payload, headers=self.headers).status_code, 400)
        response = self.client.post('/api/settings', json={'mode': 'hand', 'virtual': True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['mode'], 'hand')
        self.assertFalse(response.json['virtual_active'])
        self.assertEqual(self.client.post('/api/settings', json={'tracking': True}, headers=self.headers).status_code, 400)

    def test_start_and_stop_endpoints(self):
        with patch.object(self.service, 'start') as start:
            self.assertEqual(self.client.post('/api/session/start', headers=self.headers).status_code, 200)
            start.assert_called_once()
        self.service.settings.update(tracking=True, virtual=True)
        self.client.post('/api/session/stop', headers=self.headers)
        self.assertFalse(self.service.settings['tracking'])
        self.assertFalse(self.service.settings['virtual'])

    def test_face_and_hand_enable_tracking_but_manual_pause_remains_available(self):
        self.service.preview_only = False
        self.service.state['phase'] = 'running'
        for mode in ('face', 'hand'):
            response = self.client.post('/api/settings', json={'mode': mode}, headers=self.headers)
            self.assertTrue(response.json['tracking'])
            response = self.client.post('/api/settings', json={'tracking': False}, headers=self.headers)
            self.assertFalse(response.json['tracking'])
            response = self.client.post('/api/settings', json={'mode': mode}, headers=self.headers)
            self.assertTrue(response.json['tracking'])

    def test_preview_mode_never_auto_enables_movement(self):
        self.service.state['phase'] = 'running'
        for mode in ('face', 'hand'):
            response = self.client.post('/api/settings', json={'mode': mode}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json['tracking'])

    def test_static_mode_disables_tracking(self):
        self.service.preview_only = False
        self.service.state['phase'] = 'running'
        self.service.settings.update(mode='face', tracking=True)
        response = self.client.post('/api/settings', json={'mode': 'static'}, headers=self.headers)
        self.assertFalse(response.json['tracking'])
        response = self.client.post('/api/settings', json={'tracking': True}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_live_pinch_threshold_validation_and_update(self):
        self.service.state['phase'] = 'running'
        for invalid in (True, 0.04, 0.61, 20, '0.20', None, float('nan'), float('inf')):
            response = self.client.post('/api/settings', json={'pinch_threshold': invalid}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        revision = self.service.revision
        response = self.client.post('/api/settings', json={'pinch_threshold': 0.12}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['pinch_threshold'], 0.12)
        self.assertEqual(response.json['pinch_release_margin'], 0.06)
        self.assertGreater(self.service.revision, revision)
        self.assertFalse(response.json['tracking'])

    def test_pinch_is_restored_after_app_restart(self):
        self.service.state['phase'] = 'running'
        response = self.client.post('/api/settings', json={'pinch_threshold': 0.17}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        restarted = TrackingService(settings_path=self.service.settings_path)
        self.assertEqual(restarted.snapshot()['pinch_threshold'], 0.17)
        self.assertEqual(restarted.snapshot()['mode'], 'static')
        self.assertFalse(restarted.snapshot()['tracking'])

    def test_failed_save_preserves_current_value(self):
        self.service.state['phase'] = 'running'
        original = self.service.settings['pinch_threshold']
        with patch.object(service_module, 'save_pinch_threshold', side_effect=RuntimeError('Disk unavailable')):
            response = self.client.post('/api/settings', json={'pinch_threshold': 0.15}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.service.settings['pinch_threshold'], original)

    def test_invalid_saved_settings_use_default(self):
        self.service.settings_path.write_text('{broken')
        with self.assertLogs('local_settings', level='WARNING'):
            restarted = TrackingService(settings_path=self.service.settings_path)
        self.assertEqual(restarted.settings['pinch_threshold'], 0.20)

    def test_mjpeg_stream(self):
        response = self.client.get('/stream.mjpg', buffered=False)
        self.assertIn('multipart/x-mixed-replace', response.content_type)
        self.assertIn(b'Content-Type: image/jpeg', next(response.response))
        response.close()


class WorkerTests(unittest.TestCase):
    def run_worker(self, service, during_inference=None, virtual_error=None):
        service.settings['mode'] = 'face'
        frame = np.full((480, 640, 3), 40, dtype=np.uint8)
        camera, model, detector, connection, virtual = [MagicMock() for _ in range(5)]
        camera.read.side_effect = [(True, frame), (False, None)]
        def target(*args):
            args[1][0, 0] = 255  # Preview annotation must not reach Meet.
            if during_inference:
                during_inference(service)
            return TrackingTarget((500, 300), True, 'Tracking face')
        with patch.object(service_module, 'YOLO', return_value=model), \
             patch.object(service_module, 'HandDetector', return_value=detector), \
             patch.object(service_module.cv2, 'VideoCapture', return_value=camera), \
             patch.object(service_module.serial, 'Serial', return_value=connection), \
             patch.object(service.stop_event, 'wait', return_value=False), \
             patch.object(service_module, 'face_target', side_effect=target), \
             patch.object(service, '_open_virtual', side_effect=virtual_error, return_value=virtual), \
             patch.object(service_module.LOGGER, 'exception'):
            service._run()
        return camera, connection, detector, virtual

    def test_tracking_input_is_small_and_preserves_aspect_ratio(self):
        for height, width, expected in ((720, 1280, (360, 640)),
                                        (1080, 1920, (360, 640)),
                                        (480, 640, (480, 640))):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            reduced = service_module.tracking_frame(frame)
            self.assertEqual(reduced.shape[:2], expected)
            self.assertEqual(frame.shape[:2], (height, width))

    def test_stale_inference_cannot_move_after_pause(self):
        service = TrackingService()
        service.settings['tracking'] = True
        resources = self.run_worker(service, lambda worker: worker.configure({'tracking': False}))
        camera, connection, detector, virtual = resources
        self.assertTrue(all(call.args[0] == b'0,0\n' for call in connection.write.call_args_list))
        connection.close.assert_called_once()
        camera.release.assert_called_once()
        detector.close.assert_called_once()

    def test_home_during_inference_discards_stale_movement(self):
        service = TrackingService()
        service.settings['tracking'] = True
        camera, connection, detector, virtual = self.run_worker(service, lambda worker: worker.home())
        commands = [call.args[0] for call in connection.write.call_args_list]
        self.assertIn(b'H\n', commands)
        self.assertTrue(all(command in (b'H\n', b'0,0\n') for command in commands))
        self.assertFalse(service.snapshot()['homing'])

    def test_virtual_output_uses_clean_frames_and_cleans_up(self):
        service = TrackingService(preview_only=True)
        service.settings['virtual'] = True
        camera, connection, detector, virtual = self.run_worker(service)
        self.assertEqual(virtual.send.call_args.args[0][0, 0, 0], 40)
        virtual.close.assert_called_once()
        self.assertFalse(service.snapshot()['virtual_active'])
        self.assertFalse(service.snapshot()['camera_connected'])

    def test_virtual_failure_is_reported(self):
        service = TrackingService(preview_only=True)
        service.settings['virtual'] = True
        self.run_worker(service, virtual_error=RuntimeError('Loopback missing'))
        self.assertEqual(service.snapshot()['virtual_error'], 'Loopback missing')
        self.assertFalse(service.snapshot()['virtual'])
        self.assertGreater(service.sequence, 1)  # Preview continued after output failure.

    def test_static_session_skips_inference_and_serial(self):
        service = TrackingService()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        camera, face, hand = MagicMock(), MagicMock(), MagicMock()
        camera.read.side_effect = [(True, frame), (False, None)]
        with patch.object(service_module, 'YOLO', return_value=face), \
             patch.object(service_module, 'HandDetector', return_value=hand), \
             patch.object(service_module.cv2, 'VideoCapture', return_value=camera), \
             patch.object(service_module.serial, 'Serial') as serial_port, \
             patch.object(service_module.LOGGER, 'exception'):
            service._run()
        face.predict.assert_not_called()
        hand.detect.assert_not_called()
        serial_port.assert_not_called()
        hand.close.assert_called_once()

    def test_partial_initialization_releases_camera(self):
        service = TrackingService()
        camera, detector = MagicMock(), MagicMock()
        camera.isOpened.return_value = False
        with patch.object(service_module, 'YOLO'), \
             patch.object(service_module, 'HandDetector', return_value=detector), \
             patch.object(service_module.cv2, 'VideoCapture', return_value=camera), \
             patch.object(service_module.LOGGER, 'exception'):
            service._run()
        detector.close.assert_called_once()
        camera.release.assert_called_once()
        self.assertEqual(service.snapshot()['phase'], 'error')

    def test_virtual_device_cannot_be_capture_device(self):
        service = TrackingService(camera='2', virtual_device='/dev/video2')
        with self.assertRaisesRegex(RuntimeError, 'different'):
            service._open_virtual(np.zeros((480, 640, 3), dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
