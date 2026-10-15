# TouchlessX — Project Documentation

Everything that is implemented in this project, how it works, and how to use it.

- **What it is:** a desktop application that lets you control your computer with hand
  gestures seen by the webcam, with your face (head movement and expressions), and with
  spoken requests, without touching the mouse or keyboard.
- **Language:** Python 3.10–3.12.
- **Runs on:** macOS (developed and tested on an Apple-silicon MacBook). Windows code
  paths exist but have not been tested.
- **Where the work happens:** on your own computer. Camera frames, voice audio and the
  AI model are all processed locally.
- **Size:** about 9,800 lines of application code in 28 modules, and 333 automated tests.

---

## 1. Features at a glance

| Area | What is implemented |
|---|---|
| Hand tracking | Live webcam preview, 21 hand landmarks drawn on the picture, frame-rate and latency display |
| Gestures | 17 built-in gestures, two of them made with both hands, plus gestures you train yourself |
| Mouse | Cursor movement, click, double-click, right-click, drag and drop, scrolling in both directions |
| Keyboard and text | Copy, cut, paste, select all, delete, undo, redo, your own keyboard shortcuts |
| Windows and browser | Minimise and restore; move, resize and snap other apps' windows; switch desktops and application; tabs, back / forward / reload, zoom |
| Media and slides | Play / pause, next / previous track, volume, mute, next / previous slide |
| Voice | Wake word, about 70 spoken commands, works offline |
| Face Control | Hands-free mode: head moves the cursor; wink, raised eyebrows, puckered lips, smile and open mouth mapped to clicks, scroll and more |
| JARVIS mode | Holo Desk workspace with panels moved by hand, cyan HUD theme, JARVIS profile |
| Virtual keyboard | On-screen keyboard typed on by pinching; types into the app in front |
| Learn | A guided lesson that teaches every gesture, checked by the camera |
| Assistant | "Open YouTube and search …", "write a mail about …", questions, dictation — using a local AI model |
| Profiles | Eight ready-made gesture layouts, plus create / rename / duplicate / import / export |
| Training Studio | Record your own gesture, train a classifier, see its accuracy, keep and revert model versions |
| Safety | Emergency stop button and gesture, lock / unlock, practice mode, confirmation for smart-home commands |
| Settings | Every gesture can be remapped; sensitivity, cursor speed, scroll style, theme and accent colour |
| Insights | Local history of actions, usage charts, recognition quality, suggestions |
| Smart home | Optional webhooks to a home hub, mapped to gestures |
| Privacy | No video saved, no telemetry, no account |
| Desktop integration | Menu-bar icon, start at login, update check, and a build script for a Mac app and installer image |

---

## 2. How it works

Each camera frame goes through these steps, about 30 times a second:

1. **Camera** (`camera.py`) reads a frame from the webcam and mirrors it.
2. **Hand tracking** (`hand_tracker.py`) uses MediaPipe's Hand Landmarker to find 21
   points on the hand.
3. **Gesture engine** (`gesture_engine.py`) names the hand shape from those points, and
   only accepts it when it is stable (see section 6).
4. **Safety** (`safety.py`) checks that control is not stopped or locked and that the
   detection is confident enough.
5. **Action controller** (`action_controller.py`) looks up what that gesture is mapped
   to and performs it: moves the mouse, presses keys, scrolls.
6. **Interface** (`ui.py`, `ui_pages.py`) shows the picture, the gesture, and what was
   done.
7. **Logs** (`action_log.py`, `history.py`) record the action as text for the insights
   page and for troubleshooting.

Voice runs alongside on its own thread: microphone → wake word → speech-to-text →
command or assistant task → the same action controller.

With **Face Control** on, steps 2–3 are replaced: `face_control.py` finds 478 face points
and expression scores with MediaPipe's Face Landmarker, turns head movement into cursor
movement and expressions into actions, and hands them to the same action controller -
so pause, practice mode and the feature switches apply exactly as for hands.

Clicks pass one more check: if the cursor is over the **virtual keyboard**, the click is
turned into a key typed into the application in front (`virtual_keyboard.py`).

