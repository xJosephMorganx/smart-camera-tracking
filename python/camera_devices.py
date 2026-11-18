"""Enumerate Linux capture devices without starting a video stream."""
import fcntl
import os
from pathlib import Path
import struct

# linux/videodev2.h: VIDIOC_QUERYCAP and struct v4l2_capability (104 bytes).
QUERY_CAPABILITIES = 0x80685600
VIDEO_CAPTURE = 0x00000001
VIDEO_CAPTURE_MPLANE = 0x00001000
DEVICE_CAPABILITIES = 0x80000000


def list_cameras():
    cameras = []
    for path in sorted(Path('/dev').glob('video[0-9]*'), key=lambda item: int(item.name[5:])):
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            buffer = bytearray(104)
            fcntl.ioctl(descriptor, QUERY_CAPABILITIES, buffer)
            driver, name, bus, version, capabilities, device_caps, *reserved = struct.unpack('16s32s32sIII3I', buffer)
            if b'v4l2 loopback' in driver or b'v4l2loopback' in driver:
                continue
            flags = device_caps if capabilities & DEVICE_CAPABILITIES else capabilities
            if not flags & (VIDEO_CAPTURE | VIDEO_CAPTURE_MPLANE):
                continue
            label = name.split(b'\0')[0].decode('utf-8', errors='replace')
            cameras.append({'id': str(path), 'label': f'{label} · {path.name}'})
        except OSError:
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return cameras
