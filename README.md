# Lumina Lite

A replacement for the Lumina Studio app, which no longer works. It needs a free
license key that it requests from `api.getlumina.com`, and that server is gone:
the DNS name points at a Heroku app that no longer exists, and getlumina.com
returns 503. Without a key the app stays stuck in onboarding and never feeds
the virtual camera.

Lumina Lite reads the camera directly ("Lumina Camera - Raw", a standard UVC
webcam, 4K30 MJPEG), applies effects, and sends the result to the
"Lumina Camera - Plus" virtual camera that the original installer already
registered. So in Teams, Zoom, Meet and other apps, you still pick **Lumina Camera - Plus**.

## Run

Start Menu or Desktop: **Lumina Lite**. Installed in `%LOCALAPPDATA%\LuminaLite`.
Settings are saved to `%APPDATA%\LuminaLite\settings.json`.

Keep the window open (it can be minimised) while you're in a call. When the app
is closed, Lumina Camera - Plus shows the "Lumina is starting" placeholder.

## Features

- Lumina camera tuning (on by default): when the app starts it sends the camera
  the values the original Lumina app used (backlight compensation 4,
  brightness 10, gain 10, hue 8). The camera's own defaults include backlight
  compensation 10, which washes the whole picture out. Changes you make in the
  hardware settings dialog are kept until the app restarts.
- 4K capture with 1080p output gives the sharpest picture when effects are off.

- "Apply video call preset" button: 1080p, not mirrored, slow auto-framing,
  light background blur, slightly warmer colour with a little extra contrast
  and saturation. Holds 30 fps on this laptop. Video apps mirror only your own
  self-view, so the outgoing feed should stay unmirrored.
- Background segmentation (15 per second) and face detection (10 per second)
  run on their own threads, so the video stays at 30 fps. 4K capture with
  effects on drops to about 21 fps on this CPU.

- 1080p or 4K capture. 4K gives lossless digital zoom and auto-framing.
- 720p, 1080p or 4K output.
- Auto-frame: follows your face with adjustable tightness and speed.
- Manual zoom, pan and tilt.
- Background blur, or replace the background with one of the images Lumina shipped.
- Brightness, contrast, saturation and warmth.
- Hardware settings button: opens the camera's own UVC dialog for exposure,
  focus, white balance, gain and so on. The values are stored on the camera.
- Mirror.

## How the virtual camera is fed

`LuminaCamera.dll` reads a named shared-memory block, `LuminaFMO`. Its layout is
documented at the top of `vcam.py`: a header, then three NV12 frame slots with
width, height, a magic number and a timestamp. Lumina Lite creates that block
and writes frames into it, the same way the original app did.

The original LuminaStudioApp must not be running at the same time. Lumina Lite
closes it on startup.

Lumina Lite starts minimised at login from `Lumina Lite.lnk` in the Startup
folder (`app.py --minimized`). The original installer's `Lumina.lnk` was moved
out of Startup to `%LOCALAPPDATA%\LuminaLite\Lumina.lnk.original-startup`.

## Install or update

Needs [uv](https://docs.astral.sh/uv/) and the original Lumina software
(for the "Lumina Camera - Plus" driver).

```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Startup
```

This copies the app to `%LOCALAPPDATA%\LuminaLite`, builds its venv there and
creates Start Menu and Desktop shortcuts. `-Startup` also starts it minimised at
login. The venv is kept on a short path because MediaPipe fails to load its DLL
when the path is longer than 260 characters.
