# TouchlessX

A Python desktop app that turns your webcam into a touchless controller: it tracks
your hand, recognizes a small set of deliberate gestures, and maps them to mouse
movement, clicks, media control, and presentation navigation. Everything runs
locally — no cloud account, no video stored or uploaded by default.

## Gesture map

| Gesture | Action | Category |
|---|---|---|
| ✋ Open Palm | Move the cursor | Mouse Control |
| 🤏 Pinch (thumb + index) | Left click | Mouse Control |
| ✌️ Two Fingers (index + middle) | Right click | Mouse Control |
| ☝️ Point, move up/down | Volume up/down | Volume Control |
| ✊ Fist | Play / Pause media | Media Control |
| 👈 Swipe Left | Previous slide | Presentation |
| 👉 Swipe Right | Next slide | Presentation |

Each category can be toggled on/off live from the **Quick Controls** panel in the UI.
Press **Space** (or the Pause button) at any time to immediately stop all gesture-
triggered actions — the camera keeps running and showing detection, nothing is sent
to the mouse/keyboard while paused.

## Setup

Requires Python 3.9–3.11 (MediaPipe's Python wheels lag behind the newest Python
releases, so a very new interpreter may fail to install it).

```bash
cd TouchlessX
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

On first run your OS will prompt for **camera access**, and — since this app moves
the mouse and presses keys — for **input/accessibility control**:

- **macOS**: System Settings → Privacy & Security → Accessibility → enable your
  terminal app (or the packaged app, once built).
- **Windows**: no extra permission needed, but antivirus/UAC may prompt once.
- **Linux**: `pyautogui` needs an X11 session (not plain Wayland) for mouse control.

If input control can't be granted or fails to initialize, TouchlessX still runs in
**preview-only mode** — detection and the UI work, it just won't move anything.

## Project structure

```
TouchlessX/
├── app.py                   # Entry point
├── requirements.txt
├── config/
│   └── settings.json        # Sensitivity thresholds + per-category on/off, edited live by the UI
├── src/
│   ├── camera.py             # Camera Layer - reads webcam frames
│   ├── hand_tracker.py       # Hand Tracking Layer - MediaPipe landmarks
│   ├── gesture_engine.py     # Gesture Engine - classification, cooldowns, swipe detection
│   ├── action_controller.py  # Action Dispatcher - gesture -> mouse/keyboard, category-gated
│   ├── safety.py             # Global pause + confidence gating
│   └── ui.py                 # Desktop UI (CustomTkinter)
└── tests/
    ├── test_gestures.py       # Gesture classification + cooldown/trigger behavior
    └── test_mapping.py        # Action mapping + gating, via a fake input executor
```

## Running the tests

Gesture classification and action-mapping logic are pure/stateful-but-deterministic
and run with no camera, display, or real mouse/keyboard needed:

```bash
pip install pytest
pytest tests/ -v
```

## Known limitations (first release)

- Gesture recognition is geometry-based (landmark distances/ratios), not a trained
  classifier — it's tuned for a hand facing the camera in reasonable lighting, not
  every angle, lighting, or background.
- Single hand, single user at a time.
- Presentation shortcuts (Left/Right arrow) work in most slideshow and media apps,
  but aren't guaranteed for every application.
- Smart Home and AI Assistant panels are shown in the UI as placeholders for a
  future extension — they're not wired to anything yet.

## Future enhancements

- Custom gesture training (per the original project scope's optional ML phase).
- Two-hand gestures and temporal gesture sequences.
- Smart-home device integration through a compatible hub/API.
- Packaged, double-clickable builds (PyInstaller) per OS.
