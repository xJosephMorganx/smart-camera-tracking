import time
import math
import cv2
import serial
import mediapipe as mp
import pyvirtualcam

# =========================
# CONFIG
# =========================
COM_PORT = "COM11"
BAUD = 115200

CAM_INDEX = 1
FRAME_W = 640
FRAME_H = 480

MODEL_PATH = "models/hand_landmarker.task"

DEADBAND_PX = 10
GAIN = 0.035
MAX_STEP = 2
SEND_EVERY_MS = 50

# Suavizado del punto objetivo
USE_ONE_EURO = False
SMOOTH_ALPHA = 0.2

# One Euro Filter (si USE_ONE_EURO=True)
ONE_EURO_MIN_CUTOFF = 1.2
ONE_EURO_BETA = 0.02
ONE_EURO_D_CUTOFF = 1.0

PAN_SIGN = +1
TILT_SIGN = -1

# Target preferido: "index_tip" (8) o "palm_center"
TARGET_MODE = "index_tip"  # "index_tip" | "palm_center"

# Pinch = thumb tip (4) cerca de index tip (8)
PINCH_THRESH_PX = 35

# Anti-flicker opcional del pinch (hold estable):
# Si tu pinch a veces parpadea (ON/OFF) por 1-2 frames,
# esto exige que pinch esté presente N ms para activar tracking.
PINCH_HOLD_MS = 0          # 0 = desactivado (respuesta inmediata). Prueba 80-120 si parpadea.

# MediaPipe Hands Task params
MP_MAX_HANDS = 1
MP_DET_CONF = 0.6
MP_PRESENCE_CONF = 0.6
MP_TRK_CONF = 0.6

WINDOW_NAME = "PanTilt Hand Tracker (Pinch Hold)"

# =========================
# HAND CONNECTIONS (para dibujar líneas)
# (Equivalente a las conexiones típicas de MediaPipe Hands)
# =========================
HAND_CONNECTIONS = [
    # Thumb
    (0, 1), (1, 2), (2, 3), (3, 4),
    # Index
    (0, 5), (5, 6), (6, 7), (7, 8),
    # Middle
    (0, 9), (9, 10), (10, 11), (11, 12),
    # Ring
    (0, 13), (13, 14), (14, 15), (15, 16),
    # Pinky
    (0, 17), (17, 18), (18, 19), (19, 20),
    # Palm connections
    (5, 9), (9, 13), (13, 17), (5, 17)
]

# =========================
# HELPERS
# =========================
def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def compute_step(err_px):
    if abs(err_px) <= DEADBAND_PX:
        return 0
    step = int(GAIN * err_px)
    return clamp(step, -MAX_STEP, MAX_STEP)

def open_camera(index, w=640, h=480):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"No pude abrir la cámara en índice {index}. Prueba 0/1/2.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)

    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        raise RuntimeError(f"Cámara en índice {index} abrió pero no entrega frames.")

    print(f"[OK] Cámara abierta en índice {index} ({frame.shape[1]}x{frame.shape[0]})")
    return cap

def l2(px1, py1, px2, py2):
    dx = px1 - px2
    dy = py1 - py2
    return math.sqrt(dx*dx + dy*dy)

# =========================
# One Euro Filter (opcional)
# =========================
class LowPass:
    def __init__(self, alpha, init_value=None):
        self.alpha = alpha
        self.s = init_value

    def apply(self, x):
        if self.s is None:
            self.s = x
            return x
        self.s = self.alpha * x + (1.0 - self.alpha) * self.s
        return self.s

def alpha_from_cutoff(cutoff, dt):
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)

