# Smart Home Camera

Turn an old Android phone into a home security camera you can view live from your current phone, anywhere — no subscription, no cloud video storage, built on open-source WebRTC.

## Overview

Smart Home Camera has two ends of the same mobile app plus a lightweight backend:

- **Camera Device mode** (the old phone, staying at home): registers itself, sends periodic heartbeats, and streams live video+audio over WebRTC when you press Start Camera.
- **Viewer mode** (your current phone): shows whether the camera is online, its battery/network/last-seen status, a live view, and an event history.
- **Backend** (FastAPI + PostgreSQL): handles accounts, device registry, online/offline detection, event history, and WebRTC signaling. It never stores or streams video itself — video goes peer-to-peer between the two phones.

```
Old Android Phone (Camera)          Current Phone (Viewer)
        |                                    |
        |-- REST: register/heartbeat ------->|  \
        |-- WebSocket: signaling ----------->|   } FastAPI + PostgreSQL backend
        |                                    |  /
        |<======= WebRTC (peer-to-peer video/audio) =======>|
```

## Features (V1)

- Email/password auth (JWT), passwords hashed with bcrypt
- Register one or more camera devices to your account
- Live online/offline status, derived from a heartbeat every 25s (not a naive "always online" flag)
- Start/stop the camera remotely from the device itself; status and Start/Stop propagate to the Viewer
- Live video+audio streaming over WebRTC — encrypted peer-to-peer, not relayed or stored by the backend
- Event history (device online/offline, camera started/stopped, low battery, network changes)
- A polished, dark, professional UI on both the Camera Device and Viewer sides

