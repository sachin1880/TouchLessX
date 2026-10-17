# Home CCTV: TouchlessX + HomeSmartCCTV

The integration of **TouchlessX** (touchless desktop control) and **HomeSmartCCTV**
(old Android phone as a home security camera) into one touchless smart security
platform.

It began as a separate project (TouchlessSecurity) and now lives in the TouchlessX
repository, history included:

| Where | What | Started from |
|---|---|---|
| repository root | TouchlessX | TouchlessX at commit `e58b31f` |
| `homesmartcctv/` | HomeSmartCCTV backend and Android app | HomeSmartCCTV at commit `31778fd` (https://github.com/sachin1880/HomeSmartCCTV) |
| `docs/home-cctv/` | this overview, the integration plan and the phase notes | |

In these documents, `touchlessx/...` means the repository root and `docs/...` means
`docs/home-cctv/...`.

## Status

| Phase | State | Details |
|---|---|---|
| 0. Audit | Done | `docs/INTEGRATION_PLAN.md` |
| 1. Touchless command center | Built; tested end to end on a real Android phone | `docs/PHASE1_COMMANDS.md` |
| 2. Camera diagnostics (incl. phone temperature) | Next | |
| 3. AI person detection | Planned | |
| 4. Incident Time Machine (media stays on the Mac) | Planned | |
| 5. Gesture-secured privacy mode | Planned | |
| 6. Hardening (device credentials, TLS, TURN) | Planned | |