class OneEuro:
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_f = LowPass(alpha=1.0)
        self.dx_f = LowPass(alpha=1.0)
        self.last_x = None
        self.last_t = None

    def apply(self, x, t):
        if self.last_t is None:
            self.last_t = t
            self.last_x = x
            self.x_f.s = x
            self.dx_f.s = 0.0
            return x

        dt = max(1e-6, t - self.last_t)
        dx = (x - self.last_x) / dt

        a_d = alpha_from_cutoff(self.d_cutoff, dt)
        self.dx_f.alpha = a_d
        dx_hat = self.dx_f.apply(dx)

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = alpha_from_cutoff(cutoff, dt)
        self.x_f.alpha = a
        x_hat = self.x_f.apply(x)

        self.last_t = t
        self.last_x = x_hat
        return x_hat

# =========================
# MediaPipe Tasks helpers
# =========================
def norm_to_px(lm, w, h):
    return int(lm.x * w), int(lm.y * h)

def palm_center_px(hand_lms, w, h):
    idxs = [0, 5, 9, 13, 17]
    xs, ys = [], []
    for i in idxs:
        px, py = norm_to_px(hand_lms[i], w, h)
        xs.append(px)
        ys.append(py)
    return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))

def draw_hand(vis, hand_lms, w, h):
    # Dibujar puntos
    pts = []
    for lm in hand_lms:
        px, py = norm_to_px(lm, w, h)
        pts.append((px, py))
        cv2.circle(vis, (px, py), 2, (0, 255, 0), -1)

    # Dibujar líneas
    for a, b in HAND_CONNECTIONS:
        ax, ay = pts[a]
        bx, by = pts[b]
        cv2.line(vis, (ax, ay), (bx, by), (0, 255, 0), 1)