**Not in V1** (see [Future Roadmap](#future-roadmap)): AI/object detection, family-member tracking, analytics pipelines, Kafka/Spark, TURN relay (STUN only — see [SECURITY.md](SECURITY.md#nat-traversal--turn)).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full technical write-up (data model, online/offline detection logic, WebRTC signaling protocol, and the Phase 2→3 decision to consolidate camera capture onto `react-native-webrtc`).

## Technology stack

**Backend:** Python, FastAPI, PostgreSQL, SQLAlchemy 2.0, Alembic, Pydantic v2, PyJWT, bcrypt, pytest.

**Mobile:** React Native 0.87 (TypeScript), React Navigation, `react-native-webrtc`, `react-native-device-info`, `@react-native-community/netinfo`, `@react-native-async-storage/async-storage`. Camera-device-specific native Android code (Kotlin) handles the foreground-service notification that keeps streaming alive while backgrounded.

## Project structure

```
smart-home-camera/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # auth, users, devices, webrtc (+ signaling WebSocket)
│   │   ├── core/             # config, security (JWT/bcrypt), logging
│   │   ├── db/                # SQLAlchemy engine/session
│   │   ├── models/            # User, CameraDevice, CameraEvent
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── services/          # device online/offline logic, signaling relay
│   │   └── main.py
│   ├── alembic/               # DB migrations
│   ├── tests/                 # pytest suite (25 tests)
│   ├── requirements.txt / requirements-dev.txt
│   ├── Dockerfile
│   └── .env.example
├── mobile/
│   ├── android/                       # native Android project + Kotlin camera module
│   ├── src/
│   │   ├── screens/                   # RoleSelect, Login, Register, CameraDevice,
│   │   │                              # ViewerDashboard, LiveCamera, EventHistory, CameraSettings
│   │   ├── webrtc/                     # signaling client + camera/viewer peer connection hooks
│   │   ├── api/, storage/, hooks/, utils/, theme/, components/, types/
│   │   └── navigation/
│   └── package.json
├── docs/
│   ├── architecture.md
│   ├── api.md
│   └── development.md
├── docker-compose.yml
├── SECURITY.md
└── README.md
```

## Local setup

### Prerequisites

- Node.js ≥ 22.11, npm
- Python 3.11+ (developed/tested on 3.14)
- PostgreSQL (via Homebrew, a system install, or Docker — see below)
- Android Studio + SDK, an emulator or a physical Android device, for the mobile app
- On macOS: `sudo xcodebuild -license accept` must be run once before Homebrew/git will work at all, if you haven't already accepted the Xcode license.

### Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in real values (especially `JWT_SECRET` — generate one with `openssl rand -hex 32`, never use the placeholder in production):

```
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://smarthome:smarthome@localhost:5432/smarthome_camera
JWT_SECRET=<generate a real one>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
CORS_ORIGINS=http://localhost:19006,http://localhost:8081
HEARTBEAT_OFFLINE_THRESHOLD_SECONDS=60
LOW_BATTERY_THRESHOLD_PERCENT=15
STUN_SERVERS=stun:stun.l.google.com:19302
```

### Database setup

**Option A — Homebrew Postgres** (what this project was actually developed and tested against):

```bash
brew install postgresql@16
brew services start postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
createuser -s smarthome
psql -d postgres -c "ALTER USER smarthome WITH PASSWORD 'smarthome';"
createdb -O smarthome smarthome_camera
createdb -O smarthome smarthome_camera_test   # used by the pytest suite
```

**Option B — Docker Compose** (`docker-compose.yml` provisions Postgres 16 + the backend together). This file is written to the same spec as option A and the backend has no code path that differs by database source, but it has not been run on this development machine (Docker wasn't available in this environment) — validate it once before relying on it:

```bash
docker compose up
```

### Running the backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Backend: http://localhost:8000 — Swagger docs: http://localhost:8000/docs

### Running the backend tests

```bash
cd backend
source .venv/bin/activate
python -m pytest -v
```

25 tests covering registration, login, auth, device ownership, heartbeat, camera start/stop, event creation, unauthorized access, invalid input, and the WebRTC signaling relay (connect/reject/relay/replace).

### Running the mobile app

```bash
cd mobile
npm install
npm start          # Metro bundler
# in another terminal:
npm run android     # or: npx react-native run-android
```

The app talks to `http://10.0.2.2:8000/api` by default (the Android emulator's alias for the host machine's `localhost`). Editing `mobile/src/api/client.ts`'s `DEV_BASE_URL` to your machine's LAN IP is required to run against a real old Android phone on the same Wi-Fi network — `10.0.2.2` only works from an emulator.

### Camera-device setup

1. Install the app on the old phone.
2. On first launch, choose **Camera Device**.
3. Log in (or create an account).
4. Grant Camera and Microphone permission when prompted — both are requested only when you press **Start Camera**, not eagerly on launch.
5. Press **Start Camera**. The phone now streams whenever a Viewer connects, and shows a persistent notification (Android requirement for the foreground service that keeps it running with the screen off).

### Viewer-device setup

1. Install the app on your current phone.
2. Choose **Viewer**, log in with the **same account**.
3. Your registered camera(s) appear automatically on the dashboard, with live status.
4. **View Camera** opens the live feed; **Event History** and **Camera Settings** are also available per device.

### API documentation

Full interactive reference: `/docs` (Swagger UI) once the backend is running. A summary is also in [docs/api.md](docs/api.md).

## Security

See [SECURITY.md](SECURITY.md) for the full model: authentication, device-ownership authorization, why the signaling WebSocket uses a query-param token and how that's mitigated, video-privacy guarantees, and what's still missing before this should run beyond personal use (rate limiting, refresh tokens, TURN).

## Troubleshooting

- **`git`/`brew` refuse to run on macOS ("You have not agreed to the Xcode license")** — run `sudo xcodebuild -license accept` once.
- **Mobile app can't reach the backend** — on an emulator, use `10.0.2.2`, not `localhost`; on a real device, use your machine's LAN IP and make sure both devices are on the same network and the backend is bound to `0.0.0.0`, not `127.0.0.1`.
- **"Device already registered to another account" on re-registration** — the Android device's `getUniqueId()` is stable across app reinstalls; if you're testing with multiple accounts on the same device/emulator, delete the stale row from `camera_devices` or use a different device/emulator per account.
- **Live view stuck on "Waiting for camera to start streaming..."** — the camera device isn't currently connected to the signaling channel for that device; press Start Camera on the camera device first.
- **No real video on an emulator, just a black box or a synthetic 3D room** — that's the emulator's virtual camera (configure it in AVD Manager, or just test on a real device for a real feed).

## Future roadmap

Deliberately **not** built in V1 — documented here so scope stays honest, not because they're forgotten:

- **V2 — AI camera detection:** OpenCV/YOLO-based person/pet/package detection, event snapshots.
- **V3 — Data engineering:** camera events → ETL → Pandas/Parquet → analytics.
- **V4 — Family members:** per-person activity/check-in tracking, with explicit consent and controls.
- **V5 — Advanced data pipeline:** MQTT → Kafka/Redpanda → stream processing → data warehouse.
- **V6 — AI anomaly detection:** feature engineering over historical activity → ML model → assistive (never certainty-claiming) unusual-activity signals.

## Cost

Built to run at ~₹0: PostgreSQL, FastAPI, and every mobile dependency here are open-source and self-hosted; the only external service is Google's free public STUN server. The one place a paid service could become necessary is a TURN relay for restrictive networks — see [SECURITY.md](SECURITY.md#nat-traversal--turn) for the free (self-hosted coturn) vs. paid tradeoff.
