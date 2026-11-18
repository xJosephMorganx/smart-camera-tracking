"""One hardware-owning worker shared by all browser clients."""
import logging
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import serial
from ultralytics import YOLO

from tracking_config import MODEL_PATH, HAND_MODEL_PATH, BAUD_RATE, FRAME_WIDTH, FRAME_HEIGHT, INFERENCE_SIZE
from hand_detector import HandDetector
from camera_devices import list_cameras
from motion_control import MotionController
from tracking_config import HOME_COMMAND_COUNT, HOME_INTERVAL_SECONDS, TRACKING_MAX_SIZE, PREVIEW_JPEG_QUALITY
from local_settings import SETTINGS_PATH, load_pinch_threshold, save_pinch_threshold, valid_pinch_threshold
from tracking_targets import PINCH_RELEASE_MARGIN_RATIO, TrackingTarget, face_target, hand_target

LOGGER = logging.getLogger(__name__)


def placeholder(message):
    frame = np.full((480, 640, 3), (24, 23, 20), dtype=np.uint8)
    cv2.putText(frame, message, (35, 245), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (185, 190, 185), 1)
    return cv2.imencode('.jpg', frame)[1].tobytes()


def tracking_frame(frame):
    """Keep inference input small without cropping or stretching the image."""
    height, width = frame.shape[:2]
    scale = min(1.0, TRACKING_MAX_SIZE / max(height, width))
    if scale == 1.0:
        return frame
    return cv2.resize(frame, (round(width * scale), round(height * scale)),
                      interpolation=cv2.INTER_AREA)


