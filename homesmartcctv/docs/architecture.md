# Architecture

## Data model

```
users
  id, name, email (unique), password_hash, created_at, updated_at

camera_devices
  id, user_id (FK -> users), device_name, device_uuid (unique),
  platform (ANDROID|IOS), status (ONLINE|OFFLINE), camera_status (IDLE|STREAMING),
  last_seen, battery_level, network_status (WIFI|CELLULAR|DISCONNECTED),
  created_at, updated_at

camera_events
  id, camera_device_id (FK -> camera_devices), event_type, metadata (JSONB),
  timestamp, created_at
```

A user can own multiple devices (the schema never assumed a single camera, even though V1's UI mostly shows one). Events cascade-delete with their device; devices cascade-delete with their user.

## Online/offline detection

A device can only send a heartbeat while it has connectivity — the backend can never be *told* the instant a device goes offline, only notice staleness. `app/services/device_status.py` treats a device as online if `last_seen` is within `HEARTBEAT_OFFLINE_THRESHOLD_SECONDS` (default 60s, roughly 2× the mobile app's 25s heartbeat interval), and re-checks that on every read (`GET /devices`, `GET /devices/{id}`) and on every heartbeat. When the computed state flips, it's persisted and exactly one `DEVICE_ONLINE`/`DEVICE_OFFLINE` transition event is recorded — never one per request, so a flaky connection reconnecting in a loop doesn't flood the event history with duplicates. The same dedup principle applies to `LOW_BATTERY` (fires once on crossing the threshold, not every heartbeat) and network-status-change events.

## WebRTC signaling

The backend is a **dumb relay** — it never sees decoded video, only SDP offers/answers and ICE candidates, and only while actively relaying them between exactly one camera connection and one viewer connection per device (`app/services/signaling.py`, `SignalingManager`). This keeps the "no video storage" requirement structurally true rather than just policy.

Protocol (`app/api/routes/devices.py`, `@router.websocket("/{device_id}/signal")`):

1. Client connects to `wss://<host>/api/devices/{device_id}/signal?token=<JWT>&role=camera|viewer`. The JWT is validated and device ownership is checked *before* `websocket.accept()` — an unauthorized or wrong-owner connection is rejected at the handshake, not after.
2. On connect, if the other role is already present, both sides get a `{"type": "peer-joined", "role": ...}` message. This is what lets a Viewer that opens the Live Camera screen *before* the camera has started still work correctly — it just waits for that message.
3. The **viewer is always the WebRTC offerer**: on `peer-joined` (role camera), it creates an `RTCPeerConnection`, adds `recvonly` audio+video transceivers, and sends `{"type": "offer", "sdp": ...}`.
4. The **camera is always the answerer**: on receiving an offer, it creates its own `RTCPeerConnection`, attaches its already-captured local media tracks, and sends back `{"type": "answer", "sdp": ...}`.
5. ICE candidates flow both directions as `{"type": "ice-candidate", "candidate", "sdpMid", "sdpMLineIndex"}`, relayed verbatim.
6. A second viewer connecting to the same device sends the first viewer `{"type": "replaced"}` and closes its socket — V1 supports one active viewer per device, not fan-out to many.
7. On disconnect, the remaining peer gets `{"type": "peer-left", "role": ...}`.

ICE server configuration (currently just a public STUN server) is served from `GET /api/webrtc/ice-servers` rather than hardcoded client-side, so a TURN server can be added later purely as a backend config change — no app update needed.

## Why camera capture moved from CameraX to react-native-webrtc

Phase 2 built a small native Android module (`CameraDeviceModule` + a `CameraPreviewViewManager`) using CameraX, purely for a local preview — there was no streaming yet. Phase 3 added real streaming via `react-native-webrtc`, which needs to own the camera itself (`getUserMedia`) to capture and encode frames for transmission. Android's Camera2 API only allows one consumer of a given camera device at a time, so running CameraX's preview and WebRTC's capture concurrently would fail with a "camera in use" conflict. Rather than keep two competing camera pipelines, Phase 3 consolidated onto `react-native-webrtc` for both local preview (via `RTCView`) and the actual stream — the same `MediaStream` backs both. What's left of the original native module is the pieces `react-native-webrtc` doesn't provide: the foreground-service notification that keeps the app's process alive while streaming with the screen off, and reading device uptime (`SystemClock.elapsedRealtime()`, not otherwise exposed to JS).

## Mobile app structure

- `src/webrtc/` — `signalingClient.ts` (thin `WebSocket` wrapper, no auto-reconnect by design: a dropped connection surfaces as a disconnected state and the user restarts, which is simpler and more predictable than resuming a half-negotiated peer connection), `useCameraPeerConnection.ts` (answerer lifecycle), `useViewerPeerConnection.ts` (offerer lifecycle), `rtcEvents.ts` (typed event-listener helpers working around a TypeScript resolution quirk in `react-native-webrtc`'s RN-condition source path — see the comment there for detail).
- `src/screens/CameraDeviceScreen.tsx` — device registration, heartbeat loop, Start/Stop Camera (permissions → `useCameraPeerConnection` → backend status update).
- `src/screens/ViewerDashboardScreen.tsx` / `LiveCameraScreen.tsx` / `EventHistoryScreen.tsx` / `CameraSettingsScreen.tsx` — the Viewer-side screens, all polling or fetching from the REST API; only `LiveCameraScreen` touches WebRTC.
- `android/app/src/main/java/com/smarthomecamera/camera/` — `CameraDeviceModule.kt` (foreground-service start/stop, uptime), `CameraKeepAliveService.kt` (the actual foreground service + notification).
