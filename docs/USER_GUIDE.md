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
4. Press **Start the guided lesson**. The **Learn** page teaches every gesture in the
   active profile one at a time: it shows what to do and what the gesture does, and
   moves on by itself once the camera sees you do it. Practice mode is on throughout,
   so nothing is clicked. Skip or go back to any step; open **Learn** in the sidebar
   to take the lesson again, for example after switching profile.

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

**How scrolling works (Swipe, the default).** Show the scroll pose (two fingers is the
steadiest), then **flick your hand upward**: the page scrolls up, keeps rolling by
itself and coasts to a stop, like flicking a page on a phone. Flick again while it is
still moving to go further and faster. A flick downward scrolls down; a clear sideways
flick scrolls sideways. Only quick movements count - slow movement does nothing, and
bringing your hand back for the next flick is ignored for about a second, so the page
never scrolls back by accident. If your hand blurs mid-flick the scroll still
completes. To scroll up and down only, switch on **Scroll up and down only**.

**Glide** (System Controls → Scroll style) is the alternative for long documents. When the scroll pose has been held for
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
- **Holo Desk** — the JARVIS workspace: camera, gesture ring, system controls, panels you
  move by hand, a dock and your gesture cards (section 8e).
- **Learn** — the guided lesson: one gesture per step, checked by the camera, in
  practice mode.
- **Gesture Studio** — practice area, gesture → action mapping, an on/off switch and
  optional own confidence / cooldown per gesture, recognition sensitivity.
- **Training Studio** — record, train and evaluate your own gestures (section 8a).
- **System Controls** — cursor speed and smoothing, double-click window, scroll speed,
  drag, your keyboard shortcuts, input permission.
- **Media & Presentation** — switches, slide keys, test buttons, compatibility notes.
- **Smart Home** — webhooks to a hub, with confirmation (section 8f).
- **AI Insights** — usage from the local history, recognition quality, suggestions,
  custom gesture training.
- **Voice** — voice commands, wake word and the assistant (sections 8b and 8c).
- **Face Control** — the hands-free mode (section 8d).
- **Clipboard** — copy / paste gestures and the history of copied texts.
- **Profiles** — Default, Work, Presentation, Air Mouse, Browse, GestureAI, JARVIS, Media
  and your own; rename, duplicate, import, export.
- **Settings** — theme (dark, JARVIS or light), accent colour, mini controller, camera, privacy and data, help.
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

## 8b. Voice commands

Switch on **Voice → Listen for voice commands**. The first time, a speech model (about
40 MB) is downloaded and macOS asks for microphone access (for the app you launched
from). The header shows **🎙 Listening** while the microphone is open.

Say **"computer"** and then the command, in one breath or as two phrases:

| Say | What happens |
|---|---|
| "computer, scroll down" / "scroll up a little" / "scroll down fast" | One push of scrolling that coasts to a stop |
| "computer, scroll this page slowly" / "keep scrolling down" | Scrolls steadily until you say "computer, stop" |
| "computer, go to top" / "go to bottom" / "page down" | Jumps |
| "computer, change the tab" / "next tab" / "previous tab" / "new tab" / "close tab" | Browser tabs |
| "computer, open google chrome" / "open safari" / "open notes" | Opens an installed application |
| "computer, switch app" / "minimize" / "restore" / "go back" / "reload" | Windows and navigation |
| "computer, click" / "double click" / "right click" | Clicks where the cursor is |
| "computer, copy" / "paste" / "cut" / "select all" / "undo" / "delete" | Editing |
| "computer, play" / "pause" / "next song" / "volume up" / "mute" | Media |
| "computer, stop everything" / "resume" | Emergency stop and resume |

Speech without the wake word is ignored. The Voice page shows what was heard and what
was done, which is the place to look when a command is not taken. It understands these
commands and natural variations of them ("please scroll the page down a bit"); it is
not a free-form assistant. Applications whose names are not ordinary English words
(for example "Xcode") cannot be asked for by name: add them under System Controls →
Applications with a name you can say. Voice works with the camera stopped.

## 8c. Assistant: say what you want done

