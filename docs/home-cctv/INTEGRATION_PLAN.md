# TouchlessSecurity — Phase 0 audit and integration plan

Audit date: 2026-10-17. Based on reading the code in this repository (copies of
TouchlessX `e58b31f` and HomeSmartCCTV `31778fd`) and running their tests. Scope for
now: **local only** (backend on the Mac, phones on the same Wi-Fi); TURN and
internet-facing deployment are out of scope until later.

## 1. Baseline (measured)

| Check | Result |
|---|---|
| TouchlessX tests (`touchlessx/`) | 333 passed |
| HomeSmartCCTV backend tests (PostgreSQL 16, isolated DB `touchless_security_test`) | 39 passed |
| Alembic `upgrade head` on an empty database | 4 migrations applied, head `5a75553d4db2` |
| Mobile Jest | 1 suite, 1 test passed |
| Mobile TypeScript (`tsc --noEmit`) | 0 errors |
| Android build on a device | Not run yet (SDK, adb and emulator are installed) |

## 2. TouchlessX audit

- Python 3.12 desktop app, CustomTkinter UI, ~9,800 lines in 28 modules.
- Camera → MediaPipe → `GestureEngine` → `SafetyGuard` → `ActionController` →
  `InputExecutor`, on a worker thread; voice and Face Control feed the same controller.
- Already present and reusable: confidence thresholds, cooldowns, hold times, pause,
  emergency stop, practice mode, **thumbs-up confirmation with expiry** (used for smart
  home), an allowlist of actions (no command execution), voice parser, profiles.
- Network today: `smart_home.py` (urllib POST, background thread, 4 s timeout).
- No HTTP client dependency; no credential store.

## 3. HomeSmartCCTV audit

**Backend** (FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, PyJWT, bcrypt, PostgreSQL):

| Area | What exists |
|---|---|
| Auth | Email/password register and login; one JWT type (24 h, HS256); `get_current_user` dependency |
| Authorization | `_get_owned_device`: device must belong to the user (404 / 403) |
| Devices | register (by `device_uuid`), list, get, heartbeat (battery, network, camera status), camera start / stop |
| Status | Online = heartbeat within 60 s, computed on read; one DEVICE_ONLINE / OFFLINE event per transition |
| Events | `camera_events` with enum type and JSONB metadata: started/stopped, online/offline, network lost/back, low battery, MANUAL_CAPTURE, PERSON_DETECTED |
| Media | **JPEG captures and 60 s MP4 segments are uploaded to the backend** and stored on its disk (`media/`), 3 and 2 days retention, served to the owner only |
| Signaling | WebSocket `/api/devices/{id}/signal?token=…&role=…`, in-memory, one camera + one viewer per device, relays any JSON message |
| Logging | uvicorn access log silenced so tokens in WebSocket URLs are not written |

**Mobile** (React Native 0.87, TypeScript, react-native-webrtc 124):

- Camera mode: the camera lifecycle lives in the React Native screen
  (`CameraDeviceScreen.tsx`); the Kotlin `CameraKeepAliveService` only holds a
  foreground notification (type `camera` on Android 14+).
- Heartbeat loop in the camera screen; viewer dashboard, live view, event history,
  video playlist and player, captured faces screen.
- Remote **camera flip** exists as an informal `switch-camera` message over the
  signaling socket (no acknowledgement).
- "Person detection" is **ML Kit face detection** on a still frame every 10 s
  (PixelCopy of the preview), 2 min cooldown, uploaded as PERSON_DETECTED.
- Native modules: `MediaCaptureModule` (PixelCopy), `VideoRecordingModule` (records
  straight from the native WebRTC video track — a usable frame source for AI later).
- Token in AsyncStorage (not encrypted). Dev API over plain HTTP on the LAN.

## 4. Existing versus missing

| Required | Status |
|---|---|
| Typed device commands with lifecycle, idempotency, expiry, ack, audit | **Missing** (only the fire-and-forget `switch-camera` message) |
| Audit log | **Missing** |
| TouchlessX ↔ camera API client | **Missing** |
| Health: heartbeat staleness | Present (online/offline) |
| Health: frame freshness, WebRTC state, service state, permission errors, recovery | **Missing** |
| Person detection | Partial: face detection only, 10 s still frames |
| Object categories, zones, dedup beyond a cooldown | **Missing** |
| Incident timeline (filters, stable incident IDs) | Partial: per-device event list, no incidents, no filters |
| Media retention and owner-only retrieval | Present (backend-side storage) |
| User-requested deletion, media status tracking | **Missing** |
| Privacy mode with desired / reported state | **Missing** |
| Separate device credentials, revocation, rate limiting | **Missing** |
| TLS | Missing (acceptable for local-only; required before any internet exposure) |

