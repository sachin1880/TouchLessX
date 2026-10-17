# Security Model

This document explains how Smart Home Camera protects your account, your camera, and your video feed, and what's still on you to configure correctly before relying on this in production.

## Authentication

- Passwords are hashed with **bcrypt** (`backend/app/core/security.py`) before storage. Plaintext passwords are never written to the database or to logs.
- Sessions are **JWT** access tokens (HS256), signed with `JWT_SECRET`. Tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 24h).
- Every authenticated REST endpoint validates the token via `Depends(get_current_user)` (`backend/app/api/deps.py`), which re-fetches the user from the database on every request rather than trusting token claims for anything beyond identity.
- The **WebSocket signaling endpoint is the one exception** to header-based auth: browsers and React Native's `WebSocket` client can't set custom headers on the handshake, so the JWT travels as a `?token=` query parameter instead. This is a known, accepted tradeoff for WebRTC signaling. Two mitigations are in place:
  - `uvicorn.access` logging is silenced (`backend/app/core/logging.py`), so this URL — token included — never gets written to the access log.
  - Tokens are short-lived and scoped to the same account-wide session as everything else; a leaked signaling URL is exactly as sensitive as a leaked bearer token, no more.

## Authorization (device ownership)

- Every device-scoped endpoint (`heartbeat`, `camera/start`, `camera/stop`, `events`, and the signaling WebSocket) checks that the authenticated user owns the device (`camera_devices.user_id == current_user.id`) before doing anything else. A mismatch returns `403`, not a data leak.
- Registering a `device_uuid` that's already claimed by a different account returns `409 Conflict` rather than silently reassigning it.

## Camera stream privacy

- The backend **never stores or relays video** — it only relays WebRTC signaling messages (SDP offers/answers, ICE candidates) between exactly one camera connection and one viewer connection per device. Actual video/audio travels peer-to-peer (or via a TURN relay you configure, see below), encrypted end-to-end by WebRTC's mandatory DTLS-SRTP — the backend's process memory never has access to the decrypted stream at all.
- A viewer can only receive a stream for devices they own (same ownership check as above), and only while a camera is actively connected to that device's signaling room.
- The signaling relay supports one active camera and one active viewer per device. A second viewer connecting sends the first viewer a `replaced` message and disconnects it — there's no scenario where two people can silently watch the same feed without each other's knowledge.

## Transport security

- **Local development runs over plain HTTP/WS**, which is fine on `localhost` / your LAN / the Android emulator's `10.0.2.2` loopback.
- **Production must run behind HTTPS/WSS.** Put the FastAPI backend behind a reverse proxy (nginx, Caddy, or your hosting provider's TLS termination) and update the mobile app's `API_BASE_URL` accordingly. Without TLS, the JWT (including the one in the signaling query string) and all API traffic are sent in the clear.
- WebRTC's own media transport (DTLS-SRTP) is encrypted regardless of whether your signaling channel is TLS — but an unencrypted signaling channel still exposes your JWT, so this isn't a substitute for HTTPS.

## NAT traversal / TURN

- ICE server config is served from `GET /api/webrtc/ice-servers`, currently just a public STUN server (`stun:stun.l.google.com:19302`, free, no signup). This lets most home networks connect peer-to-peer.
- Some networks (symmetric NAT, restrictive corporate/mobile networks) can't complete a direct P2P connection and need a TURN relay, which is **not included** in V1 to keep costs at ₹0. If you hit connection failures specifically on networks like that, the fix is to add a TURN server (self-hosted [coturn](https://github.com/coturn/coturn) is free; hosted TURN services charge for bandwidth) and add its URL to `STUN_SERVERS` in `backend/.env` — no app update needed, since clients fetch this list from the backend at connect time.

## Secrets and configuration

- No secrets are hardcoded anywhere in the codebase. `backend/.env.example` documents every required variable; the real `.env` is git-ignored.
- `JWT_SECRET` in `.env.example` is a placeholder and **must** be replaced with a long random value (`openssl rand -hex 32`) before any real deployment.
- CORS is restricted to the origins listed in `CORS_ORIGINS` — update this for your actual mobile app / web origins in production rather than leaving it permissive.

## Logging

- Request logs never include passwords or JWTs. Failed login attempts log the email only (useful for spotting brute-force attempts; an email address isn't a secret the way a password or token is).
- `uvicorn.access` (which would otherwise log full URLs, including the signaling endpoint's `?token=` query string) is suppressed at `WARNING` level; application-level logs use structured messages with IDs, not raw request bodies.

## Known limitations in V1

- **No rate limiting** on `/api/auth/login` or `/api/auth/register` yet. For a personal single-user deployment this is a low-severity gap, but it should be added (e.g. `slowapi`) before exposing the backend beyond your own devices.
- **No refresh tokens** — sessions simply expire after 24h and the user logs in again. Fine for a personal project; a refresh-token flow would be needed for a smoother multi-day session experience.
- **No account recovery** (password reset) flow exists yet.

None of these are exploitable data-leak or auth-bypass issues — they're missing hardening/convenience features appropriate to flag before this goes beyond personal use.