class TrackingService:
    def __init__(self, camera='2', port='/dev/ttyUSB0', virtual_device='/dev/video10', preview_only=False, settings_path=None):
        self.camera_source = int(camera) if str(camera).isdecimal() else str(camera)
        self.port = port
        self.virtual_device = virtual_device
        self.preview_only = preview_only
        self.settings_path = Path(settings_path) if settings_path is not None else SETTINGS_PATH
        self.condition = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.thread = None
        self.sequence = 0
        self.jpeg = placeholder('Start a camera session')
        self.settings = dict(mode='static', tracking=False, virtual=False, preview=True, pinch_threshold=load_pinch_threshold(self.settings_path))
        self.revision = 0
        self.home_remaining = 0
        self.last_home_time = 0.0
        self.state = dict(
            homing=False, phase='idle', status='Ready to connect', error=None, virtual_error=None,
            fps=0.0, inference_ms=0.0, virtual_active=False, serial_connected=False,
            camera_connected=False, active_mode=None, tracking_active=False,
            frame_width=FRAME_WIDTH, frame_height=FRAME_HEIGHT, active_camera=None,
        )

    def snapshot(self):
        with self.condition:
            return dict(self.state, **self.settings, preview_only=self.preview_only,
                        camera=str(self.camera_source), port=self.port,
                        virtual_device=self.virtual_device, pinch_release_margin=PINCH_RELEASE_MARGIN_RATIO)

    def _publish(self, **values):
        with self.condition:
            self.state.update(values)
            self.condition.notify_all()

    def start(self):
        with self.condition:
            if self.thread and self.thread.is_alive():
                if self.stop_event.is_set():
                    raise RuntimeError('The session is still stopping. Try again shortly.')
                return
            self.stop_event.clear()
            self.state.update(phase='starting', error=None, virtual_error=None, status='Loading models and camera')
            self.thread = threading.Thread(target=self._run, name='pan-tilt-camera', daemon=True)
            self.thread.start()

    def stop(self):
        with self.condition:
            self.home_remaining = 0
            self.state['homing'] = False
            self.settings.update(tracking=False, virtual=False)
            self.revision += 1
            self.stop_event.set()
            if self.thread and self.thread.is_alive():
                self.state['phase'] = 'stopping'
            self.condition.notify_all()

    def close(self):
        self.stop()
        thread = self.thread
        if thread:
            thread.join(timeout=10)

    def home(self):
        with self.condition:
            if self.preview_only:
                raise RuntimeError('Home is unavailable in preview-only mode.')
            if self.state['phase'] != 'running':
                raise RuntimeError('Start a camera session first.')
            if self.state['homing']:
                return
            self.settings.update(mode='static', tracking=False)
            self.state.update(homing=True, tracking_active=False, status='Returning home')
            self.home_remaining = HOME_COMMAND_COUNT
            self.last_home_time = 0.0
            self.revision += 1
            self.condition.notify_all()

    def _send_home_tick(self, connection):
        # Called only by the hardware worker while holding the condition lock.
        now = time.perf_counter()
        if self.home_remaining and now - self.last_home_time >= HOME_INTERVAL_SECONDS:
            connection.write(b'H\n')
            self.home_remaining -= 1
            self.last_home_time = now
        elif not self.home_remaining and now - self.last_home_time >= HOME_INTERVAL_SECONDS:
            # The firmware does not acknowledge physical position.
            self.state['homing'] = False

    def configure(self, changes):
        allowed = {'mode', 'tracking', 'virtual', 'preview', 'pinch_threshold'}
        if not isinstance(changes, dict) or not changes or changes.keys() - allowed:
            raise ValueError('Provide mode, tracking, virtual, preview, or pinch_threshold.')
        if 'mode' in changes and changes['mode'] not in ('face', 'hand', 'static'):
            raise ValueError('Mode must be face, hand, or static.')
        for key in ('tracking', 'virtual', 'preview'):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError(f'{key} must be a boolean.')
        if 'pinch_threshold' in changes:
            value = changes['pinch_threshold']
            if not valid_pinch_threshold(value):
                raise ValueError('Pinch threshold must be a ratio from 0.05 to 0.60.')
        with self.condition:
            if self.state['phase'] not in ('starting', 'running'):
                raise RuntimeError('Start a camera session first.')
            if self.state['homing'] and ('mode' in changes or changes.get('tracking')):
                raise RuntimeError('Wait until the Home sequence finishes.')
            if self.preview_only and changes.get('tracking'):
                raise ValueError('Servo movement is disabled in preview-only mode.')
            if changes.get('mode') in ('face', 'hand') and 'tracking' not in changes:
                changes = dict(changes, tracking=not self.preview_only)
            if changes.get('mode', self.settings['mode']) == 'static':
                if changes.get('tracking'):
                    raise ValueError('Static mode does not allow servo tracking.')
                changes = dict(changes, tracking=False)
            if 'pinch_threshold' in changes:
                save_pinch_threshold(self.settings_path, changes['pinch_threshold'])
            self.settings.update(changes)
            if changes.get('preview') is False:
                self.jpeg = placeholder('Preview hidden')
                self.sequence += 1
            self.revision += 1
            if 'virtual' in changes:
                self.state['virtual_error'] = None
            self.condition.notify_all()

    def select_camera(self, source):
        if not isinstance(source, str) or source not in {item['id'] for item in list_cameras()}:
            raise ValueError('Select an available physical camera.')
        if Path(source).resolve() == Path(self.virtual_device).resolve():
            raise ValueError('The virtual output cannot be used as the capture camera.')
        with self.condition:
            if self.state['phase'] in ('starting', 'stopping'):
                raise RuntimeError('Wait until the session finishes starting or stopping.')
            current = f'/dev/video{self.camera_source}' if isinstance(self.camera_source, int) else self.camera_source
            if Path(current).resolve() == Path(source).resolve():
                return
            self.camera_source = source
            self.settings['tracking'] = False
            self.state['tracking_active'] = False
            self.revision += 1
            self.condition.notify_all()

    def _open_camera(self, source):
        camera = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not camera.isOpened():
            camera.release()
            raise RuntimeError(f'Cannot open camera {source}. Close other camera apps or check the device.')
        # HD at 30 FPS is available through MJPEG on the connected cameras.
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        camera.set(cv2.CAP_PROP_FPS, 30)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return camera

    def frames(self):
        last_sequence = -1
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.sequence != last_sequence, timeout=2)
                jpeg, last_sequence = self.jpeg, self.sequence
            # Each viewer gets the latest frame; no unbounded queues or extra inference.
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n'

    def _frame(self, frame):
        with self.condition:
            self.state.update(frame_width=frame.shape[1], frame_height=frame.shape[0])
            if not self.settings['preview']:
                return
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
        if not ok:
            raise RuntimeError('Could not encode the camera preview.')
        with self.condition:
            self.jpeg = encoded.tobytes()
            self.state.update(frame_width=frame.shape[1], frame_height=frame.shape[0])
            self.sequence += 1
            self.condition.notify_all()

    def _open_virtual(self, frame):
        device = Path(self.virtual_device)
        source = Path(f'/dev/video{self.camera_source}') if isinstance(self.camera_source, int) else Path(self.camera_source)
        if source.resolve() == device.resolve():
            raise RuntimeError('The virtual output must be different from the physical camera.')
        if not device.exists():
            raise RuntimeError(f'{device} is missing. Create the PanTilt virtual camera using the setup command below.')
        import pyvirtualcam
        height, width = frame.shape[:2]
        return pyvirtualcam.Camera(width=width, height=height, fps=30,
                                   device=str(device), backend='v4l2loopback',
                                   fmt=pyvirtualcam.PixelFormat.BGR)

    def _run(self):
        camera = connection = detector = virtual = controller = None
        failed = False
        try:
            for path in (MODEL_PATH, HAND_MODEL_PATH):
                if not path.is_file():
                    raise FileNotFoundError(f'Model not found: {path}')
            face_model = YOLO(MODEL_PATH)
            detector = HandDetector(HAND_MODEL_PATH)
            if self.stop_event.is_set():
                return
            active_source = self.camera_source
            camera = self._open_camera(active_source)
            self._publish(camera_connected=True, active_camera=str(active_source))
            # Static preview must not reset the Arduino by opening its serial port.
            # Connect only after the user explicitly enables servo tracking.
            controller = MotionController(None)
            controller.stop()
            last_control = None
            pinch_active = False
            frame_count = 0
            fps_start = time.perf_counter()
            self._publish(phase='running', status='Motion paused')
            while not self.stop_event.is_set():
                with self.condition:
                    settings = self.settings.copy()
                    revision = self.revision
                    requested_source = self.camera_source
                if requested_source != active_source:
                    controller.stop()
                    pinch_active = False
                    last_control = None
                    if virtual is not None:
                        virtual.close()
                        virtual = None
                    camera.release()
                    self._publish(camera_connected=False, virtual_active=False, status='Switching camera')
                    camera = self._open_camera(requested_source)
                    active_source = requested_source
                    self._publish(camera_connected=True, active_camera=str(active_source))
                if (settings['tracking'] or self.state['homing']) and not self.preview_only and connection is None:
                    self._publish(status='Connecting Arduino')
                    connection = serial.Serial(self.port, BAUD_RATE, timeout=0, write_timeout=0.5)
                    controller = MotionController(connection)
                    if self.stop_event.wait(2):
                        break
                    connection.reset_input_buffer()
                    self._publish(serial_connected=True)
                    controller.stop()
                    with self.condition:
                        if revision != self.revision:
                            continue
                with self.condition:
                    if self.state['homing'] and connection is not None and not self.stop_event.is_set():
                        self._send_home_tick(connection)
                control = (settings['mode'], settings['tracking'], settings['pinch_threshold'])
                if control != last_control:
                    controller.stop()
                    last_control = control
                    pinch_active = False
                if not settings['virtual'] and virtual is not None:
                    virtual.close()
                    virtual = None
                    self._publish(virtual_active=False)
                ok, raw = camera.read()
                if not ok or raw is None:
                    raise RuntimeError('Camera stopped delivering frames.')
                preview = raw.copy()
                started = time.perf_counter()
                detection_frame = tracking_frame(raw) if settings['mode'] != 'static' else None
                if settings['mode'] == 'face':
                    result = face_model.predict(detection_frame, imgsz=INFERENCE_SIZE, conf=0.5, verbose=False)[0]
                    target = face_target(result, preview, detection_frame.shape)
                elif settings['mode'] == 'hand':
                    target = hand_target(detector.detect(detection_frame), preview,
                                         pinch_threshold=settings['pinch_threshold'],
                                         pinch_active=pinch_active)
                    pinch_active = target.active
                else:
                    target = TrackingTarget(None, False, 'Static camera · no movement')
                inference_ms = 0.0 if settings['mode'] == 'static' else (time.perf_counter() - started) * 1000
                with self.condition:
                    # Ignore inference completed after a mode/pause/stop request.
                    if self.stop_event.is_set() or revision != self.revision:
                        controller.stop()
                        continue
                    steps = controller.update(target, raw.shape, settings['tracking'])
                if settings['virtual']:
                    try:
                        if virtual is None:
                            virtual = self._open_virtual(raw)
                        virtual.send(raw)
                        self._publish(virtual_active=True, virtual_error=None)
                    except Exception as error:
                        if virtual is not None:
                            try:
                                virtual.close()
                            except Exception:
                                LOGGER.exception('Could not close virtual camera')
                            virtual = None
                        with self.condition:
                            self.settings['virtual'] = False
                            self.state.update(virtual_active=False, virtual_error=str(error))
                height, width = preview.shape[:2]
                if settings['mode'] != 'static':
                    cv2.drawMarker(preview, (width // 2, height // 2), (255, 255, 255),
                                   cv2.MARKER_CROSS, 16, 1)
                if controller.filtered:
                    cv2.circle(preview, tuple(map(int, controller.filtered)), 5, (0, 0, 255), 1)
                self._frame(preview)
                frame_count += 1
                elapsed = time.perf_counter() - fps_start
                if elapsed >= 0.5:
                    self._publish(fps=round(frame_count / elapsed, 1))
                    fps_start, frame_count = time.perf_counter(), 0
                self._publish(status='Returning home' if self.state['homing'] else target.status, inference_ms=round(inference_ms, 1),
                              active_mode=settings['mode'],
                              tracking_active=bool(settings['tracking'] and target.active),
                              steps=list(steps))
        except Exception as error:
            failed = True
            LOGGER.exception('Camera session failed')
            self._publish(error=str(error))
        finally:
            # One worker owns cleanup, including partial initialization failures.
            for resource, action in ((controller, 'stop'), (virtual, 'close'),
                                     (connection, 'close'), (detector, 'close'), (camera, 'release')):
                if resource is not None:
                    try:
                        getattr(resource, action)()
                    except Exception:
                        LOGGER.exception('Resource cleanup failed: %s', action)
            with self.condition:
                self.home_remaining = 0
                self.state['homing'] = False
                self.settings.update(tracking=False, virtual=False)
                self.state.update(phase='error' if failed else 'idle',
                                  status='Session failed' if failed else 'Session stopped',
                                  camera_connected=False, serial_connected=False,
                                  virtual_active=False, tracking_active=False, active_mode=None, active_camera=None,
                                  fps=0.0, inference_ms=0.0)
                self.jpeg = placeholder('Session failed' if failed else 'Camera session stopped')
                self.sequence += 1
                self.condition.notify_all()
