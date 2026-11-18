"""Shared motion policy for the local camera interface."""
import time

from tracking_config import (
    PAN_SIGN, TILT_SIGN, SMOOTH_ALPHA, SEND_INTERVAL_SECONDS,
    DEADBAND_PX, PROPORTIONAL_GAIN, MAX_STEP_DEG,
)


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def calculate_step(error_px):
    if abs(error_px) <= DEADBAND_PX:
        return 0

    # Match the original controller: truncate before limiting the step.
    step = int(PROPORTIONAL_GAIN * error_px)
    return clamp(step, -MAX_STEP_DEG, MAX_STEP_DEG)


def calculate_axis_step(filtered_error, measured_error):
    # Do not keep moving on stale filter history near or across the center.
    if abs(measured_error) <= DEADBAND_PX:
        return 0
    if filtered_error * measured_error <= 0:
        return 0
    return calculate_step(filtered_error)


def send_steps(connection, pan_step, tilt_step):
    # The current firmware accepts relative corrections without acknowledgments.
    if connection is not None:
        connection.write(f"{pan_step},{tilt_step}\n".encode("ascii"))




class MotionController:
    def __init__(self, connection):
        self.connection = connection
        self.filtered = None
        self.active = False
        self.last_steps = (0, 0)
        self.last_send_time = 0.0

    def stop(self):
        send_steps(self.connection, 0, 0)
        self.filtered = None
        self.active = False
        self.last_steps = (0, 0)
        self.last_send_time = time.perf_counter()

    def update(self, target, frame_shape, enabled):
        active = enabled and target.active
        steps = (0, 0)
        if active:
            x, y = target.position
            if self.filtered is None:
                self.filtered = (x, y)
            else:
                self.filtered = tuple(
                    (1 - SMOOTH_ALPHA) * old + SMOOTH_ALPHA * new
                    for old, new in zip(self.filtered, (x, y))
                )
            height, width = frame_shape[:2]
            # Gains and deadband were tuned at 640x480. Normalize errors so
            # increasing capture resolution does not amplify servo corrections.
            scale_x, scale_y = 640 / width, 480 / height
            steps = (
                PAN_SIGN * calculate_axis_step((self.filtered[0] - width / 2) * scale_x, (x - width / 2) * scale_x),
                TILT_SIGN * calculate_axis_step((self.filtered[1] - height / 2) * scale_y, (y - height / 2) * scale_y),
            )
        else:
            self.filtered = None

        now = time.perf_counter()
        interval_elapsed = now - self.last_send_time >= SEND_INTERVAL_SECONDS
        stopped_axis = any(old != 0 and new == 0 for old, new in zip(self.last_steps, steps))
        if (self.active and not active) or stopped_axis or interval_elapsed:
            # An early stop must not repeat movement on the other axis.
            sent = steps if interval_elapsed else (0, 0)
            send_steps(self.connection, *sent)
            self.last_steps = sent
            self.last_send_time = now
        self.active = active
        return steps
