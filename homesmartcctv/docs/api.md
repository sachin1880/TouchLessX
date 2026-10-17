# API Reference

The full interactive reference — request/response schemas, try-it-out — is served by FastAPI itself at `/docs` (Swagger UI) and `/redoc` once the backend is running. This page is a quick summary; treat `/docs` as the source of truth.

All endpoints below except `/api/health`, `/api/auth/register`, and `/api/auth/login` require `Authorization: Bearer <token>`.

## Auth

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create an account. Body: `name`, `email`, `password` (min 8 chars). |
| POST | `/api/auth/login` | Returns `{access_token, token_type}`. |
| GET | `/api/users/me` | Current user's profile. |

## Devices

| Method | Path | Description |
|---|---|---|
| POST | `/api/devices/register` | Register (or re-register/update) a camera device. Upserts by `device_uuid`; 409 if that UUID belongs to a different account. |
| GET | `/api/devices` | List devices owned by the current user, with live-computed `is_online`. |
| GET | `/api/devices/{device_id}` | Single device, ownership-checked (403 if not yours). |
| POST | `/api/devices/{device_id}/heartbeat` | Body: optional `battery_level`, `network_status`, `camera_status`. Updates `last_seen` and may emit `DEVICE_ONLINE`/`LOW_BATTERY`/network-change events. |
| POST | `/api/devices/{device_id}/camera/start` | Marks `camera_status = STREAMING`, emits `CAMERA_STARTED`. |
| POST | `/api/devices/{device_id}/camera/stop` | Marks `camera_status = IDLE`, emits `CAMERA_STOPPED`. |
| GET | `/api/devices/{device_id}/events?limit=50` | Event history, newest first (limit capped at 200). |
| WS | `/api/devices/{device_id}/signal?token=<jwt>&role=camera\|viewer` | WebRTC signaling relay. See [architecture.md](architecture.md#webrtc-signaling) for the message protocol. |

## WebRTC

| Method | Path | Description |
|---|---|---|
| GET | `/api/webrtc/ice-servers` | `{"ice_servers": [{"urls": "stun:..."}]}` — fetch before creating an `RTCPeerConnection`. |

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | `{"status": "ok"}`, unauthenticated. |

## Error responses

Standard FastAPI/Pydantic shape: `{"detail": "..."}` for a single error, or a validation-error array for `422`s. Common codes used deliberately across this API:

- `401` — missing/invalid/expired token.
- `403` — authenticated, but you don't own the resource.
- `404` — resource doesn't exist (never returned instead of 403 to avoid confusing "not yours" with "doesn't exist" — both are checked explicitly).
- `409` — conflict (duplicate email on register, `device_uuid` claimed by another account).
- `422` — request body failed validation (e.g. `battery_level` outside 0–100).

## Event types

`CAMERA_STARTED`, `CAMERA_STOPPED`, `DEVICE_ONLINE`, `DEVICE_OFFLINE`, `NETWORK_DISCONNECTED`, `NETWORK_RECONNECTED`, `LOW_BATTERY`. AI-derived event types (`PERSON_DETECTED`, etc.) are V2 — not in this schema yet.
