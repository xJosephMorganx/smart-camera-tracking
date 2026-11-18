"""Convert face and hand detections into targets without coupling them to servo control."""

from dataclasses import dataclass
import math

import cv2
import numpy as np

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)
PALM_INDICES = (0, 5, 9, 13, 17)
# Tip separation divided by palm width (index MCP to pinky MCP).
PINCH_ACTIVATION_RATIO = 0.20
PINCH_RELEASE_MARGIN_RATIO = 0.06


@dataclass
class TrackingTarget:
    position: tuple | None
    active: bool
    status: str


def largest_box_index(boxes):
    if len(boxes) == 0:
        return None
    return max(
        range(len(boxes)),
        key=lambda index: (boxes[index][2] - boxes[index][0])
        * (boxes[index][3] - boxes[index][1]),
    )


def draw_box(frame, box):
    x1, y1, x2, y2 = map(int, box)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 1)


def face_target(result, frame, source_shape=None):
    boxes = result.boxes.xyxy.cpu().numpy()
    index = largest_box_index(boxes)
    if index is None:
        return TrackingTarget(None, False, "No face detected")
    box = boxes[index].copy()
    if source_shape is not None:
        # Map inference coordinates onto the full-resolution preview.
        source_height, source_width = source_shape[:2]
        height, width = frame.shape[:2]
        box *= np.array([width / source_width, height / source_height,
                         width / source_width, height / source_height])
    draw_box(frame, box)
    x1, y1, x2, y2 = box
    return TrackingTarget(((x1 + x2) / 2, (y1 + y2) / 2), True, "Tracking face")


def hand_target(result, frame, pinch_threshold=PINCH_ACTIVATION_RATIO,
                target_mode="index_tip", pinch_active=False):
    if not result.hand_landmarks:
        return TrackingTarget(None, False, "No hand detected")
    landmarks = result.hand_landmarks[0]
    if len(landmarks) != 21:
        raise ValueError("Expected 21 MediaPipe hand landmarks.")
    height, width = frame.shape[:2]
    points = np.array([(point.x * width, point.y * height) for point in landmarks])
    # MediaPipe provides normalized coordinates, not per-tip confidence scores.
    valid = (
        np.isfinite(points).all(axis=1)
        & (points[:, 0] >= 0) & (points[:, 0] < width)
        & (points[:, 1] >= 0) & (points[:, 1] < height)
    )
    for first, second in HAND_CONNECTIONS:
        if valid[first] and valid[second]:
            cv2.line(frame, tuple(points[first].astype(int)),
                     tuple(points[second].astype(int)), (0, 255, 0), 1)
    for point, visible in zip(points, valid):
        if visible:
            cv2.circle(frame, tuple(point.astype(int)), 2, (0, 255, 255), -1)

    # These are raw model estimates, not filtered servo target coordinates.
    # Label the two pinch landmarks so occlusion errors are visible in preview.
    for keypoint, label, color in ((4, "Thumb", (255, 0, 255)), (8, "Index", (0, 128, 255))):
        if valid[keypoint]:
            point = tuple(points[keypoint].astype(int))
            cv2.circle(frame, point, 5, color, 2)
            cv2.putText(frame, label, (point[0] + 6, point[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    required = (4, 8, 5, 17) if target_mode == "index_tip" else (4, 8, *PALM_INDICES)
    if not all(valid[keypoint] for keypoint in required):
        return TrackingTarget(None, False, "Hand landmarks uncertain: holding")
    palm_width = math.dist(points[5], points[17])
    if palm_width <= 1e-6:
        return TrackingTarget(None, False, "Palm width unavailable: holding")
    # Pixel coordinates correct for the frame aspect ratio before normalizing.
    # Uniform changes in hand size cancel out in this ratio.
    distance_ratio = math.dist(points[4], points[8]) / palm_width
    # Hysteresis prevents flicker while requiring a tighter initial pinch.
    threshold = pinch_threshold + (PINCH_RELEASE_MARGIN_RATIO if pinch_active else 0)
    pinching = distance_ratio <= threshold
    position = points[8] if target_mode == "index_tip" else points[list(PALM_INDICES)].mean(axis=0)
    status = f"Pinch {'ON' if pinching else 'OFF'} ({distance_ratio:.0%} of palm)"
    return TrackingTarget(tuple(position), pinching, status)