def main():
    cap = open_camera(CAM_INDEX, FRAME_W, FRAME_H)

    ser = serial.Serial(COM_PORT, BAUD, timeout=0)
    time.sleep(2)
    print(f"[OK] Serial abierto en {COM_PORT} @ {BAUD}")

    BaseOptions = mp.tasks.BaseOptions
    VisionRunningMode = mp.tasks.vision.RunningMode
    HandLandmarker = mp.tasks.vision.HandLandmarker
    HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=MP_MAX_HANDS,
        min_hand_detection_confidence=MP_DET_CONF,
        min_hand_presence_confidence=MP_PRESENCE_CONF,
        min_tracking_confidence=MP_TRK_CONF,
    )
    landmarker = HandLandmarker.create_from_options(options)

    last_send_t = 0.0
    last_frame_t = time.time()
    fps = 0.0

    fx, fy = None, None
    onee_x = OneEuro(ONE_EURO_MIN_CUTOFF, ONE_EURO_BETA, ONE_EURO_D_CUTOFF)
    onee_y = OneEuro(ONE_EURO_MIN_CUTOFF, ONE_EURO_BETA, ONE_EURO_D_CUTOFF)

    pinch_hold_start = None  # para PINCH_HOLD_MS

    virtual_cam = pyvirtualcam.Camera(width=FRAME_W, height=FRAME_H, fps=30, print_fps=False)
    print("[OK] VirtualCam device:", virtual_cam.device)    

    try:
        
        while True:
            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                continue

            now = time.time()
            dt = max(1e-6, now - last_frame_t)
            last_frame_t = now
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else (1.0 / dt)

            h, w = frame_bgr.shape[:2]
            cx_img, cy_img = w // 2, h // 2

            vis = frame_bgr.copy()
            cv2.drawMarker(vis, (cx_img, cy_img), (255, 255, 255),
                           markerType=cv2.MARKER_CROSS, thickness=2)

            # ---------- MediaPipe detect ----------
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            timestamp_ms = int(now * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            hand_detected = result.hand_landmarks is not None and len(result.hand_landmarks) > 0

            dx = 0
            dy = 0
            pinch_now = False
            tracking_active = False
            target_px = None

            # Para texto debug (si no hay mano, no existen aún)
            ix = iy = thx = thy = 0

            if hand_detected:
                hand = result.hand_landmarks[0]  # 21 landmarks

                # Dibujar mano (puntos + líneas)
                draw_hand(vis, hand, w, h)

                # ---------- Target ----------
                if TARGET_MODE == "palm_center":
                    tx, ty = palm_center_px(hand, w, h)
                else:
                    tx, ty = norm_to_px(hand[8], w, h)  # index tip

                # ---------- Pinch ----------
                ix, iy = norm_to_px(hand[8], w, h)
                thx, thy = norm_to_px(hand[4], w, h)
                pinch_dist = l2(ix, iy, thx, thy)
                pinch_now = pinch_dist <= PINCH_THRESH_PX

                # Tracking = SOLO mientras haya pinch (hold-to-track)
                if PINCH_HOLD_MS <= 0:
                    tracking_active = pinch_now
                else:
                    if pinch_now:
                        if pinch_hold_start is None:
                            pinch_hold_start = now
                        tracking_active = (now - pinch_hold_start) * 1000.0 >= PINCH_HOLD_MS
                    else:
                        pinch_hold_start = None
                        tracking_active = False

                target_px = (tx, ty)

                # ---------- Suavizado ----------
                if USE_ONE_EURO:
                    sx = int(onee_x.apply(tx, now))
                    sy = int(onee_y.apply(ty, now))
                else:
                    if fx is None or fy is None:
                        fx, fy = tx, ty
                    else:
                        fx = int((1.0 - SMOOTH_ALPHA) * fx + SMOOTH_ALPHA * tx)
                        fy = int((1.0 - SMOOTH_ALPHA) * fy + SMOOTH_ALPHA * ty)
                    sx, sy = fx, fy

                # ---------- Control ----------
                err_x = sx - cx_img
                err_y = sy - cy_img
                if tracking_active:
                    dx = PAN_SIGN * compute_step(err_x)
                    dy = TILT_SIGN * compute_step(err_y)
                else:
                    dx, dy = 0, 0

                # ---------- Visual target ----------
                cv2.circle(vis, (tx, ty), 6, (0, 0, 255), -1)  # target crudo
                cv2.circle(vis, (sx, sy), 7, (255, 0, 0), 2)   # target suavizado
                cv2.line(vis, (cx_img, cy_img), (sx, sy), (255, 255, 255), 2)

            else:
                fx, fy = None, None
                pinch_hold_start = None

            # ========== ENVÍO SERIAL ==========
            if (now - last_send_t) * 1000.0 >= SEND_EVERY_MS:
                ser.write(f"{dx},{dy}\n".encode("ascii"))
                last_send_t = now

            # ========== OVERLAY DEBUG ==========
            status_hand = "SI" if hand_detected else "NO"
            status_pinch = "SI" if pinch_now else "NO"
            status_track = "ON" if tracking_active else "OFF"

            cv2.putText(vis, f"FPS: {fps:5.1f}", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

            cv2.putText(vis, f"MANO: {status_hand}   PINCH: {status_pinch}   TRACKING(PINCH): {status_track}", (10, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

            if hand_detected and target_px is not None:
                pinch_dist_int = int(l2(ix, iy, thx, thy))
                cv2.putText(vis, f"TARGET(px): {target_px[0]},{target_px[1]}   pinchDist(px): {pinch_dist_int}",
                            (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                cv2.putText(vis, f"dx,dy: {dx},{dy}   deadband:{DEADBAND_PX} gain:{GAIN} max:{MAX_STEP}",
                            (10, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            else:
                cv2.putText(vis, "SIN MANO", (10, 85),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)

            # ========== ENVIAR A CAM VIRTUAL (AL FINAL) ==========
            vis_rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
            virtual_cam.send(vis_rgb)
            virtual_cam.sleep_until_next_frame()

            # (Opcional) también mostrar ventana local
            cv2.imshow(WINDOW_NAME, vis)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break

    finally:
        try:
            ser.write(b"0,0\n")
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        try:
            landmarker.close()
        except Exception:
            pass
        try:
            virtual_cam.close()
        except Exception:
            pass
        cap.release()
        cv2.destroyAllWindows()
        print("[OK] Cerrado.")


if __name__ == "__main__":
    main()