"""Capture from the Lumina camera, apply effects, push to the Lumina Plus vcam."""

import os
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")  # 1 s open instead of ~40 s
import threading
import time
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

from vcam import LuminaVCam

LUMINA_DIR = r"C:\Program Files\Lumina"
BACKGROUNDS = ["Desert", "Forest", "Lake", "Nebula", "Stars"]

# Camera (UVC) values the original Lumina app pushed to the camera at startup,
# from its default profile (~/.config/Lumina/profileConfig.json). Without them
# the camera runs its own defaults, notably backlight compensation 10, which
# over-brightens the whole frame and washes it out.
LUMINA_TUNING = {
    cv2.CAP_PROP_BACKLIGHT: 4,
    cv2.CAP_PROP_BRIGHTNESS: 10,
    cv2.CAP_PROP_GAIN: 10,
    cv2.CAP_PROP_HUE: 8,
    cv2.CAP_PROP_CONTRAST: 32,
    cv2.CAP_PROP_SATURATION: 64,
    cv2.CAP_PROP_SHARPNESS: 4,
}


@dataclass
class Settings:
    camera: str = "Lumina Camera - Raw"
    capture: str = "1080p"          # 1080p | 4K
    out_w: int = 1920
    out_h: int = 1080
    mirror: bool = False
    background: str = "Off"         # Off | Blur | <name in BACKGROUNDS>
    blur: int = 25
    autoframe: bool = False
    frame_tightness: float = 0.5    # 0 = wide, 1 = tight
    frame_speed: float = 0.5
    zoom: float = 1.0               # manual zoom when autoframe is off
    pan_x: float = 0.0              # -1..1
    pan_y: float = 0.0
    brightness: int = 0             # -50..50
    contrast: float = 1.0           # 0.5..1.5
    saturation: float = 1.0         # 0..2
    warmth: int = 0                 # -30..30
    camera_tuning: bool = True      # push LUMINA_TUNING to the camera when the app starts

    # Tuned for Teams/Zoom/Meet: they send at most 1080p, mirror only your own
    # self-view (so the outgoing feed must not be mirrored), and compress hard,
    # so effects stay subtle and framing moves slowly.
    CALL_PRESET = {
        "capture": "1080p",         # 4K + effects only reaches ~21 fps on this laptop CPU
        "camera_tuning": True,
        "out_w": 1920, "out_h": 1080,
        "mirror": False,
        "autoframe": True, "frame_tightness": 0.4, "frame_speed": 0.25,
        "background": "Blur", "blur": 20,
        "brightness": 0, "contrast": 1.05, "saturation": 1.05, "warmth": 4,
    }

    def apply_call_preset(self):
        for k, v in self.CALL_PRESET.items():
            setattr(self, k, v)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, type(getattr(s, k))(v))
        return s


class LatestWorker:
    """Runs fn on the newest submitted input in its own thread.

    The video loop never waits: it submits a frame and reads whatever result
    is ready, which may be a frame or two old. Older inputs are dropped."""

    def __init__(self, fn, max_rate=30.0):
        self.fn = fn
        self.min_interval = 1.0 / max_rate
        self.result = None
        self._input = None
        self._cv = threading.Condition()
        self._alive = True
        self.fps = 0.0
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, item):
        with self._cv:
            self._input = item
            self._cv.notify()

    def _loop(self):
        t0, n = time.time(), 0
        while self._alive:
            with self._cv:
                while self._input is None and self._alive:
                    self._cv.wait(0.5)
                item, self._input = self._input, None
            if item is None:
                continue
            started = time.time()
            try:
                self.result = self.fn(item)
            except Exception:
                pass
            # Leave CPU for the video loop; masks and face boxes change slowly.
            rest = self.min_interval - (time.time() - started)
            if rest > 0:
                time.sleep(rest)
            n += 1
            if time.time() - t0 >= 1:
                self.fps, t0, n = n / (time.time() - t0), time.time(), 0

    def stop(self):
        self._alive = False
        with self._cv:
            self._cv.notify()


def list_cameras():
    try:
        from pygrabber.dshow_graph import FilterGraph
        return FilterGraph().get_input_devices()
    except Exception:
        return []


