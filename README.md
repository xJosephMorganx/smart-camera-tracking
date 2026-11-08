# Smart Camera Tracking

A camera-based pan-tilt tracking system using Python, OpenCV, YOLO, and an
Arduino Nano, with face tracking and pinch-controlled hand tracking modes.

## Status

Under development. The repository currently contains the original MediaPipe
tracking and virtual-camera scripts. The YOLO-based tracker and Arduino Nano
firmware have not yet been implemented or tested.

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
smart-camera-tracking/
├── .gitignore
├── LICENSE
├── README.md
├── meet.py                       # Original virtual-camera reference
└── pan_tilt_mediapipe_hands.py    # Original tracking reference
```

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

## Setup and usage

Installation and run instructions will be added once the implementation is
available. The reference scripts contain machine-specific camera and serial
settings and require external model assets; they are not a ready-to-run
Arduino Nano implementation.

## Preview

Hardware and tracking screenshots will be added after the first working tests.

<!-- Replace these placeholders with actual project images when available.
![Pan-tilt hardware](docs/images/hardware.jpg)
![Tracking preview](docs/images/tracking-preview.png)
-->

## License

This project is licensed under the GNU General Public License v3.0
(GPL-3.0-only). See [LICENSE](LICENSE) for the full license text.
Third-party libraries, datasets, and model weights retain their own licenses.
