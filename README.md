# Smart Camera Tracking

An educational rebuild of a camera-based pan-tilt tracker using Python,
OpenCV, YOLO, and an Arduino Nano. Development proceeds one learning activity
at a time, with the learner writing the implementation.

## Status

Planning and reference review only. The new tracker and Nano firmware have
not been implemented or tested. The original scripts are preserved unchanged;
their existing comments and messages include Spanish.

## Planned behavior

- **Face tracking:** follow the center of a selected face and stop updating
  the servo targets when the face is lost.
- **Hand tracking:** follow the palm center only while a thumb–index pinch
  is held; retain the last servo targets when tracking is inactive.
- Share motion control and serial communication between both modes.
- Start with an OpenCV window; add virtual-camera output later.

## Hardware

| Component | Planned configuration |
| --- | --- |
| Controller | Arduino Nano |
| Pan servo signal | D9 |
| Tilt servo signal | D10 |
| Serial connection | 115200 baud |
| Video input | USB camera |
| Connectivity | USB hub with a single USB-C connection to the computer |
| Servo power | USB; stability and current capacity under load still need verification |

## Current project structure

```text
Pan-tilt/
├── .gitignore
├── README.md
├── meet.py                       # Original virtual-camera reference
└── pan_tilt_mediapipe_hands.py    # Original tracking reference
```

Add `src/` when the first Python exercises need a home and
`firmware/pan_tilt/pan_tilt.ino` when firmware development begins.
Split detection, gesture logic, motion control, and serial communication
into modules progressively rather than creating empty scaffolding now.

## Reference review

The original tracker uses MediaPipe Hand Landmarker, OpenCV, PySerial, and
PyVirtualCam. It measures thumb–index distance with landmarks 4 and 8 and
already provides a palm-center calculation using landmarks 0, 5, 9, 13, and 17.
However, `TARGET_MODE` currently selects the index fingertip.

Reusable concepts include image-center error, deadband, proportional gain,
bounded angular increments, axis signs, target smoothing, and timed serial
transmission. The reference sends `dx,dy\n` every 50 ms at most, including
`0,0\n` when tracking is inactive. Its intended interpretation is relative
motion; the missing firmware prevents verification of receiver behavior.
The new protocol is still to be designed.

Review points for later exercises:

- Integer truncation with gain 0.035 makes errors below about 29 pixels
  produce no motion, despite the explicit 10-pixel deadband.
- A fixed 35-pixel pinch threshold depends on hand distance and image scale.
- The optional One Euro filters are not reset when the hand disappears.
- Resource initialization occurs before the cleanup block, so some startup
  failures can leave previously opened resources without explicit cleanup.
- `meet.py` derives the virtual-camera size from the actual captured frame;
  the tracker instead uses the requested size.

## Model candidates

Research date: 2026-11-08. No weights have been downloaded or evaluated locally.

- Face: [akanametov/yolo-face](https://github.com/akanametov/yolo-face)
  publishes face-specific weights, including `yolov8n-face.pt`.
- Hand: [poptoz/yolo26-hand-pose-face-detection](https://huggingface.co/poptoz/yolo26-hand-pose-face-detection)
  describes a custom YOLO26 hand model with 21 landmarks and ONNX exports.
  Treat it as a community candidate requiring compatibility and pinch tests.
- Training alternative: [Ultralytics Hand Keypoints](https://docs.ultralytics.com/datasets/pose/hand-keypoints/)
  provides a 21-landmark dataset and a custom-training example.
- Specialized fallback: MediaPipe Hand Landmarker, already used by the
  reference, if a suitable YOLO hand model does not meet the project's needs.

Generic COCO pose weights target body landmarks, not the 21 hand landmarks.
Model choice must be validated before implementing gesture recognition.

## Learning sequence

1. Prepare the Python and Arduino environments.
2. Capture and inspect camera frames.
3. Detect faces and track a visual target without servo motion.
4. Estimate hand landmarks and recognize pinch.
5. Build Nano firmware incrementally and define the shared serial protocol.
6. Test serial communication, then implement bounded pan-tilt control.
7. Integrate modes, calibrate, and verify behavior under load.
8. Add virtual-camera output and final documentation.

Each activity includes an explanation, learner-written code, a focused test,
and a comprehension question before proceeding.

## Setup and usage

Environment versions, installation commands, and run instructions will be
documented during the first learning activity. Do not treat the reference
scripts as a ready-to-run Nano implementation: they contain machine-specific
camera and serial settings and require external model assets.

## Preview

Hardware and tracking screenshots will be added after the first working tests.

<!-- Replace these placeholders with actual project images when available.
![Pan-tilt hardware](docs/images/hardware.jpg)
![Tracking preview](docs/images/tracking-preview.png)
-->

## Development log

- **2026-11-08:** Preserved both original scripts, reviewed the tracking flow,
  researched initial model candidates, and initialized local version control.
  Public repository: [smart-camera-tracking](https://github.com/xJosephMorganx/smart-camera-tracking).

## License

This project is licensed under the GNU General Public License v3.0
(GPL-3.0-only). See [LICENSE](LICENSE) for the full license text.
Third-party libraries, datasets, and model weights retain their own licenses.