After the wake word you can also ask freely (Voice → **Understand free requests**):

| Say | What happens |
|---|---|
| "computer, open YouTube and search for <song>" / "play <song> on YouTube" | YouTube opens with the search results |
| "computer, open Gmail" / "open WhatsApp" / "open amazon dot com" | The site opens in your browser (nothing needs installing) |
| "computer, search Google for <anything>" | A Google search |
| "computer, write a mail about <topic>" / "... to <person> about <topic>" | The AI writes subject and text and opens it as a **draft**; you check and send |
| "computer, write a paragraph about <topic>" | The AI writes it and pastes it where your cursor is |
| "computer, type <words>" | Exactly those words are pasted where your cursor is |
| "computer, what is ...?" / "explain ..." | The answer is shown on the Voice page and read aloud |

Free requests take a second or two; the first mail after starting takes longer while
the AI loads. Put your name under **Your name, for signing mails**. Choose whether
drafts open in Gmail or your mail app. The assistant runs entirely on this computer
and cannot send mail, browse, or click through pages for you; read what it writes.

## 8d. Face Control (hands-free)

Open **Face Control** in the sidebar and switch on **Use Face Control**. The first time,
the face model (about 4 MB) is downloaded once. While it is on, hand gestures do
nothing; the header shows **🙂 FACE CONTROL**.

| Do this | It does (default — change any of them on the page) |
|---|---|
| Move your head | Moves the cursor |
| 😉 Wink your left eye | Click — two winks quickly = double-click |
| 🤨 Raise your eyebrows | Right click |
| 😗 Pucker your lips (kiss) | Double-click: opens a folder, file or link in one go |
| 😄 Smile | Minimize the window in front |
| 😮 Open your mouth | Scroll mode on / off — while on, look up or down to scroll (further = faster) |
| 😌 Close both eyes for about a second | Pause / resume control |

Each expression has an action menu, a live bar that fills as you make it (full = it
acts), and a sensitivity slider under **Tuning**. The right-eye wink is unused by
default; give it an action if you can wink that eye.

Cursor style:
- **Like a mouse (precise)**, the default: slow head movements move the cursor a little,
  for small targets such as tabs; quick ones cross the screen. Nothing to recenter.
- **Point with your head**: where you face is where the cursor goes. Press **Recenter**
  while looking at the middle of the screen if it drifts.

Tips:
- Sit facing the camera with your face evenly lit, and hold still for a moment after
  switching on.
- The cursor holds still while you make an expression, so the click lands where you
  aimed. Aim first, then make the expression.
- Ordinary blinks are ignored: both eyes close together, which is not a wink.
- If a bar fills by itself (for example while you talk or laugh), lower that
  expression's sensitivity or set it to *No action*.
- Glasses with reflections and dim light make eye detection less reliable.

## 8e. JARVIS mode

1. **Settings → Theme → JARVIS (cyan HUD)** for the look.
2. Choose the **JARVIS** profile in the header for the gestures below.
3. Open **Holo Desk** in the sidebar.

| Gesture (JARVIS profile) | Action |
|---|---|
| Point | Move the cursor |
| Pinch | Click; hold to grab and drag; open the fingers to drop |
| Two quick pinches | Double-click |
| Open palm | Rest — the cursor stays still |
| Flick left / right with an open palm | Previous / next desktop |
| Flick up / down with an open palm | Scroll up / down |
| Thumb + index "L", spread / close | Zoom in / out |
| Two fingers | Right click |
| Three fingers held, then move | Move the front app window |
| Fist | Emergency stop |
| Both palms | Pause / resume (also after an emergency stop) |

**Moving Holo Desk panels:** point at a panel's title bar, pinch, keep pinching and move
your hand, then open your fingers. Drag the ◢ corner to resize. The layout is saved;
**Reset layout** restores it.

**Virtual keyboard:** open it with the ⌨ button (Holo Desk, its dock) or a gesture mapped
to *Show / Hide Keyboard*. Point at a key and pinch. It types into the app you were
using — click into a text field first (with a pinch), then type. ⇧ capitalises the next
key. One pinch types one key.

