from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.models.camera_video import CameraVideo


def _register_device(client, headers, device_uuid="device-video"):
    return client.post(
        "/api/devices/register",
        json={"device_name": "Living Room Camera", "device_uuid": device_uuid, "platform": "ANDROID"},
        headers=headers,
    )


def _fake_mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"fake-mp4-content" * 10


def test_create_video_requires_auth(client):
    response = client.post(
        "/api/devices/00000000-0000-0000-0000-000000000000/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": "60"},
    )
    assert response.status_code in (401, 403)


def test_create_video_and_retrieve_file(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"]).json()
    content = _fake_mp4_bytes()
    started_at = datetime.now(timezone.utc).isoformat()

    response = client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", content, "video/mp4")},
        data={"started_at": started_at, "duration_seconds": "60"},
        headers=session["headers"],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["duration_seconds"] == 60
    assert body["video_path"] == f"/devices/{device['id']}/videos/{body['id']}/file"

    file_response = client.get(
        f"/api/devices/{device['id']}/videos/{body['id']}/file", headers=session["headers"]
    )
    assert file_response.status_code == 200
    assert file_response.content == content
    assert file_response.headers["content-type"] == "video/mp4"


def test_list_videos_orders_newest_first(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-video-list").json()

    first_start = datetime.now(timezone.utc) - timedelta(minutes=2)
    second_start = datetime.now(timezone.utc) - timedelta(minutes=1)

    client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": first_start.isoformat(), "duration_seconds": "60"},
        headers=session["headers"],
    )
    client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": second_start.isoformat(), "duration_seconds": "60"},
        headers=session["headers"],
    )

    response = client.get(f"/api/devices/{device['id']}/videos", headers=session["headers"])
    assert response.status_code == 200
    videos = response.json()
    assert len(videos) == 2
    assert videos[0]["started_at"] > videos[1]["started_at"]


def test_create_video_rejects_non_mp4(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-video-badtype").json()

    response = client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mov", b"not-a-real-mov", "video/quicktime")},
        data={"started_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": "60"},
        headers=session["headers"],
    )
    assert response.status_code == 422


def test_video_file_requires_ownership(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    device = _register_device(client, owner["headers"], device_uuid="device-video-ownership").json()

    upload = client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": "60"},
        headers=owner["headers"],
    )
    video_id = upload.json()["id"]

    response = client.get(f"/api/devices/{device['id']}/videos/{video_id}/file", headers=other["headers"])
    assert response.status_code == 403


def test_expired_videos_are_removed_on_next_upload(client, register_and_login, db_session):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-video-expiry").json()

    stale = client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": "60"},
        headers=session["headers"],
    ).json()

    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().video_retention_days + 1)
    db_session.query(CameraVideo).filter(CameraVideo.id == stale["id"]).update({"created_at": cutoff})
    db_session.commit()

    # A second upload's opportunistic cleanup should remove the now-expired first one.
    client.post(
        f"/api/devices/{device['id']}/videos",
        files={"file": ("segment.mp4", _fake_mp4_bytes(), "video/mp4")},
        data={"started_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": "60"},
        headers=session["headers"],
    )

    response = client.get(
        f"/api/devices/{device['id']}/videos/{stale['id']}/file", headers=session["headers"]
    )
    assert response.status_code == 404