## 5. Architecture decisions

1. **Commands go through the backend, never straight from TouchlessX to the phone.**
   TouchlessX → `POST /api/devices/{id}/commands` → stored as `pending` → delivered to the
   camera over a new authenticated **device-control WebSocket**
   (`/api/devices/{id}/control`), separate from signaling, with **polling of
   `/commands/pending` as a fallback** → camera acknowledges, executes, reports the
   result → TouchlessX and the viewer read the status. No new infrastructure (no
   Redis / MQTT): one backend process, in-memory connection registry like signaling.
2. **Strict allowlist of command types**, each with a Pydantic parameter schema:
   Phase 1 — `refresh_status`, `switch_camera`, `capture_snapshot`;
   Phase 5 — `set_privacy`. Anything else is rejected with 422.
3. **Lifecycle**: `pending → delivered → acknowledged → succeeded | failed`, and
   `pending | delivered → expired | cancelled`. Transitions are validated server-side;
   terminal states are final. Default expiry 60 s. Idempotency key unique per user.
4. **TouchlessX** gets `security_client.py` (urllib, timeouts, background thread — never
   on the gesture thread) and `security_commands.py` (typed commands, policy:
   which need confirmation). Gestures and voice map to `security:<command>` actions; the
   existing thumbs-up confirmation and its expiry are reused. The token is kept in the
   **macOS Keychain**, never in settings files or logs.
5. **Media storage stays as it is (on the backend's disk)** unless decided otherwise —
   see question 1. The backend runs on the user's own Mac, so this is local storage,
   not cloud.
6. **Every phase ships with tests and an Alembic migration that only adds**; nothing
   existing is dropped or rewritten.

## 6. Phased plan with acceptance criteria

| Phase | Delivers | Accepted when |
|---|---|---|
| **1. Commands** | `device_commands` and `audit_logs` tables; command routes; control WebSocket + poll fallback; camera-side handler with ack/result; TouchlessX Security page (login, device list, status, command buttons) and gesture / voice mapping with confirmation; command status in the viewer app | Backend tests cover authorization, cross-user denial, idempotency, expiry, cancellation, invalid transitions; one end-to-end run: TouchlessX gesture → confirm → camera flips / reports status → viewer shows `succeeded` |
| **2. Diagnostics** | Health snapshots; frame-freshness probe from the native video track; WebRTC state reporting; recovery with retry limits and cooldown; health card in the viewer | Stale heartbeat, frozen frames and WebRTC failure each detected and shown separately; no restart loop in tests |
| **3. AI detection** | Native (Kotlin) detector on sampled frames from the video track, person first, freely licensed model; zones, threshold, dedup; metadata + optional snapshot | Measured latency / CPU / battery on the real phone documented; false-positive and miss counts on recorded test footage |
| **4. Incident Time Machine** | Incidents grouping events, filters by device / type / time, media status (available / expired / deleted), user deletion with retry | Filter and access-control tests; deletion works for online and offline devices |
| **5. Privacy mode** | Desired / reported state, `set_privacy` command, gesture confirmation, capture-off vs stream-off, manual recovery | "On" is shown only after the camera confirms; lost-ack, restart and offline cases tested |
| **6. Hardening** | Device credentials and revocation, rate limits, secure token storage on phones, TLS, TURN, backups, long-run tests | Security review checklist complete |

## 7. Risks

- **Shared account**: camera and viewer use the same user token today; a stolen camera
  phone has full account access. Phase 6 adds per-device credentials.
- **Plain HTTP on the LAN** and tokens in AsyncStorage: acceptable for local testing
  only; not for internet exposure.
- **Gesture ≠ authentication**: commands are authorized by the backend; gestures only
  trigger and confirm.
- **Camera lifecycle in React Native**: if the app process dies, streaming stops;
  diagnostics (Phase 2) must detect this rather than assume it cannot happen.
- **Real-device behaviour** (battery, heat, background limits, AI accuracy) can only be
  confirmed on the user's phones.

## 8. Open questions

1. **Media storage**: keep snapshots and clips on the backend's disk (current design,
   simplest, local to your Mac) or move them onto the camera phone as the original
   brief suggests (much more work, and clips become unavailable when the phone is off)?
   Recommendation: keep the current design.
2. **Test phones**: is a real Android phone available for the camera side, or should
   Phase 1 be verified with the Android emulator first?
