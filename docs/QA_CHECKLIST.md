# Manual QA checklist

Automated tests (`pytest tests/`) cover gesture rules, state transitions, dispatch,
profiles, shortcuts, history and config. They cannot cover the camera, real hands or
the OS. Run this list on each platform before calling a build tested, and record the
OS version, hardware, camera and Python version with the results.

## Start-up and camera
- [ ] Fresh checkout: install, start, Getting Started page appears.
- [ ] Camera prompt answered "allow": preview starts without restarting the app.
- [ ] Camera prompt answered "deny": clear error message; app stays usable.
- [ ] `python app.py --check` reports the model, camera and a frame rate.
- [ ] Stop Camera / Start Camera works repeatedly.
- [ ] Unplug an external camera while running: error shown, no crash; plug in again
      and Start Camera recovers.
- [ ] Another app holding the camera: error shown, recovers after it is released.
- [ ] Change camera and resolution in Settings: preview restarts with the new values.
- [ ] Quit: camera light goes off.

## Input permission
- [ ] Without permission: dashboard shows "No permission"; gestures are shown but
      nothing moves; test buttons report failure.
- [ ] With permission: cursor moves; test buttons on Media & Presentation work.

## Gestures (repeat in bright, dim and backlit conditions; near and far)
- [ ] Open palm moves the cursor to all four screen corners.
- [ ] Pinch clicks once per pinch; a held pinch does not repeat.
- [ ] Pinching does not move the cursor off a small target.
- [ ] Two quick pinches open a file in the file manager.
- [ ] Two fingers held: one right click.
- [ ] Three fingers + vertical movement scrolls in both directions.
- [ ] Point + vertical movement changes volume in steps.
- [ ] Fist held: play/pause once.
- [ ] Fast flick left/right: one slide per flick.
- [ ] Slow cursor movement across the screen: no swipe.
- [ ] Opening and closing the hand, and lowering it out of view: no action fires.
- [ ] A second hand or person entering the frame: no crash, no burst of actions.
- [ ] Count false triggers over 5 minutes of normal cursor use; record the number.

## Safety
- [ ] Space, Esc, header Pause, mini-controller Pause each stop actions immediately.
- [ ] Pausing during a drag releases the mouse button.
- [ ] Practice mode: actions are shown as "Practice: …" and nothing is sent.
- [ ] Each feature switch blocks exactly its own actions.
- [ ] Detections below the confidence threshold cause no action.
- [ ] Smart Home with confirmation: device gesture alone sends nothing; thumbs-up
      within 5 s sends once; after 5 s it sends nothing.

## Lock, emergency stop, hands
- [ ] GestureAI profile: fist locks; cursor, clicks, scroll and slides all stop;
      thumbs-up unlocks; the LOCKED badge appears and clicking it unlocks.
- [ ] EMERGENCY STOP button stops control with no hand in view, with the camera off,
      and while locked; RESUME CONTROL restores it.
- [ ] Emergency stop gesture works in each profile and while locked.
- [ ] No hand in view for a while: nothing fires when the hand returns.
- [ ] Controlling hand = right: the left hand alone does nothing, and vice versa.
- [ ] Mirror preview off: the picture flips, cursor direction is unchanged.
- [ ] Pointer calibration: after capturing two corners the cursor reaches all four
      screen corners inside that area; Clear restores the old behaviour.

## Training Studio
- [ ] Opening the page shows TRAINING · CONTROL OFF and gestures control nothing.
- [ ] Create, rename, clear and delete a gesture.
- [ ] Start / Pause / Resume / Stop; the counter only grows while a hand is visible.
- [ ] Train with one gesture, with two, and with too few samples (clear message).
- [ ] Metrics and confusion matrix appear; warnings for thin / unbalanced data.
- [ ] The trained pose is recognised live; similar untrained poses are not.
- [ ] Restart: the model is still active. "Use this" on an older version reverts.
- [ ] `data/` contains CSV, model files and the history only - no images.

## Applications and shortcuts
- [ ] Add an application, map it, open it by gesture.
- [ ] A non-application file cannot be chosen.

## Profiles and settings
- [ ] Switch profile from the header: mappings and switches change at once.
- [ ] Create, rename, duplicate, delete; deleting the active profile switches first.
- [ ] Export, then import the file: identical profile appears.
- [ ] Import a non-profile file: clear error.
- [ ] Restart: active profile, theme and all settings are kept.
- [ ] Reset active profile restores defaults for that profile only.
- [ ] Add a keyboard shortcut, map it, trigger it by gesture in the target app.
- [ ] A disallowed key in a shortcut is rejected with a message.

## Interface
- [ ] Every page opens in dark and light theme; text is readable in both.
- [ ] Window at minimum size: nothing overlaps or is cut off.
- [ ] Mini controller stays above other apps, shows the state, can be closed and
      re-enabled in Settings.
- [ ] Main window minimised: gestures keep working; mini controller stays.

## Privacy
- [ ] No image or video file is created anywhere under the project folder.
- [ ] `logs/actions.log` and `data/history.db` contain only time, gesture, action,
      confidence.
- [ ] With history and log switched off, neither file grows.
- [ ] Clear history / Clear log empty them.
- [ ] With Smart Home unused, no network traffic after the first model download.

## Platform notes

| | macOS | Windows |
|---|---|---|
| Status | Developed and exercised here (Apple Silicon) | Code paths written, **not tested** |
| Camera permission | Asked on first start; belongs to the launching app (Terminal, VS Code) | Settings → Privacy → Camera → allow desktop apps |
| Input permission | System Settings → Privacy & Security → Accessibility for the launching app | None; UAC-elevated windows ignore synthetic input |
| Media keys | Sent as system media-key events | Sent through PyAutoGUI key names |
| Double-click | Second click marked as click 2 | Two quick clicks |
| Minimise / restore window | Accessibility API (exact window) | Win+Down / Alt+Tab (approximate) |
| Open application | `.app` bundles via `open -a` | `.exe` via the shell's file association |
| Horizontal scroll | Quartz scroll event | PyAutoGUI `hscroll` |

On Windows, run every section above before describing it as supported, and pay
attention to: camera opening time, media keys, horizontal scrolling direction,
minimise / restore, and DPI scaling of the preview.
