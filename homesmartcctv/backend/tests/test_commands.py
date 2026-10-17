import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.models.device_command import DeviceCommand


def _device(client, headers, device_uuid=None):
    response = client.post(
        "/api/devices/register",
        json={"device_name": "Living Room Camera", "device_uuid": device_uuid or f"dev-{uuid.uuid4().hex[:10]}",
              "platform": "ANDROID"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create(client, headers, device_id, command_type="REFRESH_STATUS", key=None, **extra):
    body = {"command_type": command_type, "idempotency_key": key or uuid.uuid4().hex, **extra}
    return client.post(f"/api/devices/{device_id}/commands", json=body, headers=headers)


@pytest.fixture()
def owner(client, register_and_login):
    session = register_and_login()
    session["device_id"] = _device(client, session["headers"])
    return session


def _age(db_session, command_id, seconds):
    """Moves a command's expiry into the past."""
    command = db_session.get(DeviceCommand, uuid.UUID(command_id))
    command.expires_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db_session.commit()


# ------------------------------------------------------------------ creating
def test_create_requires_auth(client, owner):
    response = client.post(f"/api/devices/{owner['device_id']}/commands",
                           json={"command_type": "REFRESH_STATUS", "idempotency_key": "abcdefgh12"})
    assert response.status_code in (401, 403)


def test_create_command(client, owner):
    response = _create(client, owner["headers"], owner["device_id"], "SWITCH_CAMERA",
                       parameters={"facing": "back"}, source="touchlessx")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["command_type"] == "SWITCH_CAMERA"
    assert body["parameters"] == {"facing": "back"}
    assert body["source"] == "touchlessx"
    assert body["expires_at"] > body["created_at"]


@pytest.mark.parametrize("body", [
    {"command_type": "RUN_SHELL", "idempotency_key": "abcdefgh12"},           # not on the allowlist
    {"command_type": "REFRESH_STATUS", "idempotency_key": "short"},           # key too short
    {"command_type": "REFRESH_STATUS", "idempotency_key": "has spaces!!"},    # key characters
    {"command_type": "REFRESH_STATUS", "idempotency_key": "abcdefgh12", "parameters": {"x": 1}},
    {"command_type": "SWITCH_CAMERA", "idempotency_key": "abcdefgh12", "parameters": {"facing": "up"}},
    {"command_type": "REFRESH_STATUS", "idempotency_key": "abcdefgh12", "unexpected": True},
    {"command_type": "REFRESH_STATUS", "idempotency_key": "abcdefgh12", "ttl_seconds": 1},
])
def test_invalid_commands_are_rejected(client, owner, body):
    response = client.post(f"/api/devices/{owner['device_id']}/commands", json=body, headers=owner["headers"])
    assert response.status_code == 422


def test_ttl_above_the_maximum_is_rejected(client, owner):
    response = _create(client, owner["headers"], owner["device_id"],
                       ttl_seconds=get_settings().command_max_ttl_seconds + 1)
    assert response.status_code == 422


def test_another_users_device_is_off_limits(client, owner, register_and_login):
    other = register_and_login()
    assert _create(client, other["headers"], owner["device_id"]).status_code == 403
    command_id = _create(client, owner["headers"], owner["device_id"]).json()["id"]
    assert client.get(f"/api/commands/{command_id}", headers=other["headers"]).status_code == 404
    for action in ("ack", "cancel"):
        response = client.post(f"/api/devices/{owner['device_id']}/commands/{command_id}/{action}",
                               headers=other["headers"])
        assert response.status_code == 403
    assert client.get(f"/api/devices/{owner['device_id']}/audit", headers=other["headers"]).status_code == 403


def test_a_command_must_belong_to_the_device_named_in_the_url(client, owner):
    second_device = _device(client, owner["headers"])
    command_id = _create(client, owner["headers"], owner["device_id"]).json()["id"]
    response = client.post(f"/api/devices/{second_device}/commands/{command_id}/ack", headers=owner["headers"])
    assert response.status_code == 404


# ------------------------------------------------------------------ idempotency
def test_a_repeated_request_returns_the_original_command(client, owner):
    first = _create(client, owner["headers"], owner["device_id"], key="gesture-12345")
    again = _create(client, owner["headers"], owner["device_id"], key="gesture-12345")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    listed = client.get(f"/api/devices/{owner['device_id']}/commands", headers=owner["headers"]).json()
    assert len(listed) == 1


def test_reusing_a_key_for_something_else_is_a_conflict(client, owner):
    _create(client, owner["headers"], owner["device_id"], key="gesture-12345")
    response = _create(client, owner["headers"], owner["device_id"], "CAPTURE_SNAPSHOT", key="gesture-12345")
    assert response.status_code == 409


def test_idempotency_keys_are_per_user(client, owner, register_and_login):
    other = register_and_login()
    other_device = _device(client, other["headers"])
    assert _create(client, owner["headers"], owner["device_id"], key="shared-key-1").status_code == 201
    assert _create(client, other["headers"], other_device, key="shared-key-1").status_code == 201


# ------------------------------------------------------------------ lifecycle
def test_full_lifecycle_pending_delivered_acknowledged_succeeded(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device, "CAPTURE_SNAPSHOT").json()["id"]

    pending = client.get(f"/api/devices/{device}/commands/pending", headers=headers).json()
    assert [c["id"] for c in pending] == [command_id] and pending[0]["status"] == "DELIVERED"

    ack = client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers)
    assert ack.json()["status"] == "ACKNOWLEDGED"
    assert client.get(f"/api/devices/{device}/commands/pending", headers=headers).json() == []

    result = client.post(f"/api/devices/{device}/commands/{command_id}/result", headers=headers,
                         json={"status": "SUCCEEDED", "result": {"event_id": "abc"}})
    body = result.json()
    assert body["status"] == "SUCCEEDED" and body["result"] == {"event_id": "abc"}
    assert body["delivered_at"] and body["acknowledged_at"] and body["completed_at"]
    assert client.get(f"/api/commands/{command_id}", headers=headers).json()["status"] == "SUCCEEDED"


def test_retried_ack_and_result_are_harmless(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    for _ in range(2):
        assert client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers).status_code == 200
    report = {"status": "FAILED", "error_code": "camera_not_streaming"}
    for _ in range(2):
        response = client.post(f"/api/devices/{device}/commands/{command_id}/result", headers=headers, json=report)
        assert response.status_code == 200 and response.json()["error_code"] == "camera_not_streaming"
    conflicting = client.post(f"/api/devices/{device}/commands/{command_id}/result", headers=headers,
                              json={"status": "SUCCEEDED"})
    assert conflicting.status_code == 409


def test_a_result_after_a_lost_ack_still_completes_the_command(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    response = client.post(f"/api/devices/{device}/commands/{command_id}/result", headers=headers,
                           json={"status": "SUCCEEDED"})
    assert response.json()["status"] == "SUCCEEDED" and response.json()["acknowledged_at"]


def test_result_reports_are_validated(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    url = f"/api/devices/{device}/commands/{command_id}/result"
    assert client.post(url, headers=headers, json={"status": "DONE"}).status_code == 422
    assert client.post(url, headers=headers, json={"status": "FAILED", "error_code": "Bad Code!"}).status_code == 422
    big = {"status": "SUCCEEDED", "result": {"blob": "x" * 5000}}
    assert client.post(url, headers=headers, json=big).status_code == 422


# ------------------------------------------------------------------ cancel and expiry
def test_cancel_before_the_camera_accepts(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    response = client.post(f"/api/devices/{device}/commands/{command_id}/cancel", headers=headers)
    assert response.json()["status"] == "CANCELLED"
    assert client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers).status_code == 409
    assert client.get(f"/api/devices/{device}/commands/pending", headers=headers).json() == []


def test_an_accepted_command_cannot_be_cancelled(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers)
    response = client.post(f"/api/devices/{device}/commands/{command_id}/cancel", headers=headers)
    assert response.status_code == 409


def test_an_unaccepted_command_expires(client, owner, db_session):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    _age(db_session, command_id, 1)
    body = client.get(f"/api/commands/{command_id}", headers=headers).json()
    assert body["status"] == "EXPIRED" and body["error_code"] == "expired"
    assert client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers).status_code == 409
    assert client.get(f"/api/devices/{device}/commands/pending", headers=headers).json() == []


def test_an_accepted_command_without_a_result_fails_after_the_grace_period(client, owner, db_session):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device).json()["id"]
    client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers)
    _age(db_session, command_id, 1)
    assert client.get(f"/api/commands/{command_id}", headers=headers).json()["status"] == "ACKNOWLEDGED"
    _age(db_session, command_id, get_settings().command_result_grace_seconds + 1)
    body = client.get(f"/api/commands/{command_id}", headers=headers).json()
    assert body["status"] == "FAILED" and body["error_code"] == "no_result"


