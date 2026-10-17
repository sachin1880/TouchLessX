from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.models.camera_event import CameraEvent


def _register_device(client, headers, device_uuid="device-capture"):
    return client.post(
        "/api/devices/register",
        json={"device_name": "Living Room Camera", "device_uuid": device_uuid, "platform": "ANDROID"},
        headers=headers,
    )


def _fake_jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"fake-jpeg-content" * 10


def test_create_capture_requires_auth(client):
    response = client.post(
        "/api/devices/00000000-0000-0000-0000-000000000000/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code in (401, 403)


def test_create_capture_and_retrieve_image(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"]).json()
    content = _fake_jpeg_bytes()

    response = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", content, "image/jpeg")},
        headers=session["headers"],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event_type"] == "MANUAL_CAPTURE"
    assert body["image_path"] == f"/devices/{device['id']}/captures/{body['id']}/image"

    image_response = client.get(
        f"/api/devices/{device['id']}/captures/{body['id']}/image", headers=session["headers"]
    )
    assert image_response.status_code == 200
    assert image_response.content == content
    assert image_response.headers["content-type"] == "image/jpeg"


def test_capture_appears_in_event_history(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-history").json()

    client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        headers=session["headers"],
    )

    events = client.get(f"/api/devices/{device['id']}/events", headers=session["headers"]).json()
    captures = [e for e in events if e["event_type"] == "MANUAL_CAPTURE"]
    assert len(captures) == 1
    assert captures[0]["image_path"] is not None


def test_create_capture_rejects_non_jpeg(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-badtype").json()

    response = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.png", b"not-a-real-png", "image/png")},
        headers=session["headers"],
    )
    assert response.status_code == 422


def test_capture_image_requires_ownership(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    device = _register_device(client, owner["headers"], device_uuid="device-capture-ownership").json()

    upload = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        headers=owner["headers"],
    )
    event_id = upload.json()["id"]

    response = client.get(f"/api/devices/{device['id']}/captures/{event_id}/image", headers=other["headers"])
    assert response.status_code == 403


def test_expired_captures_are_removed_on_next_upload(client, register_and_login, db_session):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-expiry").json()

    stale = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        headers=session["headers"],
    ).json()

    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().capture_retention_days + 1)
    db_session.query(CameraEvent).filter(CameraEvent.id == stale["id"]).update({"created_at": cutoff})
    db_session.commit()

    # A second upload's opportunistic cleanup should remove the now-expired first one.
    client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        headers=session["headers"],
    )

    response = client.get(
        f"/api/devices/{device['id']}/captures/{stale['id']}/image", headers=session["headers"]
    )
    assert response.status_code == 404


def test_create_person_detected_capture_and_retrieve_image(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-detect").json()
    content = _fake_jpeg_bytes()

    response = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", content, "image/jpeg")},
        data={"event_type": "PERSON_DETECTED"},
        headers=session["headers"],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event_type"] == "PERSON_DETECTED"
    assert body["image_path"] == f"/devices/{device['id']}/captures/{body['id']}/image"

    image_response = client.get(
        f"/api/devices/{device['id']}/captures/{body['id']}/image", headers=session["headers"]
    )
    assert image_response.status_code == 200
    assert image_response.content == content


def test_create_capture_rejects_unknown_event_type(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-badtype").json()

    response = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        data={"event_type": "NOT_A_REAL_TYPE"},
        headers=session["headers"],
    )
    assert response.status_code == 422


def test_expired_captures_are_removed_when_listing_too(client, register_and_login, db_session):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-expiry-list").json()
    stale = client.post(
        f"/api/devices/{device['id']}/captures",
        files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        headers=session["headers"],
    ).json()
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().capture_retention_days + 1)
    db_session.query(CameraEvent).filter(CameraEvent.id == stale["id"]).update({"created_at": cutoff})
    db_session.commit()

    # No new upload (camera off): looking at the history alone removes it.
    events = client.get(f"/api/devices/{device['id']}/events", headers=session["headers"]).json()
    assert stale["id"] not in [e["id"] for e in events]
    response = client.get(f"/api/devices/{device['id']}/captures/{stale['id']}/image", headers=session["headers"])
    assert response.status_code == 404


def test_captures_are_kept_for_two_days():
    assert get_settings().capture_retention_days == 2
    assert get_settings().video_retention_days == 2


def test_image_only_lists_just_photos(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-capture-image-only").json()
    client.post(f"/api/devices/{device['id']}/heartbeat", json={"battery_level": 5, "network_status": "WIFI"},
                headers=session["headers"])  # LOW_BATTERY / DEVICE_ONLINE: no image
    for event_type in ("MANUAL_CAPTURE", "PERSON_DETECTED"):
        client.post(
            f"/api/devices/{device['id']}/captures",
            files={"file": ("capture.jpg", _fake_jpeg_bytes(), "image/jpeg")},
            data={"event_type": event_type},
            headers=session["headers"],
        )
    everything = client.get(f"/api/devices/{device['id']}/events", headers=session["headers"]).json()
    photos = client.get(f"/api/devices/{device['id']}/events?image_only=true", headers=session["headers"]).json()
    assert len(everything) > len(photos)
    assert sorted(e["event_type"] for e in photos) == ["MANUAL_CAPTURE", "PERSON_DETECTED"]
    assert all(e["image_path"] for e in photos)
