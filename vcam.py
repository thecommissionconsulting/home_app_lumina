"""Feed frames to the installed "Lumina Camera - Plus" DirectShow virtual camera.

The vcam filter (C:\\Program Files\\Lumina\\LuminaCamera.dll) reads frames from a
named file mapping called "LuminaFMO". Layout, recovered from the DLL:

    0x00  u32  state            2 = producer ready
    0x04  i32  frame counter    latest written frame; slot = counter % 3
    0x08  u32  consumer magic   vcam writes 0x0204066D when it reads
    0x10  i64  consumer time    vcam writes its last read time (100 ns since Unix epoch)
    0x20  3 slots, each 0x1FA4018 bytes:
          +0x00 i32 frame counter (must equal header counter)
          +0x04 i32 width
          +0x08 i32 height
          +0x0C u32 magic 0x0204066D
          +0x10 i64 timestamp, 100 ns since Unix epoch (ignored if older than 1 s)
          +0x18 NV12 pixel data (Y plane, then interleaved UV), up to 3840x2160

The vcam rescales to whatever size the calling app asked for, so frames can be
any size up to 4K.
"""

import ctypes
import ctypes.wintypes as wt
import struct
import time

import cv2
import numpy as np

MAGIC = 0x0204066D
HEADER = 0x20
SLOT_STRIDE = 0x1FA4018
SLOT_HDR = 0x18
MAX_W, MAX_H = 3840, 2160
MAP_SIZE = 99536896  # same size the original app creates
NAME = "LuminaFMO"
EPOCH_DIFF_100NS = 116444736000000000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileMappingW.restype = wt.HANDLE
_k32.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(ctypes.c_uint64)]

INVALID_HANDLE_VALUE = wt.HANDLE(-1)
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0xF001F
ERROR_ALREADY_EXISTS = 183


def now_100ns() -> int:
    ft = ctypes.c_uint64()
    _k32.GetSystemTimePreciseAsFileTime(ctypes.byref(ft))
    return ft.value - EPOCH_DIFF_100NS


def bgr_to_nv12(frame: np.ndarray) -> bytes:
    h, w = frame.shape[:2]
    i420 = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
    y = i420[:h]
    u = i420[h:h + h // 4].reshape(-1)
    v = i420[h + h // 4:].reshape(-1)
    uv = np.empty(u.size * 2, dtype=np.uint8)
    uv[0::2] = u
    uv[1::2] = v
    return y.tobytes() + uv.tobytes()


class LuminaVCam:
    def __init__(self):
        self.handle = _k32.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, MAP_SIZE, NAME)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateFileMapping failed")
        self.already_existed = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        self.base = _k32.MapViewOfFile(self.handle, FILE_MAP_ALL_ACCESS, 0, 0, 0)
        if not self.base:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.handle)
            raise OSError(err, "MapViewOfFile failed")
        self.buf = (ctypes.c_ubyte * MAP_SIZE).from_address(self.base)
        self.counter = struct.unpack_from("<i", self.buf, 4)[0]

    def send(self, frame_bgr: np.ndarray) -> None:
        h, w = frame_bgr.shape[:2]
        if w > MAX_W or h > MAX_H:
            scale = min(MAX_W / w, MAX_H / h)
            frame_bgr = cv2.resize(frame_bgr, (int(w * scale) & ~1, int(h * scale) & ~1), interpolation=cv2.INTER_AREA)
        elif w % 2 or h % 2:
            frame_bgr = frame_bgr[: h & ~1, : w & ~1]
        h, w = frame_bgr.shape[:2]
        data = bgr_to_nv12(frame_bgr)

        n = self.counter + 1
        off = HEADER + (n % 3) * SLOT_STRIDE
        ctypes.memmove(self.base + off + SLOT_HDR, data, len(data))
        struct.pack_into("<iiiIq", self.buf, off, n, w, h, MAGIC, now_100ns())
        struct.pack_into("<i", self.buf, 4, n)
        struct.pack_into("<I", self.buf, 0, 2)
        self.counter = n

    def consumer_active(self, within_s: float = 2.0) -> bool:
        """True if some app has pulled a frame from the vcam recently."""
        magic, t = struct.unpack_from("<Iq", self.buf, 8)[0], struct.unpack_from("<q", self.buf, 0x10)[0]
        return magic == MAGIC and (now_100ns() - t) < within_s * 1e7

    def close(self) -> None:
        if self.base:
            struct.pack_into("<I", self.buf, 0, 0)
            self.buf = None
            _k32.UnmapViewOfFile(self.base)
            self.base = None
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None
