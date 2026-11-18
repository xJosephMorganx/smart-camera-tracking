"""Synthetic checks for gesture gating and the shared serial controller."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import motion_control as tracker
from tracking_targets import TrackingTarget, face_target, hand_target


def result_with_hand(distance=15):
    landmarks = [SimpleNamespace(x=500 / 640, y=300 / 480) for _ in range(21)]
    landmarks[4].x -= distance / 640
    landmarks[5].x = 450 / 640
    landmarks[17].x = 550 / 640
    return SimpleNamespace(
        boxes=SimpleNamespace(xyxy=torch.tensor([[400., 200., 600., 400.]])),
        hand_landmarks=[landmarks],
    )


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def test_hd_face_coordinates_are_mapped_to_preview(self):
        result = result_with_hand()
        result.boxes.xyxy = torch.tensor([[100., 50., 200., 150.]])
        target = face_target(result, np.zeros((720, 1280, 3), dtype=np.uint8), (360, 640, 3))
        self.assertEqual(target.position, (300, 200))

    def test_servo_steps_do_not_depend_on_capture_resolution(self):
        for offset in (0, 8, 40, 100, -60):
            steps = []
            for width, height in ((640, 480), (1280, 720), (1920, 1080)):
                controller = tracker.MotionController(None)
                target = TrackingTarget((width * (0.5 + offset / 640),
                                         height * (0.5 + offset / 480)), True, '')
                steps.append(controller.update(target, (height, width, 3), True))
            self.assertEqual(steps[0], steps[1])
            self.assertEqual(steps[0], steps[2])

    def test_pinch_requires_valid_tips(self):
        self.assertTrue(hand_target(result_with_hand(), self.frame).active)
        self.assertFalse(hand_target(result_with_hand(distance=70), self.frame).active)
        result = result_with_hand()
        result.hand_landmarks[0][4].x = -1
        self.assertFalse(hand_target(result, self.frame).active)

    def test_tighter_pinch_with_release_margin(self):
        self.assertTrue(hand_target(result_with_hand(distance=19), self.frame).active)
        self.assertFalse(hand_target(result_with_hand(distance=24), self.frame).active)
        self.assertTrue(hand_target(result_with_hand(distance=24), self.frame, pinch_active=True).active)
        self.assertFalse(hand_target(result_with_hand(distance=27), self.frame, pinch_active=True).active)
        missing = SimpleNamespace(hand_landmarks=[])
        self.assertFalse(hand_target(missing, self.frame, pinch_active=True).active)

    def test_pinch_is_invariant_to_hand_size(self):
        for scale in (0.4, 1.0, 2.0):
            for distance, expected in ((15, True), (30, False)):
                result = result_with_hand(distance=distance)
                for landmark in result.hand_landmarks[0]:
                    landmark.x = 500 / 640 + (landmark.x - 500 / 640) * scale
                    landmark.y = 300 / 480 + (landmark.y - 300 / 480) * scale
                self.assertEqual(hand_target(result, self.frame).active, expected)

    def test_invalid_palm_reference_stops_pinch(self):
        result = result_with_hand()
        result.hand_landmarks[0][17].x = result.hand_landmarks[0][5].x
        self.assertFalse(hand_target(result, self.frame, pinch_active=True).active)
        result.hand_landmarks[0][5].x = float('nan')
        self.assertFalse(hand_target(result, self.frame, pinch_active=True).active)

    def test_missing_and_invalid_keypoints(self):
        result = result_with_hand()
        result.hand_landmarks[0][8].x = float('nan')
        self.assertFalse(hand_target(result, self.frame).active)
        result.hand_landmarks[0] = result.hand_landmarks[0][:17]
        with self.assertRaises(ValueError):
            hand_target(result, self.frame)
        result.hand_landmarks = []
        self.assertFalse(hand_target(result, self.frame).active)

    def test_largest_face_and_palm_target(self):
        result = result_with_hand()
        result.boxes.xyxy = torch.tensor([[0., 0., 10., 10.], [100., 100., 200., 200.]])
        self.assertEqual(face_target(result, self.frame).position, (150, 150))
        self.assertEqual(hand_target(result_with_hand(), self.frame, target_mode='palm_center').position, (500, 300))

    def test_stale_filter_does_not_move_past_center(self):
        self.assertEqual(tracker.calculate_axis_step(100, -30), 0)
        self.assertEqual(tracker.calculate_axis_step(100, 0), 0)
        self.assertEqual(tracker.PAN_SIGN * tracker.calculate_axis_step(100, 100), 3)
        self.assertEqual(tracker.TILT_SIGN * tracker.calculate_axis_step(100, 100), -3)

    def test_mode_reset_pinch_release_and_shutdown(self):
        connection = MagicMock()
        controller = tracker.MotionController(connection)
        ticks = iter(index * 0.1 for index in range(20))
        with patch.object(tracker.time, 'perf_counter', side_effect=lambda: next(ticks)):
            controller.stop()
            controller.update(face_target(result_with_hand(), self.frame), self.frame.shape, True)
            controller.stop()  # Mode changes discard the previous target history.
            controller.update(hand_target(result_with_hand(), self.frame), self.frame.shape, True)
            controller.update(hand_target(result_with_hand(distance=70), self.frame), self.frame.shape, True)
            controller.stop()
        commands = [call.args[0] for call in connection.write.call_args_list]
        self.assertEqual(commands, [b'0,0\n', b'3,-2\n', b'0,0\n', b'3,-2\n', b'0,0\n', b'0,0\n'])
        connection.readline.assert_not_called()


if __name__ == '__main__':
    unittest.main()
