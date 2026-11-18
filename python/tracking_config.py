"""Camera, model and motion settings for the Flask application."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / "models" / "yolov8n-face.pt"
HAND_MODEL_PATH = PROJECT_ROOT / "models" / "hand_landmarker.task"

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
TRACKING_MAX_SIZE = 640
PREVIEW_JPEG_QUALITY = 90
BAUD_RATE = 115200
INFERENCE_SIZE = 320

# Values verified on the current pan-tilt assembly.
DEADBAND_PX = 10
PROPORTIONAL_GAIN = 0.035
MAX_STEP_DEG = 3
SEND_INTERVAL_SECONDS = 0.05
SMOOTH_ALPHA = 0.65
PAN_SIGN = +1
TILT_SIGN = -1

# Firmware H approaches pan=80, tilt=100 by 2 degrees within a 0..180 range.
# Fifty commands cover the largest possible distance (100 degrees).
HOME_COMMAND_COUNT = 50
HOME_INTERVAL_SECONDS = 0.05
