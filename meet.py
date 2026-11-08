import cv2
import time
import pyvirtualcam

# =========================
# CONFIG
# =========================
CAM_INDEX = 0
FRAME_W = 1280
FRAME_H = 720
VIRT_FPS = 30
WINDOW_NAME = "Preview (ESC para salir)"

def main():
    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        raise RuntimeError(f"No pude abrir la cámara en índice {CAM_INDEX}.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)

    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        raise RuntimeError("La cámara abrió pero no entrega frames.")

    # Ajusta al tamaño real que estés usando
    h, w = frame.shape[:2]
    frame_w, frame_h = w, h
    print(f"[OK] Cámara {CAM_INDEX} abierta: {frame_w}x{frame_h}")

    cam = pyvirtualcam.Camera(width=frame_w, height=frame_h, fps=VIRT_FPS, print_fps=False)
    print("[OK] VirtualCam device:", cam.device)

    last_t = time.time()
    fps = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            # FPS (solo para debug)
            now = time.time()
            dt = max(1e-6, now - last_t)
            last_t = now
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else (1.0 / dt)

            # Asegurar tamaño consistente con la virtual cam
            if frame.shape[1] != frame_w or frame.shape[0] != frame_h:
                frame = cv2.resize(frame, (frame_w, frame_h), interpolation=cv2.INTER_AREA)

            vis = frame.copy()
            cv2.putText(vis, f"VirtualCam OK  |  FPS: {fps:5.1f}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(vis, "Abre Google Meet y selecciona esta camara virtual", (20, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

            # Enviar a cámara virtual (pyvirtualcam espera RGB)
            vis_rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
            cam.send(vis_rgb)
            cam.sleep_until_next_frame()

            # Preview local
            cv2.imshow(WINDOW_NAME, vis)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q')):
                break

    finally:
        try:
            cam.close()
        except Exception:
            pass
        cap.release()
        cv2.destroyAllWindows()
        print("[OK] Cerrado.")

if __name__ == "__main__":
    main()