**Windows and desktops:** map *Move Window*, *Resize Window*, *Window to Left / Right
Half*, *Maximize Window*, *Center Window*, *Next / Previous Desktop* or *Mission Control*
to any gesture in Gesture Studio. Desktop switching needs System Settings → Keyboard →
Keyboard Shortcuts → Mission Control → "Move left / right a space" switched on.
Full-screen windows can't be moved.

## 8f. Smart home

TouchlessX switches devices through your smart-home hub; it never talks to a device
directly.

1. In your hub, create a webhook that switches the device:
   - **Home Assistant** (free): an automation with trigger **Webhook** and the device's
     action, e.g. *Toggle desk lamp*. Copy the webhook URL.
   - **IFTTT**: a *Webhooks* applet. **Node-RED**: an *http in* node.
2. On the **Smart Home** page, enter a name, the URL and a token if your hub needs one.
   Press **Add**, then **Test**.
3. Switch **Smart Home** on (dashboard) and map the device to a gesture in Gesture Studio.

Smart Home is off by default. With **Ask for confirmation** on (the default), the device
gesture only arms the command; show a thumbs-up within 5 seconds to send it. Tokens are
kept on this computer only.

The webhook is one-way: TouchlessX does not know whether the device is on or off.
Devices that only work through their maker's app - for example Xiaomi Mi Home cameras -
need a hub that supports them first (Home Assistant with its Xiaomi integration); what
can be controlled then depends on the model. Live camera video stays in the maker's app.

## 8g. Controlling your iPhone

TouchlessX runs on the Mac, but it can control an iPhone through macOS's **iPhone
Mirroring** app, which shows the iPhone's screen in a window you work with the Mac's
mouse and keyboard:

1. Open **iPhone Mirroring** (Spotlight or Launchpad) and follow its one-time setup.
2. Use your gestures on its window: pinch = tap, held pinch and move = swipe, the scroll
   gesture scrolls, the virtual keyboard types.
3. iPhone Mirroring's shortcuts can be mapped as keyboard shortcuts (section 7):
   ⌘1 Home Screen, ⌘2 App Switcher, ⌘3 Spotlight.

It needs iOS 18 or newer, the same Apple ID on the Mac and the iPhone, the iPhone nearby
and locked, and a region where Apple offers the feature. On the iPhone itself, iOS has
its own **Eye Tracking** (Settings → Accessibility).

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
| Voice: header shows "No sound" | Allow the microphone for the app you launched from (System Settings → Privacy & Security → Microphone), then restart |
| Voice: commands are ignored | Start with the wake word; check the Voice page for what was heard; speak a little slower and closer |
| Swipes fire while moving the cursor | Raise *Swipe distance*, shorten *Swipe time window*, or use the Work profile |
| Low frame rate | Choose a lower resolution in Settings |
| Face Control: clicks by itself | Lower that expression's sensitivity under Tuning, or set it to No action |
| Face Control: a wink or expression is not noticed | Watch its bar on the Face Control page and raise its sensitivity; face the camera with even light |
| Face Control: cursor drifts (Point with your head) | Look at the middle of the screen and press Recenter, or use the Like a mouse style |
| Holo Desk: a panel will not move | Pinch on the panel's title bar and hold it a moment before moving |
| Virtual keyboard types nothing | Pinch into the text field you want first; check that control is not paused or in practice mode |
| Next / previous desktop does nothing | Switch on System Settings → Keyboard → Keyboard Shortcuts → Mission Control → "Move left / right a space" |
| A window will not move or snap | It is full screen, or the app does not allow it; leave full screen first |

`python app.py --check` tests the camera and tracking without opening the window.

## 11. Limits

- Recognition depends on lighting, background, distance and hand orientation. No
  accuracy figure is claimed; none has been measured across users and conditions.
- Tested on macOS (Apple Silicon) only.
- Face Control works differently from face to face; tune the sensitivities to yours.
- Window moving and snapping are macOS only and not for full-screen windows.
- Not for vehicles, medical devices, machinery or any safety-critical use.
