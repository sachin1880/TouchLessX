# TouchlessX user guide

TouchlessX lets you control the mouse, scrolling, media keys, slides and your own
keyboard shortcuts with hand gestures seen by your webcam. Everything is processed on
your computer.

## 1. First start

1. Start the app (`.venv/bin/python app.py`). The **Getting Started** page opens.
2. **Camera** — allow access when the OS asks. On macOS the permission is given to the
   app you launched from (Terminal, VS Code, …). TouchlessX retries by itself.
3. **Input control (macOS)** — System Settings → Privacy & Security → Accessibility →
   enable the app you launched from, then restart TouchlessX. Without it gestures are
   recognised but nothing is controlled, and the dashboard shows
   *System Controls: No permission*.
4. Press **Practise first** to try the gestures in practice mode, where nothing is
   sent to the computer.

## 2. Using it well

- **Raise your hand into the picture.** A hand at the bottom edge with the wrist out of
  view is treated as resting and ignored (the dashboard shows "Resting - raise your
  hand"). This stops a hand lying by the keyboard from triggering anything.
- **Pinch with the other fingers open** (like an "OK" sign), or at least with the index
  finger reaching forward. A closed fist is a fist, not a pinch.

- Sit 40–80 cm from the camera, palm facing it, with light in front of you rather
  than behind.
- One hand, one person in view.
- The cursor follows your **palm**, not a fingertip, so pinching does not move it.
- Hold still for a moment before you click small targets.

## 3. Gestures (Default profile)

| Gesture | Action | How it behaves |
|---|---|---|
| ✋ Open palm | Move the cursor | Continuous while the palm is open |
| 🤏 Pinch | Left click | Acts at once. Pinch twice within 0.6 s to **double-click** (opens files). With drag enabled, keep the pinch held to drag |
| ✌️ Two fingers | Right click | Hold for about a quarter of a second |
| 🤟 Three fingers + move up/down | Scroll | Starts after a short hold |
| ☝️ Point + move up/down | Volume | Starts after a short hold; one step per small movement |
| ✊ Fist | Play / pause | Hold for about a quarter of a second |
| 👈 👉 Fast sideways flick of the open hand | Previous / next slide | Only fast flicks count; slow cursor movement does not |
| 👍 Thumbs-up | Confirm | Confirms a pending smart-home command |
| 👆 👇 Fast vertical flick | *(not mapped)* | Available for your own mapping |
| 🖐 Opening the hand out of a fist | *(not mapped; Restore Window in Browse)* | Fires once, within 0.7 s of a held fist |

### Air Mouse profile

One hand does everything a mouse does. Choose **Air Mouse** in the **Profile** menu.

| Mouse action | Gesture |
|---|---|
| Move the cursor | ✋ Open hand, or ☝️ a pointing finger - move your hand |
| Left click | 🤏 Pinch thumb and index once |
| Double-click | 🤏 Pinch twice quickly |
| Drag and drop | 🤏 Pinch and **keep it closed** (about half a second), move your hand, open to drop |
| Right click | ✌️ Two fingers, held briefly |
| Scroll | 🤟 Three fingers, then move the hand up/down or left/right |
| Rest (nothing happens) | ✊ Fist, or take the hand out of view |

Making it fit you, all under **System Controls** unless noted:

- **Cursor speed** - how little hand travel crosses the screen.
- **Cursor smoothing** and **Dead zone** - steadier pointer for small targets.
- **Pointer calibration** - show the two corners of your comfortable reach once; the
  cursor then spans the whole screen within it, whatever the screen size. TouchlessX
  also follows a changed resolution or display by itself.
- **Double-click window**, **Scroll speed**, **Reverse scroll direction**, **drag hold time**.
- **Controlling hand** (Settings → Camera) - left, right or either, for left- or
  right-handed use. The other hand is ignored.

The cursor holds still while you pinch, so clicks land where you aimed.

### Browse profile

For reading and browsing, switch the **Profile** menu to **Browse**:

| Gesture | Action |
|---|---|
| ✋ Open hand **or** ✌️ two fingers, moved up / down / left / right | Scroll (glide, see below) |
| ☝️ Point | Move the cursor |
| 🤏 Pinch | Left click. Twice within 0.8 s = double-click (opens a file). Held = drag, which also selects text |
| 🤟 Three fingers (held) | Copy |
| 👍 Thumbs-up (held) | Paste |
| 👈 Finger pointing left (held) | Delete |
| 👉 Finger pointing right (held) | Right click |
| ✊ Fist (held briefly) | Minimise the window in front |
| 🖐 Open the hand again within about a second | Restore the window you just minimised |

Cut, Select All, Undo, Redo, Paste Previous Copy, Page Up / Down and Jump to Top /
Bottom are available as actions too; assign them in Gesture Studio. The **Clipboard**
page shows what is mapped and keeps the last ten copied texts.

**How scrolling works (Glide, the default).** When the scroll pose has been held for
a moment, the spot where your hand is becomes a neutral point (drawn as a circle in
the preview). Move your hand **above** it and the page scrolls up and **keeps
scrolling** for as long as you hold it there; further away is faster. Bring the hand
back to the middle to stop, below it to scroll down, left or right of it to scroll
sideways. You never need to bring your hand back for a second stroke, so nothing
scrolls back. Once it has started in one direction it stays in that direction until
you return to the middle.

Prefer the page to move exactly with your hand, like a touch screen? Choose **Follow
my hand** under System Controls → Scroll style. In that style, returning your hand
for another stroke scrolls back too - lower your hand out of the pose first.

If scrolling feels backwards, switch on **Reverse scroll direction** in System
Controls; **Scroll speed** is there as well. Restore only brings back windows TouchlessX itself
minimised, most recent first. Minimise and restore are tested on macOS; on Windows they
send Win+Down and Alt+Tab, untested.

Every mapping can be changed, or set to **No action**, in **Gesture Studio**.

### GestureAI profile (lock-based)

| Gesture | Action |
|---|---|
| ✋ Open palm | Safety hold: nothing happens while it is shown |
| ☝️ Point | Move the cursor |
| 🤏 Pinch | Click; twice = double-click; hold = drag |
| ✌️ Two fingers, move the hand | Scroll (both directions) |
| 🤟 Three fingers (held) | Bring TouchlessX to the front |
| 👈 👉 Finger pointing sideways (held) | Previous / next slide |
| ✊ Fist (held) | **Lock** all gesture control |
| 👍 Thumbs-up (held) | **Unlock**, or confirm a pending smart-home command |

While locked, the header shows an amber **LOCKED** badge; click it to unlock without a
gesture.

## 4. Stopping it

- The red **EMERGENCY STOP** button at the top right of the dashboard. It turns into
  **RESUME CONTROL**.
- An **emergency stop gesture** of your choice (Settings → Emergency stop). It works in
  every profile and while locked; resuming is done from the button.
- **Space** or **Esc** while TouchlessX is the front window.
- The **Pause** button in the header.
- The **floating mini controller** — a small window that stays above other apps and
  has its own Pause button. You may need to click it twice when another app is in
  front (the first click brings it forward).
- **Stop Camera** switches tracking off completely.
- Optionally map a gesture to *Pause / Resume Control*.

There is no system-wide pause hotkey: Space/Esc only work while TouchlessX is in front.

## 5. Pages

- **Dashboard** — preview, current gesture, confidence, last action, on/off switch per
  feature, recent gestures, session analytics, system status.
- **Gesture Studio** — practice area, gesture → action mapping, an on/off switch and
  optional own confidence / cooldown per gesture, recognition sensitivity.
- **Training Studio** — record, train and evaluate your own gestures (section 8a).
- **System Controls** — cursor speed and smoothing, double-click window, scroll speed,
  drag, your keyboard shortcuts, input permission.
- **Media & Presentation** — switches, slide keys, test buttons, compatibility notes.
- **Smart Home** — webhooks to a hub, with confirmation.
- **AI Insights** — usage from the local history, recognition quality, suggestions,
  custom gesture training.
- **Profiles** — Default, Work, Presentation, Media and your own; rename, duplicate,
  import, export.
- **Settings** — theme, accent colour, mini controller, camera, privacy and data, help.
  Under **Appearance**, click a colour dot or **Custom…** to recolour buttons, switches
  and highlights at once; it works in both the dark and the light theme. The red
  emergency stop and the green / amber status colours stay as they are on purpose.

## 6. Profiles

A profile holds gesture mappings, sensitivity and the feature switches. Switch from the
**Profile** menu in the header. Whatever you change is saved to the active profile.

| Profile | Intent |
|---|---|
| Default | Everything on |
| Work | Cursor, clicks, scroll, drag; no media keys or slide swipes |
| Presentation | Only slide swipes act; no cursor or clicks |
| Air Mouse | A complete one-handed mouse: move, click, double-click, drag, right-click, scroll |
| Browse | Open hand scrolls both ways, fist minimises, opening it restores, point moves the cursor |
| Media | Play/pause, volume, track skipping; no cursor |

Exported profiles are plain JSON. Imported files are checked: unknown gestures and
actions are dropped, numbers are kept in range, and shortcut or smart-home actions are
kept only if you already have a shortcut or device with that name.

## 7. Keyboard shortcuts

In **System Controls → Keyboard shortcuts**, name a key combination (for example
`Cmd+Tab`) and map it to a one-shot gesture in Gesture Studio. A shortcut is modifiers
(Cmd, Ctrl, Alt, Shift, Win) plus one letter, digit, F-key, arrow or editing key.
Gestures can only press keys; they cannot run commands.

## 8a. Training your own gestures

1. Open **Training Studio**. Gesture control switches off while you are there.
2. **Create** a gesture (for example "Rock") and select it. Choose poses the built-in
   rules don't use: rock sign, four fingers, "L" shape, "call me".
3. Press **Start**, wait for the countdown, hold the pose and move your hand a little:
   nearer, further, slightly tilted. **Pause** / **Resume** as needed, then **Stop**.
   Aim for 40 or more samples per gesture, and similar numbers for each.
4. Press **Train model**. You get accuracy, precision, recall and a confusion matrix
   measured on samples the model did not see. Warnings tell you when a gesture has too
   few samples or the set is unbalanced.
5. Map the gesture to an action in **Gesture Studio**.

Every training run is kept as a version (the last five). **Use this** on an older
version reverts to it. The model is loaded automatically at the next start.

Applications (System Controls → Applications) and pointer calibration (System Controls
→ Pointer calibration) are set up the same way: pick, then map or capture.

## 8. Smart home

Add a webhook URL from your hub and map the device to a gesture. Smart Home is off by
default. With **Ask for confirmation** on (the default), the device gesture only arms
the command; show a thumbs-up within 5 seconds to send it.

## 9. Privacy

- Camera frames are processed in memory and discarded. Nothing is recorded or uploaded.
- No telemetry and no account.
- **Action history** (`data/history.db`) and the **diagnostic log**
  (`logs/actions.log`) hold time, gesture, action and confidence only. Each can be
  switched off and cleared in Settings; history is deleted after the retention period.
- Custom gestures store landmark coordinates, not images.
- Network use: the one-time model download, and the smart-home webhooks you configure.

## 10. Troubleshooting

| Problem | What to do |
|---|---|
| "Camera unavailable" | Close other apps using the camera; check the camera permission; pick another camera in Settings |
| Gestures recognised, nothing happens | Input permission is missing (section 1), control is paused, practice mode is on, or that feature's switch is off |
| Accidental actions | Raise *Pose hold time* or *Minimum confidence* in Gesture Studio, or set the gesture to No action |
| A gesture does not fire | Lower *Pose hold time*; check AI Insights for "shown too briefly" |
| Cursor trembles | Raise *Cursor smoothing* or *Dead zone* |
| Cursor cannot reach the screen edges | Raise *Cursor speed* |
| A second hand makes the cursor jump | Choose a controlling hand in Settings |
| Everything stopped responding to gestures | Check the header: EMERGENCY STOP / paused, an amber LOCKED badge, or PRACTICE / TRAINING |
| A trained gesture is never recognised | Train after recording; make sure AI Assistant is on; the pose may already be a built-in one |
| Scrolling goes back and forth | Use the Glide scroll style (System Controls); in Follow, drop the pose before bringing your hand back |
| Scrolling will not stop | Bring your hand back inside the circle shown in the preview, or change the hand pose |
| Swipes fire while moving the cursor | Raise *Swipe distance*, shorten *Swipe time window*, or use the Work profile |
| Low frame rate | Choose a lower resolution in Settings |

`python app.py --check` tests the camera and tracking without opening the window.

## 11. Limits

- Recognition depends on lighting, background, distance and hand orientation. No
  accuracy figure is claimed; none has been measured across users and conditions.
- Tested on macOS (Apple Silicon) only.
- Not for vehicles, medical devices, machinery or any safety-critical use.
