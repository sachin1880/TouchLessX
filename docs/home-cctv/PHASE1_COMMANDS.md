# Phase 1 — Touchless command center

TouchlessX gesture or voice → confirmation → authenticated API request → backend
authorization → delivery to the camera phone → camera acknowledges and acts →
result stored in PostgreSQL → TouchlessX and the viewer app show the real outcome.

## What was built

| Part | Files |
|---|---|
| Backend: tables | `backend/app/models/device_command.py`, `audit_log.py`; migration `alembic/versions/b25d74671c39_add_device_commands_and_audit_log.py` |
| Backend: rules | `backend/app/services/commands.py` (lifecycle), `audit.py`, `control.py` (control socket registry) |
| Backend: API | `backend/app/api/routes/commands.py`, schemas `backend/app/schemas/command.py`, settings in `core/config.py` |
| TouchlessX | `touchlessx/src/cctv_commands.py` (allowlist), `cctv_client.py` (client + worker), `ui_cctv.py` (Home CCTV page); wiring in `action_controller.py`, `profiles.py`, `voice.py`, `ui.py`, `config.py` |
| Camera phone | `mobile/src/commands/commandProcessor.ts`, `useDeviceCommands.ts`, `api/commands.ts`; `CameraDeviceScreen.tsx`; `switchCamera` in `webrtc/useCameraPeerConnection.ts` |
| Viewer phone | `mobile/src/screens/CommandActivityScreen.tsx`, "Remote Commands" button on the dashboard |

## Commands (fixed allowlist)

| Command | Parameters | Confirmation from gesture / voice | Camera does |
|---|---|---|---|
| `REFRESH_STATUS` | none | no | sends a fresh heartbeat; result: battery, network, camera status |
| `SWITCH_CAMERA` | `facing`: `front` / `back` (optional; toggles if absent) | thumbs-up or "computer, confirm" | flips the live camera; result: `facing` |
| `CAPTURE_SNAPSHOT` | none | thumbs-up or "computer, confirm" | captures and uploads a photo (MANUAL_CAPTURE event); result: `event_id` |

Flip and snapshot fail with `camera_not_streaming` unless the camera is streaming.

## Lifecycle

```
PENDING ──► DELIVERED ──► ACKNOWLEDGED ──► SUCCEEDED | FAILED
   └────────────┴──► EXPIRED | CANCELLED
```

* Default expiry 60 s (max 300 s): not acknowledged in time → `EXPIRED`.
* Acknowledged but no result within expiry + 120 s → `FAILED` (`no_result`).
* Cancel only before the camera accepts. Terminal states never change.
* A repeated ack or result is accepted and changes nothing; any other invalid
  transition is `409`.
* At most 10 unfinished commands per camera (`429`).
* Expiry is applied when commands are read or written — no background job.

## API

All endpoints need `Authorization: Bearer <token>` and ownership of the device
(another user's device → `403`, another user's command → `404`).

| Method and path | Who | Notes |
|---|---|---|
| `POST /api/devices/{id}/commands` | owner | body `{command_type, parameters, idempotency_key, ttl_seconds?, source?}`; `201` new, `200` idempotent replay, `409` key reused for something else, `422` invalid, `429` too many open |
| `GET /api/devices/{id}/commands?limit=` | owner | newest first |
| `GET /api/commands/{command_id}` | owner | |
| `POST /api/devices/{id}/commands/{cid}/cancel` | owner | `409` once accepted |
| `GET /api/devices/{id}/commands/pending` | camera | not yet accepted; marks them delivered |
| `POST /api/devices/{id}/commands/{cid}/ack` | camera | |
| `POST /api/devices/{id}/commands/{cid}/result` | camera | `{status: SUCCEEDED|FAILED, error_code?, result?}`; result ≤ 4 KB |
| `GET /api/devices/{id}/audit?limit=` | owner | audit trail |
| `WS /api/devices/{id}/control?token=` | camera | push of new commands; `{"type":"ping"}` → `pong` |

`idempotency_key`: 8–64 characters `[A-Za-z0-9_-]`, unique per user. `error_code`:
`[a-z0-9_]{1,64}`. Unknown fields are rejected.

## Security rules in place

* Strict allowlist on every layer (backend enum + parameter schemas, TouchlessX
  `cctv_commands.py`, profile validation, voice table). No shell, script or free-form
  command exists anywhere in the path.
* Gestures never authenticate: they only trigger; the backend authorizes every call.
* Sensitive commands need the existing confirmation gesture; practice mode never sends.
* No network calls on the gesture thread; bounded retries reuse the idempotency key,
  so a retry can never make the camera act twice.
* TouchlessX keeps the session token in the macOS Keychain; the password is never
  stored. Plain `http://` is allowed only for local addresses.
