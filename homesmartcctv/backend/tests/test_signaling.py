import pytest


def _register_device(client, headers, device_uuid="device-signal"):
    return client.post(
        "/api/devices/register",
        json={"device_name": "Living Room Camera", "device_uuid": device_uuid, "platform": "ANDROID"},
        headers=headers,
    )


def test_ice_servers_requires_auth(client):
    response = client.get("/api/webrtc/ice-servers")
    assert response.status_code in (401, 403)


def test_ice_servers_returns_stun_config(client, register_and_login):
    session = register_and_login()
    response = client.get("/api/webrtc/ice-servers", headers=session["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body["ice_servers"]) >= 1
    assert body["ice_servers"][0]["urls"].startswith("stun:")


def test_signaling_rejects_invalid_token(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-signal-badtoken").json()

    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/devices/{device['id']}/signal?token=not-a-real-token&role=camera"):
            pass


def test_signaling_rejects_non_owner(client, register_and_login):
    owner = register_and_login()
    other = register_and_login()
    device = _register_device(client, owner["headers"], device_uuid="device-signal-notowner").json()

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/api/devices/{device['id']}/signal?token={other['token']}&role=viewer"
        ):
            pass


def test_signaling_relays_offer_answer_and_ice_between_camera_and_viewer(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-signal-relay").json()
    token = session["token"]
    device_id = device["id"]

    with client.websocket_connect(f"/api/devices/{device_id}/signal?token={token}&role=camera") as camera_ws:
        with client.websocket_connect(f"/api/devices/{device_id}/signal?token={token}&role=viewer") as viewer_ws:
            assert camera_ws.receive_json() == {"type": "peer-joined", "role": "viewer"}
            assert viewer_ws.receive_json() == {"type": "peer-joined", "role": "camera"}

            viewer_ws.send_json({"type": "offer", "sdp": "fake-offer-sdp"})
            assert camera_ws.receive_json() == {"type": "offer", "sdp": "fake-offer-sdp"}

            camera_ws.send_json({"type": "answer", "sdp": "fake-answer-sdp"})
            assert viewer_ws.receive_json() == {"type": "answer", "sdp": "fake-answer-sdp"}

            viewer_ws.send_json({"type": "ice-candidate", "candidate": "fake-candidate"})
            assert camera_ws.receive_json() == {"type": "ice-candidate", "candidate": "fake-candidate"}

        assert camera_ws.receive_json() == {"type": "peer-left", "role": "viewer"}


def test_signaling_second_viewer_replaces_first(client, register_and_login):
    session = register_and_login()
    device = _register_device(client, session["headers"], device_uuid="device-signal-replace").json()
    token = session["token"]
    device_id = device["id"]

    with client.websocket_connect(f"/api/devices/{device_id}/signal?token={token}&role=camera"):
        with client.websocket_connect(f"/api/devices/{device_id}/signal?token={token}&role=viewer") as first_viewer:
            assert first_viewer.receive_json() == {"type": "peer-joined", "role": "camera"}

            with client.websocket_connect(
                f"/api/devices/{device_id}/signal?token={token}&role=viewer"
            ) as second_viewer:
                assert first_viewer.receive_json() == {"type": "replaced"}
                assert second_viewer.receive_json() == {"type": "peer-joined", "role": "camera"}
