"""Lumina Lite: a replacement control app for the Lumina webcam.

Reads "Lumina Camera - Raw", applies framing, background and colour effects,
and outputs to the "Lumina Camera - Plus" virtual camera installed by the
original Lumina software. Pick "Lumina Camera - Plus" in Teams, Zoom, Meet, etc.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from pipeline import BACKGROUNDS, Pipeline, Settings, list_cameras

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", "."), "LuminaLite")
CONFIG = os.path.join(CONFIG_DIR, "settings.json")
OUTPUTS = {"720p": (1280, 720), "1080p": (1920, 1080), "4K": (3840, 2160)}


def load_settings():
    try:
        with open(CONFIG, encoding="utf-8-sig") as f:  # tolerate a BOM from hand edits
            return Settings.from_dict(json.load(f))
    except Exception:
        return Settings()


def save_settings(s):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)


def stop_original_app():
    """The original app holds the shared frame buffer; it can't run alongside."""
    subprocess.run(["taskkill", "/IM", "LuminaStudioApp.exe", "/F"],
                   capture_output=True, creationflags=0x08000000)


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        root.title("Lumina Lite")
        root.configure(bg="#111")
        root.minsize(980, 560)

        style = ttk.Style()
        style.theme_use("clam")
        for w in ("TFrame", "TLabelframe", "TLabelframe.Label", "TLabel", "TCheckbutton", "TRadiobutton"):
            style.configure(w, background="#1b1b1b", foreground="#e8e8e8")
        style.configure("TButton", padding=6)

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        self.view = tk.Frame(left, bg="black", width=640, height=360)
        self.view.pack(fill="both", expand=True)
        self.view.pack_propagate(False)  # the preview image must not resize its container
        self.canvas = tk.Label(self.view, bg="black")
        self.canvas.place(relx=0.5, rely=0.5, anchor="center")
        self.status = ttk.Label(left, text="Starting")
        self.status.pack(fill="x", pady=(6, 0))
        self.usage = ttk.Label(left, text="")
        self.usage.pack(fill="x")

        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        right.pack(side="right", fill="y")
        self._build_controls(right)

        stop_original_app()
        self.pipe = Pipeline(self.s)
        self.pipe.start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()

    # ----- UI helpers -----
    def _var(self, cls, attr, on_change=None):
        v = cls(value=getattr(self.s, attr))

        def cb(*_):
            try:
                val = v.get()
            except tk.TclError:
                return
            setattr(self.s, attr, type(getattr(self.s, attr))(val))
            if on_change:
                on_change()
        v.trace_add("write", cb)
        self._vars.setdefault(attr, v)
        return v

    def _slider(self, parent, label, attr, lo, hi, res=0.01):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=12).pack(side="left")
        cls = tk.DoubleVar if isinstance(getattr(self.s, attr), float) else tk.IntVar
        v = self._var(cls, attr)
        tk.Scale(row, from_=lo, to=hi, resolution=res, orient="horizontal", variable=v, showvalue=False,
                 length=170, bg="#1b1b1b", fg="#e8e8e8", troughcolor="#333", highlightthickness=0,
                 bd=0).pack(side="left", fill="x", expand=True)
        return v

    def _build_controls(self, p):
        self._vars = {}
        ttk.Button(p, text="Apply video call preset", command=self._call_preset).pack(fill="x", pady=(0, 4))
        cam = ttk.LabelFrame(p, text="Camera", padding=8)
        cam.pack(fill="x", pady=4)
        cams = [c for c in list_cameras() if "Plus" not in c] or [self.s.camera]
        row = ttk.Frame(cam); row.pack(fill="x")
        ttk.Label(row, text="Source", width=12).pack(side="left")
        ttk.Combobox(row, values=cams, state="readonly", width=22,
                     textvariable=self._var(tk.StringVar, "camera", self._reopen)).pack(side="left")
        row = ttk.Frame(cam); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Capture", width=12).pack(side="left")
        cap_v = self._var(tk.StringVar, "capture", self._reopen)
        for c in ("1080p", "4K"):
            ttk.Radiobutton(row, text=c, value=c, variable=cap_v).pack(side="left")
        row = ttk.Frame(cam); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Output", width=12).pack(side="left")
        cur = next((k for k, v in OUTPUTS.items() if v == (self.s.out_w, self.s.out_h)), "1080p")
        self.out_var = tk.StringVar(value=cur)
        self.out_var.trace_add("write", self._set_output)
        ttk.Combobox(row, values=list(OUTPUTS), state="readonly", width=8, textvariable=self.out_var).pack(side="left")
        ttk.Checkbutton(cam, text="Mirror", variable=self._var(tk.BooleanVar, "mirror")).pack(anchor="w")
        ttk.Checkbutton(cam, text="Lumina camera tuning (exposure, colour)",
                        variable=self._var(tk.BooleanVar, "camera_tuning",
                                           lambda: self.s.camera_tuning and self.pipe.retune_camera())).pack(anchor="w")
        ttk.Button(cam, text="Hardware settings (exposure, focus, WB)...",
                   command=lambda: self.pipe.open_hw_settings()).pack(fill="x", pady=(4, 0))

        fr = ttk.LabelFrame(p, text="Framing", padding=8)
        fr.pack(fill="x", pady=4)
        ttk.Checkbutton(fr, text="Auto-frame (follow face)", variable=self._var(tk.BooleanVar, "autoframe")).pack(anchor="w")
        self._slider(fr, "Tightness", "frame_tightness", 0, 1)
        self._slider(fr, "Follow speed", "frame_speed", 0, 1)
        ttk.Label(fr, text="Manual (when auto-frame is off)").pack(anchor="w", pady=(4, 0))
        self._slider(fr, "Zoom", "zoom", 1, 4)
        self._slider(fr, "Pan", "pan_x", -1, 1)
        self._slider(fr, "Tilt", "pan_y", -1, 1)

        bg = ttk.LabelFrame(p, text="Background", padding=8)
        bg.pack(fill="x", pady=4)
        ttk.Combobox(bg, values=["Off", "Blur"] + BACKGROUNDS, state="readonly", width=12,
                     textvariable=self._var(tk.StringVar, "background")).pack(anchor="w")
        self._slider(bg, "Blur amount", "blur", 5, 60, 1)

        col = ttk.LabelFrame(p, text="Colour", padding=8)
        col.pack(fill="x", pady=4)
        self._colour_vars = {
            "brightness": self._slider(col, "Brightness", "brightness", -50, 50, 1),
            "contrast": self._slider(col, "Contrast", "contrast", 0.5, 1.5),
            "saturation": self._slider(col, "Saturation", "saturation", 0, 2),
            "warmth": self._slider(col, "Warmth", "warmth", -30, 30, 1),
        }
        ttk.Button(col, text="Reset colour", command=self._reset_colour).pack(anchor="e", pady=(4, 0))

    def _reopen(self):
        if hasattr(self, "pipe"):
            self.pipe.reopen_camera()

    def _set_output(self, *_):
        self.s.out_w, self.s.out_h = OUTPUTS[self.out_var.get()]

    def _call_preset(self):
        preset = Settings.CALL_PRESET
        for attr, val in preset.items():
            if attr in self._vars:
                self._vars[attr].set(val)
        self.out_var.set(next(k for k, v in OUTPUTS.items() if v == (preset["out_w"], preset["out_h"])))
        save_settings(self.s)

    def _reset_colour(self):
        d = Settings()
        for a in ("brightness", "contrast", "saturation", "warmth"):
            setattr(self.s, a, getattr(d, a))
        for a, v in self._colour_vars.items():
            v.set(getattr(d, a))

    # ----- loop -----
    def _tick(self):
        with self.pipe.lock:
            frame = self.pipe.preview
        if frame is not None:
            vw, vh = max(160, self.view.winfo_width()), max(90, self.view.winfo_height())
            w, h = vw, int(vw * 9 / 16)
            if h > vh:
                h, w = vh, int(vh * 16 / 9)
            img = Image.fromarray(frame[..., ::-1]).resize((w, h))
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.configure(image=self._photo)
        self.status.configure(text=f"{self.pipe.status}   {self.pipe.fps:.0f} fps")
        self.usage.configure(text="An app is using Lumina Camera - Plus" if self.pipe.in_use
                             else "Select \"Lumina Camera - Plus\" in Teams, Zoom or Meet")
        self.root.after(33, self._tick)

    def close(self):
        save_settings(self.s)
        self.pipe.stop()
        self.root.destroy()


def already_running():
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "LuminaLiteSingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = ctypes.windll.user32.FindWindowW(None, "Lumina Lite")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False


def main():
    if already_running():
        return
    root = tk.Tk()
    root.geometry("1200x700")
    if "--minimized" in sys.argv:
        root.iconify()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
