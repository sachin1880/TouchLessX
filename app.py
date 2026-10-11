"""TouchlessX entry point.

Loads configuration, wires the camera -> hand tracking -> gesture engine -> action
controller pipeline together, and starts the desktop UI.

Run with:
    python app.py            # the desktop app
    python app.py --check    # headless self-test: camera, model and frame rate
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.system import data_root

SOURCE_ROOT = Path(__file__).resolve().parent
# Settings, logs and downloaded models: the project folder when run from source, the
# per-user application-data folder when run as a packaged application.
ROOT = data_root(SOURCE_ROOT)
CONFIG_PATH = ROOT / "config" / "settings.json"
MODEL_PATH = ROOT / "assets" / "models" / "hand_landmarker.task"


def run_check(config: dict, seconds: float = 4.0) -> int:
    """Opens the camera, tracks for a few seconds and reports the measured frame rate.
    Sends no mouse/keyboard input and shows no window."""
    from src.camera import Camera, CameraError
    from src.gesture_engine import GestureEngine
    from src.hand_tracker import HandTracker, TrackerError

    # The optional parts: are their libraries present and loadable (matters most in a packaged build)?
    for name, modules in (("Voice commands", ("vosk", "sounddevice")), ("Free speech-to-text", ("faster_whisper",)),
                          ("Gesture training", ("sklearn.neighbors", "joblib")), ("Interface", ("customtkinter",))):
        try:
            for module in modules:
                __import__(module)
            print(f"{name}: OK")
        except Exception as exc:  # ImportError, or a native library that fails to load
            print(f"{name}: UNAVAILABLE ({exc})")
    try:
        tracker = HandTracker(MODEL_PATH)
        print("Hand-tracking model: OK")
        camera = Camera(config["camera_index"], config["frame_width"], config["frame_height"])
        camera.start()
    except (CameraError, TrackerError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    engine = GestureEngine()
    frames = hand_frames = 0
    seen: set[str] = set()
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        frame = camera.read()
        if frame is None:
            time.sleep(0.01)
            continue
        frames += 1
        result = tracker.process(frame)
        event = engine.update(result)
        if result is not None:
            hand_frames += 1
            seen.add(event.name)
    elapsed = time.monotonic() - started
    camera.stop()
    tracker.close()

    if frames == 0:
        print("FAILED: the camera opened but delivered no frames.", file=sys.stderr)
        return 1
    print(f"Camera {config['camera_index']}: OK, {frames / elapsed:.1f} FPS including tracking")
    print(f"Hand visible in {hand_frames}/{frames} frames; gestures seen: {', '.join(sorted(seen)) or 'none'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TouchlessX - webcam hand-gesture desktop control")
    parser.add_argument("--check", action="store_true", help="run a headless camera/tracking self-test and exit")
    args = parser.parse_args()

    try:
        from src.config import load_config

        config = load_config(CONFIG_PATH)
        if args.check:
            return run_check(config)
        from src.ui import App
    except ImportError as exc:
        print(f"Missing dependency: {exc}\nRun: pip install -r requirements.txt", file=sys.stderr)
        return 1

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    app = App(config, CONFIG_PATH, app_file=SOURCE_ROOT / "app.py")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
