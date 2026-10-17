def _register_device(client, headers, device_uuid="device-abc-123"):
    return client.post(
        "/api/devices/register",
        json={"device_name": "Living Room Camera", "device_uuid": device_uuid, "platform": "ANDROID"},
        headers=headers,
    )


def test_register_device_requires_auth(client):
    response = _register_device(client, headers={})
    assert response.status_code in (401, 403)


def test_register_device(client, register_and_login):
    session = register_and_login()
    response = _register_device(client, session["headers"])
    assert response.status_code == 201
    body = response.json()
    assert body["device_name"] == "Living Room Camera"
    assert body["is_online"] is False
    assert body["camera_status"] == "IDLE"


def test_list_devices_only_returns_own_devices(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    _register_device(client, owner["headers"], device_uuid="device-owner-1")
    _register_device(client, other["headers"], device_uuid="device-other-1")

    response = client.get("/api/devices", headers=owner["headers"])
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["device_uuid"] == "device-owner-1"


def test_device_ownership_enforced(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    created = _register_device(client, owner["headers"], device_uuid="device-owned-by-owner").json()

    response = client.get(f"/api/devices/{created['id']}", headers=other["headers"])
    assert response.status_code == 403


def test_get_nonexistent_device_returns_404(client, register_and_login):
    session = register_and_login()
    response = client.get("/api/devices/00000000-0000-0000-0000-000000000000", headers=session["headers"])
    assert response.status_code == 404


def test_heartbeat_marks_device_online_and_updates_fields(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-heartbeat").json()

    response = client.post(
        f"/api/devices/{device['id']}/heartbeat",
        json={"battery_level": 42, "network_status": "WIFI"},
        headers=session["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_online"] is True
    assert body["battery_level"] == 42
    assert body["network_status"] == "WIFI"


def test_heartbeat_invalid_battery_level_rejected(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-badbattery").json()

    response = client.post(
        f"/api/devices/{device['id']}/heartbeat",
        json={"battery_level": 150},
        headers=session["headers"],
    )
    assert response.status_code == 422


def test_camera_start_and_stop_creates_events(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-camera").json()

    start_response = client.post(f"/api/devices/{device['id']}/camera/start", headers=session["headers"])
    assert start_response.status_code == 200
    assert start_response.json()["camera_status"] == "STREAMING"

    stop_response = client.post(f"/api/devices/{device['id']}/camera/stop", headers=session["headers"])
    assert stop_response.status_code == 200
    assert stop_response.json()["camera_status"] == "IDLE"

    events_response = client.get(f"/api/devices/{device['id']}/events", headers=session["headers"])
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()]
    assert "CAMERA_STARTED" in event_types
    assert "CAMERA_STOPPED" in event_types


def test_camera_start_requires_ownership(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    device = _register_device(client, owner["headers"], device_uuid="device-camera-owned").json()

    response = client.post(f"/api/devices/{device['id']}/camera/start", headers=other["headers"])
    assert response.status_code == 403


def test_low_battery_event_fires_once_until_recovered(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-lowbatt").json()

    client.post(
        f"/api/devices/{device['id']}/heartbeat", json={"battery_level": 10}, headers=session["headers"]
    )
    client.post(
        f"/api/devices/{device['id']}/heartbeat", json={"battery_level": 8}, headers=session["headers"]
    )

    events_response = client.get(f"/api/devices/{device['id']}/events", headers=session["headers"])
    low_battery_events = [e for e in events_response.json() if e["event_type"] == "LOW_BATTERY"]
    assert len(low_battery_events) == 1


def test_unauthenticated_device_access_rejected(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-noauth").json()

    response = client.get(f"/api/devices/{device['id']}")
    assert response.status_code in (401, 403)
