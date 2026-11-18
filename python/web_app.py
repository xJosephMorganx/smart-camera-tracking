"""Local Flask UI. Run one process so only one worker owns the hardware."""
import argparse
import secrets
import signal
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request

from tracking_service import TrackingService
from camera_devices import list_cameras


def create_app(service):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=4096, TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]'])
    control_token = secrets.token_urlsafe(32)

    @app.before_request
    def protect_local_controls():
        # Do not let another website send commands to the local hardware server.
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error='Cross-origin control requests are not allowed.'), 403
            if not secrets.compare_digest(request.headers.get('X-Control-Token', ''), control_token):
                return jsonify(error='Reload the page before changing controls.'), 403

    @app.after_request
    def local_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.get('/')
    def index():
        return render_template('index.html', control_token=control_token)

    @app.get('/api/status')
    def status():
        return jsonify(service.snapshot())

    @app.get('/api/cameras')
    def cameras():
        return jsonify(cameras=list_cameras())

    @app.post('/api/camera')
    def camera():
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict) or set(data) != {'camera'}:
                raise ValueError('Provide a camera device.')
            service.select_camera(data['camera'])
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 409
        return jsonify(service.snapshot())

    @app.post('/api/session/<action>')
    def session(action):
        try:
            if action == 'start':
                service.start()
            elif action == 'stop':
                service.stop()
            else:
                return jsonify(error='Unknown session action.'), 404
        except RuntimeError as error:
            return jsonify(error=str(error)), 409
        return jsonify(service.snapshot())

    @app.post('/api/home')
    def home():
        try:
            service.home()
        except RuntimeError as error:
            return jsonify(error=str(error)), 409
        return jsonify(service.snapshot())

    @app.post('/api/settings')
    def settings():
        try:
            service.configure(request.get_json(silent=True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 409
        return jsonify(service.snapshot())

    @app.get('/stream.mjpg')
    def stream():
        return Response(service.frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    return app


def main():
    parser = argparse.ArgumentParser(description='Local PanTilt camera dashboard')
    parser.add_argument('--camera', default='2', help='Physical camera index or /dev/v4l/by-id/...')
    parser.add_argument('--serial-port', default='/dev/ttyUSB0')
    parser.add_argument('--virtual-device', default='/dev/video10')
    parser.add_argument('--port', type=int, default=5000, help='Local HTTP port')
    parser.add_argument('--preview-only', action='store_true', help='Disable serial access and servo motion')
    args = parser.parse_args()
    service = TrackingService(args.camera, args.serial_port, args.virtual_device, args.preview_only)
    app = create_app(service)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        # No reloader: it would create duplicate hardware-owning processes.
        app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        service.close()


if __name__ == '__main__':
    main()
