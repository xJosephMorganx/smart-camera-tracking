"""MediaPipe hand inference, separate from preview and servo control."""

import time

import cv2
import mediapipe as mp


class HandDetector:
    def __init__(self, model_path):
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self.last_timestamp_ms = -1

    def detect(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # VIDEO timestamps must increase, including after switching F/H modes.
        timestamp_ms = max(self.last_timestamp_ms + 1, time.monotonic_ns() // 1_000_000)
        self.last_timestamp_ms = timestamp_ms
        return self.landmarker.detect_for_video(image, timestamp_ms)

    def close(self):
        self.landmarker.close()