* Audit entries hold command type, outcome and short codes only — no tokens, no media,
  no free text. The control socket's token is in its URL, and the backend's access log
  is silenced so it is never written to disk.

Known limits for this phase (planned for Phase 6): the camera and viewer use the same
user account; tokens on the phones are in AsyncStorage; local traffic is plain HTTP.

## Live view and snapshots in TouchlessX

Added after Phase 1, on the Home CCTV page:

* **Live view** (`touchlessx/src/cctv_live.py`): TouchlessX joins the backend's existing
  signaling relay as the `viewer`, exactly like the phone viewer app, and receives the
  camera's video over WebRTC (`aiortc`). Video only, never audio. Video goes straight
  from phone to Mac; the backend only relays the offer, answer and candidates. It runs
  on its own thread and stops when you leave the page.
* **One viewer per camera** (unchanged backend rule): watching in TouchlessX closes the
  phone viewer app's live view, and the other way round. The page shows "Closed".
* **Latest snapshot**: when *Take Snapshot* succeeds, TouchlessX downloads the photo
  (`GET /devices/{id}/captures/{event_id}/image`) and shows it. If the download fails,
  the command still counts as succeeded and the page says the photo couldn't be loaded.
* **Gallery** (`touchlessx/src/ui_cctv_gallery.py`): every snapshot, person detection
  and video clip the selected camera still has on the server, newest first, with
  filters, a large viewer (◀ ▶, Save…) and clips opened in the Mac's video player.
  Loaded on its own thread; photos are downloaded once and forgotten on sign-out.
  Backend support: `GET /devices/{id}/events?image_only=true`.
* **Retention is 2 days** for photos and clips (`CAPTURE_RETENTION_DAYS=2`,
  `VIDEO_RETENTION_DAYS=2`), and expired media is now also deleted when the history is
  read, not only when a new photo arrives - so it goes even while the camera is off.
* **One phone, several accounts**: a phone's registration is per account (unique on
  `user_id` + `device_uuid`, migration `f3c7a9e1b2d4`), so the same phone can be a
  camera on more than one account; accounts still never see each other's cameras.
* Known gap: continuous video recording is switched off on the phone (the native
  recorder writes uncompressed frames, 100-200 MB a minute). The gallery shows clips as
  soon as it is re-enabled with a real H.264 encoder.
* New dependencies: `aiortc`, `websockets` (in `requirements.txt` / `pyproject.toml`).
  Without them, everything else on the page works and the live view says what to install.

## Running it locally

1. **Backend** (`homesmartcctv/backend`, its own `.venv`, `.env` with a random JWT
   secret, database `touchless_security`):
   ```bash
   .venv/bin/alembic upgrade head
   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   `0.0.0.0` lets the phones on your Wi-Fi reach it.
2. **TouchlessX** (`touchlessx`, its own `.venv`): `.venv/bin/python app.py` →
   **Home CCTV** → server `http://localhost:8000/api` → sign in with the same account
   as the phones → pick the camera.
3. **Phones**: the app's `DEV_MACHINE_LAN_IP` (`mobile/src/api/client.ts`) must be the
   Mac's Wi-Fi address. Camera phone: Camera mode → Start Camera ("Remote Control:
   Connected"). Viewer phone: dashboard → **Remote Commands**.
4. Map gestures in Gesture Studio (e.g. ✌️ → *CCTV: Take Snapshot*), or say
   "computer, take snapshot" then "computer, confirm".

## Tests

| Suite | Command | Result |
|---|---|---|
| Backend | `DATABASE_URL=…/touchless_security_test .venv/bin/python -m pytest` | 67 passed (28 new) |
| Migration | `alembic upgrade head`, `downgrade -1`, `upgrade head`, `alembic check` | clean round trip, models match |
| TouchlessX | `.venv/bin/python -m pytest` | 372 passed (26 new, then 13 for live view and snapshots) |
| Mobile | `npx jest`, `npx tsc --noEmit`, `npx eslint` | 9 passed (8 new), 0 type errors, 0 lint errors |
| End to end (simulated camera) | real backend + real TouchlessX client + scripted camera on the control socket | 3 commands confirmed, delivered in 0.06 s, all succeeded, full audit trail |
| End to end (real phone) | Android 12 phone (M2010J19CI) on Wi-Fi + real backend + TouchlessX `CctvService` / `LiveView` | Flip Camera succeeded in 1.2 s (back→front→back); Take Snapshot succeeded in 0.6 s and the 62 KB photo downloaded; live view went live in 1.2 s at ~10 frames/s shown. Gesture → confirm → flip from the app UI: pending |

## Rollback

* Code: `git revert` the Phase 1 commits in this repository (the originals are untouched).
* Database: `.venv/bin/alembic downgrade 5a75553d4db2` removes the two new tables and
  their enum types; no existing table is changed by this phase.