class Pipeline:
    def __init__(self, settings: Settings):
        self.s = settings
        self.lock = threading.Lock()
        self.preview = None
        self.status = "Starting"
        self.fps = 0.0
        self.in_use = False
        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._want_settings = threading.Event()
        self._face_worker = LatestWorker(self._detect_face, max_rate=10)
        self._seg_worker = LatestWorker(self._infer_mask, max_rate=15)
        self._thread = None
        self._seg = None
        self._face = None
        self._mask = None
        self._crop = None           # smoothed (cx, cy, w) in source pixels
        self._bg_cache = {}
        self._tuned = False         # tuning applied once per run, so changes made in
                                    # the hardware settings dialog are kept
        self.vcam = None

    # ----- lifecycle -----
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._face_worker.stop()
        self._seg_worker.stop()

    def reopen_camera(self):
        self._reopen.set()

    def open_hw_settings(self):
        self._want_settings.set()

    # ----- capture -----
    def _indexes(self):
        """DirectShow index and Media Foundation index of the chosen camera.

        DirectShow lists the Plus vcam too; Media Foundation does not, so the MF
        index is the position among the real cameras."""
        cams = list_cameras()
        if self.s.camera not in cams:
            lum = [n for n in cams if "Lumina" in n and "Plus" not in n]
            self.s.camera = lum[0] if lum else (cams[0] if cams else self.s.camera)
        ds = cams.index(self.s.camera) if self.s.camera in cams else 0
        real = [n for n in cams if n != "Lumina Camera - Plus"]
        mf = real.index(self.s.camera) if self.s.camera in real else 0
        return ds, mf

    def _show_settings_dialog(self):
        ds, _ = self._indexes()
        cap = cv2.VideoCapture(ds, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_SETTINGS, 1)  # modal; UVC values persist on the camera
        cap.release()

    def _open(self):
        ds, mf = self._indexes()
        cap = cv2.VideoCapture(mf, cv2.CAP_MSMF)
        if not cap.isOpened():
            cap = cv2.VideoCapture(ds, cv2.CAP_DSHOW)
        w, h = (3840, 2160) if self.s.capture == "4K" else (1920, 1080)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened() and self.s.camera_tuning and not self._tuned:
            # Reading these back through MSMF returns stale values, but the
            # writes do reach the camera.
            for prop, val in LUMINA_TUNING.items():
                cap.set(prop, val)
            self._tuned = True
        return cap

    def retune_camera(self):
        self._tuned = False
        self._reopen.set()

    def _run(self):
        try:
            self.vcam = LuminaVCam()
        except OSError as e:
            self.status = f"Virtual camera unavailable: {e}"
            self.vcam = None
        cap = None
        t_last, frames = time.time(), 0
        while not self._stop.is_set():
            if self._want_settings.is_set():
                self._want_settings.clear()
                if cap is not None:
                    cap.release()
                    cap = None
                self.status = "Camera settings open"
                self._show_settings_dialog()
            if cap is None or self._reopen.is_set():
                self._reopen.clear()
                if cap is not None:
                    cap.release()
                self.status = "Opening camera"
                cap = self._open()
                self._cap = cap
                self._crop = None
                if not cap.isOpened():
                    self.status = "Camera not found. Is it plugged in, or in use by another app?"
                    cap = None
                    time.sleep(2)
                    continue
            ok, frame = cap.read()
            if not ok or frame is None:
                self.status = "No frames from camera, retrying"
                cap.release()
                cap = None
                time.sleep(1)
                continue
            try:
                out = self.process(frame)
            except Exception as e:  # keep the camera alive if an effect fails
                self.status = f"Effect error: {e}"
                out = cv2.resize(frame, (self.s.out_w, self.s.out_h))
            if self.vcam:
                self.vcam.send(out)
                self.in_use = self.vcam.consumer_active()
            prev = cv2.resize(out, (960, 540), interpolation=cv2.INTER_AREA)
            with self.lock:
                self.preview = prev
            frames += 1
            if time.time() - t_last >= 1:
                self.fps = frames / (time.time() - t_last)
                t_last, frames = time.time(), 0
                self.status = f"Live  {frame.shape[1]}x{frame.shape[0]} in, {self.s.out_w}x{self.s.out_h} out"
                if self.s.background != "Off":
                    self.status += f"  mask {self._seg_worker.fps:.0f}/s"
                if self.s.autoframe:
                    self.status += f"  face {self._face_worker.fps:.0f}/s"
        if cap is not None:
            cap.release()
        if self.vcam:
            self.vcam.close()

    # ----- effects -----
    def process(self, frame):
        s = self.s
        if s.mirror:
            frame = cv2.flip(frame, 1)
        frame = self._frame_crop(frame)
        frame = cv2.resize(frame, (s.out_w, s.out_h), interpolation=cv2.INTER_AREA)
        if s.background != "Off":
            frame = self._background(frame)
        frame = self._color(frame)
        return frame

    # Runs on the face worker thread. Input: small RGB frame. Output: relative box or False.
    def _detect_face(self, rgb):
        if self._face is None:
            import mediapipe as mp
            self._face = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
        res = self._face.process(rgb)
        if not res.detections:
            return False
        boxes = [d.location_data.relative_bounding_box for d in res.detections]
        x0 = min(b.xmin for b in boxes); y0 = min(b.ymin for b in boxes)
        x1 = max(b.xmin + b.width for b in boxes); y1 = max(b.ymin + b.height for b in boxes)
        return x0, y0, x1, y1

    def _face_box(self, frame):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (640, int(640 * h / w)), interpolation=cv2.INTER_NEAREST)
        self._face_worker.submit(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        r = self._face_worker.result
        if not r:
            return None
        return r[0] * w, r[1] * h, r[2] * w, r[3] * h

    def _frame_crop(self, frame):
        s = self.s
        h, w = frame.shape[:2]
        aspect = s.out_w / s.out_h
        full_w = min(w, h * aspect)
        if s.autoframe:
            box = self._face_box(frame)
            if box:
                x0, y0, x1, y1 = box
                fh = y1 - y0
                # face height as a share of the frame: 0.18 wide .. 0.45 tight
                share = 0.18 + 0.27 * s.frame_tightness
                tw = max(min(fh / share * aspect, full_w), (x1 - x0) * 1.6)
                tw = min(tw, full_w)
                target = ((x0 + x1) / 2, (y0 + y1) / 2 + fh * 0.35, tw)
            else:
                target = (w / 2, h / 2, full_w)
            if self._crop is None:
                self._crop = target
            else:
                a = 0.02 + 0.13 * s.frame_speed
                self._crop = tuple(c + (t - c) * a for c, t in zip(self._crop, target))
            cx, cy, cw = self._crop
        else:
            cw = full_w / max(1.0, s.zoom)
            cx = w / 2 + s.pan_x * (w - cw) / 2
            cy = h / 2 + s.pan_y * (h - cw / aspect) / 2
        ch = cw / aspect
        x = int(np.clip(cx - cw / 2, 0, w - cw))
        y = int(np.clip(cy - ch / 2, 0, h - ch))
        return frame[y:y + int(ch), x:x + int(cw)]

    # Runs on the segmentation worker thread. Input: 640x360 RGB. Output: soft mask, 360x640 float32.
    def _infer_mask(self, rgb):
        if self._seg is None:
            import mediapipe as mp
            self._seg = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
        m = self._seg.process(rgb).segmentation_mask.astype(np.float32)
        if self._mask is None or self._mask.shape != m.shape:
            self._mask = m
        else:
            self._mask = self._mask * 0.4 + m * 0.6
        m = cv2.GaussianBlur(self._mask, (0, 0), 2)
        return np.clip((m - 0.3) / 0.4, 0, 1).astype(np.float32)

    def _segment(self, frame):
        small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
        self._seg_worker.submit(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        m = self._seg_worker.result
        if m is None:
            return None
        return cv2.resize(m, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)

    def _background(self, frame):
        s = self.s
        mask = self._segment(frame)
        if mask is None:  # first mask not ready yet
            return frame
        h, w = frame.shape[:2]
        if s.background == "Blur":
            k = max(1, int(s.blur))
            small = cv2.resize(frame, (w // 4, h // 4))
            small = cv2.GaussianBlur(small, (0, 0), k / 4)
            bg = cv2.resize(small, (w, h))
        else:
            key = (s.background, w, h)
            bg = self._bg_cache.get(key)
            if bg is None:
                img = cv2.imread(os.path.join(LUMINA_DIR, s.background + ".jpg"))
                if img is None:
                    return frame
                ih, iw = img.shape[:2]
                sc = max(w / iw, h / ih)
                img = cv2.resize(img, (int(iw * sc) + 1, int(ih * sc) + 1))
                y0 = (img.shape[0] - h) // 2; x0 = (img.shape[1] - w) // 2
                bg = img[y0:y0 + h, x0:x0 + w]
                self._bg_cache = {key: bg}
        return cv2.blendLinear(frame, bg, mask, 1.0 - mask)

    def _color_lut(self):
        """One 256x1x3 lookup table for brightness, contrast and warmth."""
        s = self.s
        key = (s.brightness, s.contrast, s.warmth)
        if getattr(self, "_lut_key", None) != key:
            x = np.arange(256, dtype=np.float32)
            base = (x - 128) * s.contrast + 128 + s.brightness
            b, g, r = base - s.warmth, base, base + s.warmth
            self._lut = np.clip(np.stack([b, g, r], axis=-1), 0, 255).astype(np.uint8).reshape(256, 1, 3)
            self._lut_key = key
        return self._lut

    def _color(self, frame):
        s = self.s
        if s.brightness or s.contrast != 1.0 or s.warmth:
            frame = cv2.LUT(frame, self._color_lut())
        if s.saturation != 1.0:
            # Push colours away from (or toward) their grey value.
            gray = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            frame = cv2.addWeighted(frame, s.saturation, gray, 1.0 - s.saturation, 0)
        return frame