The camera work runs on a background thread so the window never freezes.

---

## 3. Installing and running

### On a Mac

```bash
git clone https://github.com/sachin1880/TouchLessX.git
cd TouchLessX
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

On Windows the activate line is `.venv\Scripts\activate`.

The first start downloads the hand-tracking model (about 8 MB).

### Optional: the AI assistant

```bash
brew install ollama
ollama pull llama3.2:3b
```

This is about 2 GB. The app starts Ollama itself when it is first needed. Everything
else works without it.

### Useful commands

```bash
python app.py --check        # tests camera and hand tracking without opening the window
python -m pytest tests       # runs the 333 automated tests
```

---

## 4. First-time setup

1. **Camera permission** — click Allow when macOS asks.
2. **Accessibility permission** — System Settings → Privacy & Security → Accessibility →
   enable the app you launched from (Terminal or VS Code), then restart TouchlessX.
   Without this, gestures are recognised but nothing moves.
3. **Microphone permission** — only if you switch voice on.
4. A **Getting Started** page explains the camera, permissions and how to stop control.
   Press **Start the guided lesson**: the **Learn** page teaches each gesture of your
   profile, one step at a time, and moves on when the camera sees you do it. Nothing is
   clicked during the lesson.

On macOS these permissions belong to the program you start TouchlessX from, not to
TouchlessX itself.

---

## 5. Using it well

- **Raise your hand so the wrist is visible.** A hand at the bottom edge of the picture
  is treated as resting and ignored.
- **Pinch with your other fingers open**, like an "OK" sign. A closed fist is a fist.
- Sit 40–80 cm from the camera with light in front of you.
- One hand and one person in view.

---

## 6. Gestures

### The 17 built-in gestures

| Gesture | How to make it |
|---|---|
| Open palm | All five fingers extended |
| Point | Index finger up, others curled |
| Two fingers | Index and middle up |
| Three fingers | Index, middle and ring up |
| Pinch | Thumb and index fingertips together |
| Fist | All fingers curled |
| Thumbs-up | Fist with the thumb standing clearly above the hand |
| Point left / Point right | Index finger lying sideways |
| Swipe left / right / up / down | A fast flick of the open hand |
| Open from fist | Opening the hand within about a second of a held fist |
| Both palms | Both hands raised and open, held for 0.6 s |
| Two-hand pinch | Both hands pinching; move them apart or together |
| Thumb + index ("L") | Index up and thumb out; spread or close them to zoom with one hand. Only recognised while mapped |

By default **both palms** pauses and resumes control, and the **two-hand pinch** zooms
in as the hands move apart and out as they come together. While two raised hands are
in view, single-hand gestures are suspended, so the second hand cannot click or scroll
by accident. A second hand resting at the bottom of the picture is ignored.

Hand poses can drive either a continuous action (cursor, scroll, volume) while held,
or a one-shot action. Pinch, swipes and trained gestures are always one-shot.

### What makes recognition reliable

These rules exist because of real misfires found in recorded use.

| Rule | Why |
|---|---|
| A shape must be seen on 2 frames in a row | Hands pass through other shapes while opening and closing |
| One-shot poses must be held 0.25 s | A hand passing through "fist" should do nothing |
| Pinch releases only when fingers open clearly | Stops a pinch flickering into repeated clicks |
| A fist is not a pinch | In a fist the thumb rests on the index finger; this used to click |
| A resting hand is ignored | A hand lying by the keyboard was read as a fist and minimised windows 27 times in 3 minutes |
| One fist minimises one window | A second minimise within 2 seconds is ignored |
| Scroll and volume start after 0.3 s | Passing through the pose changes nothing |
| Low-confidence detections are dropped | If it is not sure, it does nothing |

All of these values can be changed in the app.

---

## 7. Actions a gesture or voice command can perform

| Group | Actions |
|---|---|
| Mouse | Move cursor, left click, right click, double click, scroll, scroll up / down a step, page up / down, jump to top / bottom |
| Text | Copy, cut, paste, paste previous copy, select all, delete, undo, redo |
| Windows | Minimise, restore, switch application, close tab / window, move / resize the front window with the hand, snap it to the left or right half, maximise, centre |
| Desktops | Next / previous desktop, Mission Control |
| Browser | Next / previous / new tab, back, forward, reload, zoom in / out |
| Media | Play / pause, next / previous track, volume up / down, mute |
| Slides | Next slide, previous slide |
| Control | Pause / resume, emergency stop, lock, confirm / unlock, safety hold, show TouchlessX, show / hide the virtual keyboard |
| Yours | Named keyboard shortcuts, "open application", smart-home devices |

Keyboard shortcuts are built from an approved list of keys. "Open application" accepts
only a real application that you picked. A gesture or voice command can never run a
command line or a script.

---

## 8. Profiles

A profile is a complete gesture layout. Switch from the **Profile** menu in the header.

| Profile | What it is for |
|---|---|
| Default | Open palm moves the cursor, pinch clicks, three fingers scroll, fist plays / pauses, swipes change slides |
| Work | Like Default but with no media keys or slide swipes, and drag switched on |
| Presentation | Only slide swipes act |
| Air Mouse | One hand as a full mouse: move, click, double-click, drag, right-click, scroll |
| Browse | Open palm or two fingers scroll, point moves the cursor, three fingers copy, thumbs-up pastes, fist minimises, opening the hand restores |
| GestureAI | Lock-based: fist locks all control, thumbs-up unlocks, open palm is a safe pose |
| JARVIS | Point moves, pinch clicks and drags, open palm rests, flicks change desktop or scroll, thumb + index "L" zooms, three fingers move the front window, fist is an emergency stop, both palms resume |
| Media | Play / pause, volume, track skipping; no cursor |

You can create your own, rename, duplicate, delete, export to a file and import. An
imported file is checked: unknown gestures and actions are dropped, numbers are kept in
range, and it cannot bring shortcuts, applications or smart-home targets with it.

---

## 9. How to use each feature

### Moving the cursor and clicking
1. Show the cursor pose for your profile (open palm, or a pointing finger).
2. Move your hand. The cursor follows your palm, so pinching does not nudge it.
3. Pinch once to click.
4. Pinch twice within 0.8 seconds to double-click, which opens a file.
5. To drag (also selects text): keep the pinch closed about half a second, move, then
   open your fingers. Drag must be switched on under System Controls.

### Scrolling
Three styles, chosen under System Controls → Scroll style:

| Style | How it behaves |
|---|---|
| Swipe (default) | A quick flick of the hand sets the page rolling and it coasts to a stop. Slow movement does nothing, and bringing the hand back does not scroll back |
| Glide | Hold your hand above where it started and the page keeps scrolling; further away is faster |
| Follow | The page moves exactly with your hand, like a touch screen |

Options: scroll speed, reverse direction, up-and-down only.

### Working with text
- Select by dragging with a held pinch, or pinch twice on a word.
- Copy, paste, cut, delete, select all, undo and redo are actions you map to gestures.
- The **Clipboard** page shows which gesture does what and keeps the last ten texts you
  copied, so an earlier one can be pasted again. It is held in memory only.

### Minimising and restoring a window
- Hold a fist (in the Browse profile) to minimise the window in front.
- Open your hand again within about a second to restore it.
- Restore only brings back windows TouchlessX itself minimised.

### Voice commands
1. Switch on **Voice → Listen for voice commands**.
2. Say **"computer"**, then the command. Speech without the wake word is ignored.

| You say | It does |
|---|---|
| "scroll down", "scroll up a little", "scroll down fast" | One push of scrolling |
| "scroll this page slowly", "keep scrolling down" | Scrolls until you say "stop" |
| "change the tab", "new tab", "close tab" | Browser tabs |
| "open google chrome", "open notes" | Opens an installed application |
| "click", "double click", "copy", "paste", "undo" | Mouse and editing |
| "play", "next song", "volume up", "mute" | Media |
| "stop everything", "resume" | Emergency stop and resume |

### Assistant (free requests)
With **Understand free requests** on, you can ask more freely after the wake word:

| You say | It does |
|---|---|
| "open YouTube and search for *song*" | Opens YouTube with the search results |
| "open Flipkart", "open Gmail", "open amazon dot com" | Opens the site; nothing needs installing |
| "open Flipkart and search for shoes" | Goes to that site's search results |
| "search Google for *anything*" | A Google search |
| "write a mail about *topic*" | The AI writes subject and text and opens it as a draft |
| "write a paragraph about *topic*" | The AI writes it and pastes it at your cursor |
| "type *these words*" | Pastes exactly what you said |
| "what is …?", "explain …" | Shows the answer and reads it aloud |

About 50 sites are known by name, and close misspellings by the speech recogniser still
match. Anything else you ask it to open is searched for on Google.

### Training your own gesture
1. Open **Training Studio**. Gesture control is switched off while you are there.
2. Create a gesture and give it a name.
3. Press **Start**, hold the pose and move your hand a little. Aim for 40 samples.
4. Press **Train model**. You get accuracy, precision, recall and a confusion matrix.
5. Map the new gesture to an action in **Gesture Studio**.

The last five models are kept, so a worse one can be reverted.

### Your own keyboard shortcuts and applications
- **System Controls → Keyboard shortcuts:** name a combination such as `Cmd+Tab`, then
  map it to a gesture.
- **System Controls → Applications:** pick an application, then map "Open …" to a gesture.

### Pointer calibration
**System Controls → Pointer calibration:** show the top-left and bottom-right of the
area your hand comfortably covers. The cursor then spans the whole screen inside it.

### Learn: the guided lesson
Open **Learn**. Each step shows a gesture, how to make it and what it does in the active
profile, next to the camera preview. It completes by itself once the camera sees it
("Well done!") and moves on. Practice mode is on for the whole lesson. Skip, go back or
jump to any step; switching profile rebuilds the lesson.

### Face Control (hands-free)
1. Open **Face Control** and switch on **Use Face Control**. The face model (about 4 MB)
   is downloaded once. Hand gestures are off while it is on.
2. Move your head to move the cursor. The default **Like a mouse** style moves a little
   for slow movements and far for quick ones; **Point with your head** is the alternative.
3. Expressions and their default actions:

| Expression | Default action |
|---|---|
| Wink left eye | Click; two winks = double-click |
| Raise eyebrows | Right click |
| Pucker lips | Double-click (opens files and folders) |
| Smile | Minimise the window in front |
| Open mouth | Scroll mode on / off: look up or down to scroll |
| Both eyes closed about a second | Pause / resume |

Every expression has an action menu, a live bar and its own sensitivity. The cursor
holds still while an expression is made, so clicks land on target. Ordinary blinks are
ignored.

### JARVIS mode and the Holo Desk
1. **Settings → Theme → JARVIS (cyan HUD)**, and the **JARVIS** profile in the header.
2. Open **Holo Desk**: camera preview, a Current Gesture ring, System Controls, and
   panels - Files, Notes, Music, System Info (CPU, memory, battery) and Clock.
3. **Move a panel:** point at its title bar, pinch and hold, move, open your fingers.
   **Resize** by the ◢ corner. Panels glide, snap to a grid, keep their place between
   sessions, and are dropped in place if the hand is lost. **Reset layout** restores them.

Drag is switched on automatically while the Holo Desk is open.

### Virtual keyboard
Open it from the Holo Desk or its dock, or map **Show / Hide Keyboard** to a gesture.
Point at a key (it lights up) and pinch. The pinch is never delivered to the keyboard
window as a click, so the key is typed into **the application you are using**. Letters,
numbers, Space, Enter, Backspace, Shift and punctuation; one pinch types one key.

### Moving windows and switching desktops
Map **Move Window**, **Resize Window**, **Window to Left / Right Half**, **Maximize
Window**, **Center Window**, **Next / Previous Desktop** or **Mission Control** to
gestures. Window actions use the macOS Accessibility API on the front application's
window; full-screen windows and some system windows cannot be moved. Desktop switching
needs System Settings → Keyboard → Keyboard Shortcuts → Mission Control → "Move left /
right a space" switched on.

### Controlling an iPhone
macOS's **iPhone Mirroring** app shows the iPhone's screen in a window that is
controlled with the Mac's mouse and keyboard - so TouchlessX gestures control the
iPhone: pinch taps, a held pinch swipes, the virtual keyboard types. It needs iOS 18 or
newer, the same Apple ID on both, the iPhone nearby and locked, and a region where Apple
offers the feature. TouchlessX itself does not run on the iPhone.

### Smart home
TouchlessX controls devices through a home hub, never directly:

1. In the hub, create a webhook that switches the device - for example a Home Assistant
   automation with a **Webhook** trigger, an IFTTT Webhooks applet, or a Node-RED
   "http in" node.
2. In **Smart Home**, add the device's name, the webhook URL and a token if needed;
   **Test** it.
3. Switch Smart Home on (dashboard) and map the device to a gesture.

By default the command is only sent after a thumbs-up confirmation. The webhook is a
one-way trigger: TouchlessX does not know the device's state. Devices that only work
through a manufacturer's app (for example Xiaomi Mi Home cameras) need a hub that
supports them first, such as Home Assistant with its Xiaomi integration; what can be
controlled then depends on the model.

### Menu-bar icon, start at login and updates
All under **Settings → Startup and updates**:

- **Menu-bar icon** (macOS): a ✋ in the menu bar to show the window, pause, stop the
  camera, switch voice on or off, or quit. It turns into ⏸ while control is paused.
- **Keep running when the window is closed:** the close button then only hides the window.
- **Start TouchlessX when I log in:** adds a per-user login entry.
- **Check for updates at start:** asks GitHub whether a newer release exists and, if
  so, shows an Update button that opens the download page. Nothing installs by itself.

### Building the Mac app
```bash
pip install pyinstaller
sh scripts/build_macos.sh
```
This produces `dist/TouchlessX.app` and a `dist/TouchlessX-<version>.dmg` installer
image. The packaged app keeps its settings in `~/Library/Application Support/TouchlessX`.

Who can install the result: Macs with an Apple chip (M1 or later) running macOS 14 or
newer. It does not run on Intel Macs or on Windows; each needs its own build made on
that kind of computer. It has only been run on the Mac it was built on.

It is not signed with a paid Apple certificate, so macOS blocks it the first time it is
opened. To allow it: try to open the app, then go to System Settings → Privacy &
Security, scroll down, and press **Open Anyway**. After that it opens normally. The
user must then allow the camera (and microphone, for voice) and add TouchlessX under
Accessibility. The AI assistant additionally needs Ollama installed on that Mac.

### Changing how it looks
**Settings → Appearance:** dark, JARVIS (deep navy with cyan edges) or light theme, and
an accent colour from eight presets or any colour you pick. The theme changes at once.

---

## 10. Safety

| Control | What it does |
|---|---|
| **EMERGENCY STOP** button | Stops all gesture and voice control at once. Works with no hand in view |
| Space or Esc | Pauses control while TouchlessX is the front window |
| Emergency stop gesture | One gesture of your choice that stops everything in every profile |
| "Computer, stop everything" | The same, by voice |
| Lock / unlock | In the GestureAI profile a fist locks control and a thumbs-up unlocks |
| Fist / both palms | In the JARVIS profile a fist is an emergency stop and both palms resume |
| Long blink | In Face Control, both eyes closed for about a second pause and resume |
| Tracking lost | A drag in progress is released, so nothing stays held down |
| Practice mode | Gestures and voice are recognised and shown, but nothing is sent |
| Mini controller | A small always-on-top window with a Pause button |
| Stop Camera | Turns tracking off completely |

The emergency stop also blocks voice commands, except "resume".

---

## 11. Screens

| Screen | Contents |
|---|---|
| Dashboard | Live preview, current gesture, confidence, last action, feature switches, recent actions, analytics, system status |
| Holo Desk | JARVIS workspace: camera, gesture ring, system controls, movable panels, dock, gesture cards |
| Learn | The guided lesson |
| Gesture Studio | Practice area, gesture → action mapping, per-gesture on/off and tuning, sensitivity |
| Training Studio | Record, train and evaluate your own gestures |
| System Controls | Cursor, scrolling, drag, shortcuts, applications, calibration, permission status |
| Media & Presentation | Media and slide switches, test buttons, compatibility notes |
| Smart Home | Webhook devices and confirmation |
| AI Insights | Usage, recognition quality, suggestions, model history |
| Voice | Voice and assistant switches, what was heard, what you can say |
| Face Control | Hands-free mode: expressions, actions, sensitivity, cursor style, live meters |
| Clipboard | Text gestures and clipboard history |
| Profiles | Manage, import and export profiles |
| Settings | Appearance (theme, accent), camera, controlling hand, emergency stop gesture, startup and updates, privacy and data |

---

## 12. Privacy and data

- Camera frames are processed in memory and discarded. No picture or video is saved.
- Voice audio is processed in memory and discarded. Speech without the wake word is
  never transcribed.
- The AI model runs on your computer. Requests are not sent to an online service.
- There is no telemetry and no account.

| File | What it holds |
|---|---|
| `config/settings.json` | Your settings |
| `config/profiles.json` | Your profiles |
| `data/history.db` | Time, gesture, action and confidence of each action |
| `data/gesture_samples.csv` | Landmark numbers for gestures you trained |
| `data/models/` | Your trained gesture models |
| `logs/actions.log`, `logs/frames.csv` | Diagnostic logs: actions, and hand landmark points |

None of these are published with the code. History and logs can be switched off and
cleared in Settings.

The only network use is: one-time model downloads (hand, face, speech), the update
check (can be switched off), smart-home webhooks you add yourself, and opening the web
pages you ask for.

---

## 13. Technology

| Purpose | Library |
|---|---|
| Camera | OpenCV |
| Hand and face landmarks | MediaPipe Hand Landmarker and Face Landmarker (pinned to 0.10.21 or older) |
| Window management | macOS Accessibility API through PyObjC |
| Maths | NumPy |
| Mouse and keyboard | PyAutoGUI, plus macOS Quartz and Accessibility |
| Interface | CustomTkinter |
| Gesture training | scikit-learn |
| Wake word and commands | Vosk |
| Free speech-to-text | faster-whisper |
| Local AI | Ollama with `llama3.2:3b` |
| Storage | SQLite, JSON, CSV |
| Tests | pytest |

MediaPipe is pinned because newer versions bundle a usage-statistics uploader that
contacts Google.

---

## 14. Project structure

```
TouchlessX/
├── app.py                    Entry point
├── requirements.txt          Dependencies
├── TouchlessX.spec           Build description for the packaged app
├── scripts/build_macos.sh    Builds the Mac app and installer image
├── pyproject.toml
├── README.md
├── config/                   Your settings (created on first run)
├── docs/
│   ├── PROJECT_DOCUMENTATION.md   This file
│   ├── USER_GUIDE.md              Detailed how-to for users
│   ├── QA_CHECKLIST.md            Manual test list
│   ├── DEMO_VIDEO_SCRIPT.md       Shot list for a demo video
│   └── screenshots/
├── src/
│   ├── camera.py             Webcam frames
│   ├── hand_tracker.py       Hand landmarks
│   ├── gesture_engine.py     Gesture recognition
│   ├── face_control.py       Face tracking, head pointer, expressions
│   ├── tutorial.py           The Learn page's lesson
│   ├── window_manager.py     Move, resize and snap other apps' windows
│   ├── virtual_keyboard.py   Keyboard layout and the key under the cursor
│   ├── system_stats.py       CPU, memory and battery figures
│   ├── custom_gestures.py    Landmark features for trained gestures
│   ├── training.py           Dataset, training, evaluation, model versions
│   ├── action_controller.py  Gesture → action, cursor filter, scrolling, OS input
│   ├── safety.py             Pause, lock, confidence check
│   ├── profiles.py           Profiles and import validation
│   ├── voice.py              Wake word, speech recognition, command parser
│   ├── assistant.py          Free requests and the local AI
│   ├── clipboard.py          Clipboard history
│   ├── smart_home.py         Webhooks
│   ├── history.py            Action history (SQLite)
│   ├── action_log.py         Logs, session statistics, suggestions
│   ├── config.py             Default settings, load and save
│   ├── colors.py             Accent colour
│   ├── system.py             Data folder, start at login, update check, menu-bar icon
│   ├── ui.py                 Main window, dashboard, background threads
│   ├── ui_pages.py           Most other screens
│   ├── ui_holo.py            The Holo Desk
│   ├── ui_hud.py             HUD ring gauges
│   ├── ui_keyboard.py        The virtual keyboard window
│   └── ui_widgets.py         Colours and widget helpers
└── tests/                    333 automated tests
```

---

## 15. Testing

**Automated (333 tests):** gesture rules, two-hand and one-hand zoom, debouncing and
hold times, cursor mapping, scrolling styles, double-click and drag, lock and emergency
stop, profiles and import validation, keyboard shortcuts, training and model versions,
voice commands, the assistant's understanding of requests, clipboard history, the Learn
lesson, Face Control (head steering, winks, expressions, scroll mode, pause), the virtual
keyboard, window and desktop actions, the JARVIS profile and theme, system statistics,
configuration. They need no
camera, microphone or network.

**Checked in other ways:**

| What | How it was checked |
|---|---|
| Recognition fixes | By replaying recorded real hand movement through the new rules |
| Voice commands | Synthesized speech in two voices: 61 of 62 understood |
| Assistant requests | Synthesized speech: 14 of 14 became the right kind of task |
| Fresh install | Installed from `requirements.txt` into an empty folder; tests and first run passed |
| Minimise / restore | On a real window on macOS |
| Packaged Mac app | Built, then its self-test and window were run from the built app |
| Menu-bar icon, start at login, update check | Driven through the running app; the update check was run live against GitHub |
| Learn, Face Control and Holo Desk pages | Opened in the running app with a stand-in camera; panel drag and resize driven with simulated mouse events |

**Not measured:** accuracy with different people, faces, lighting, cameras or accents;
Face Control, the virtual keyboard and window moving with a real person; anything on
Windows. `docs/QA_CHECKLIST.md` lists the manual tests for that.

---

## 16. Known limitations

- Works for one hand and one person at a time.
- Recognition depends on lighting, distance and hand angle. No accuracy figure is claimed.
- Windows support is written but untested. Linux is not supported for voice-opened apps.
- The assistant opens pages and writes text. It cannot click through a web page, see
  the screen, or send a mail by itself.
- The local AI model is small: its writing is plain and its facts can be wrong.
- Speech recognition often misspells unusual names such as song titles.
- The pause shortcut (Space / Esc) only works while TouchlessX is the front window.
- The Mac app build is not code-signed, so macOS shows a warning the first time. There
  is no Windows build yet, and the menu-bar icon is macOS only.
- The update check tells you about a new version; it does not install it.
- Face Control has been tested with synthetic face data; how well each expression works
  differs from face to face. Glasses with reflections and dim light make it less reliable.
- Window moving and snapping are macOS only and do not work on full-screen windows.
- Other applications' windows cannot be shown inside the Holo Desk; they are moved on
  the real desktop instead.
- Not for vehicles, medical devices, machinery or anything safety-critical.

### Not implemented

- Code signing and notarisation (needs a paid Apple or Microsoft certificate).
- Fully automatic installation of updates.
- Licensing or paid editions.
- Cloud accounts or sync.
- A Windows installer and a Windows system-tray icon.
- Switching profiles automatically by application.
- A direct Home Assistant connection (device list and state); smart home works through
  webhooks.