def test_too_many_unfinished_commands_are_refused(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    for _ in range(get_settings().command_max_open_per_device):
        assert _create(client, headers, device).status_code == 201
    assert _create(client, headers, device).status_code == 429


# ------------------------------------------------------------------ audit
def test_every_step_is_audited_without_secrets(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    command_id = _create(client, headers, device, source="touchlessx").json()["id"]
    client.post(f"/api/devices/{device}/commands/{command_id}/ack", headers=headers)
    client.post(f"/api/devices/{device}/commands/{command_id}/result", headers=headers, json={"status": "SUCCEEDED"})
    entries = client.get(f"/api/devices/{device}/audit", headers=headers).json()
    assert [e["action"] for e in reversed(entries)] == ["command.created", "command.acknowledged", "command.completed"]
    assert {e["actor_type"] for e in entries} == {"user", "device"}
    assert all(e["command_id"] == command_id for e in entries)
    assert owner["token"] not in str(entries)


# ------------------------------------------------------------------ control channel
def test_control_channel_rejects_a_bad_token(client, owner):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/devices/{owner['device_id']}/control?token=nope") as ws:
            ws.receive_json()


def test_control_channel_rejects_another_users_device(client, owner, register_and_login):
    other = register_and_login()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/devices/{owner['device_id']}/control?token={other['token']}") as ws:
            ws.receive_json()


def test_control_channel_delivers_waiting_and_new_commands(client, owner):
    headers, device = owner["headers"], owner["device_id"]
    waiting = _create(client, headers, device).json()["id"]
    with client.websocket_connect(f"/api/devices/{device}/control?token={owner['token']}") as ws:
        first = ws.receive_json()
        assert first["type"] == "command" and first["command"]["id"] == waiting
        created = _create(client, headers, device, "SWITCH_CAMERA").json()
        assert created["status"] == "DELIVERED"  # pushed straight to the open connection
        pushed = ws.receive_json()
        assert pushed["command"]["id"] == created["id"]
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert client.get(f"/api/commands/{waiting}", headers=headers).json()["status"] == "DELIVERED"
