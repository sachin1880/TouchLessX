"""Tests for Home CCTV: the HomeSmartCCTV client and service, the command allowlist,
confirmation through the action controller, profiles and voice. No network needed -
the HTTP layer and the credential store are replaced by fakes."""
from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from src.action_controller import actions_for_gesture
from src.cctv_client import CctvClient, CctvError, CctvService, TokenStore, validate_server_url
from src.cctv_commands import CCTV_COMMANDS, cctv_actions, cctv_command
from src.profiles import validate_profile
from src.voice import parse_command
from tests.test_features import _controller, _Clock

SERVER = "http://localhost:8000/api"


# ------------------------------------------------------------------ fakes
class _Response:
    def __init__(self, body):
        self._raw = json.dumps(body).encode() if body is not None else b""

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Answers like the backend; records every request."""

    def __init__(self):
        self.requests = []
        self.fail_network = 0       # the next N requests fail before reaching the server
        self.statuses = ["DELIVERED", "ACKNOWLEDGED", "SUCCEEDED"]
        self.refuse = None          # (code, detail) to answer the next command creation with

    def __call__(self, request, timeout):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, body, dict(request.header_items())))
        if self.fail_network:
            self.fail_network -= 1
            raise urllib.error.URLError("unreachable")
        path = request.full_url[len(SERVER):]
        if path == "/auth/login":
            if body["password"] != "right":
                raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b'{"detail":"x"}'))
            return _Response({"access_token": "tok-123"})
        if request.get_header("Authorization") != "Bearer tok-123":
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"{}"))
        if path == "/devices":
            return _Response([{"id": "cam-1", "device_name": "Living Room", "is_online": True}])
        if path.endswith("/commands") and request.get_method() == "POST":
            if self.refuse:
                code, detail = self.refuse
                raise urllib.error.HTTPError(request.full_url, code, "no", {},
                                             io.BytesIO(json.dumps({"detail": detail}).encode()))
            return _Response({"id": "cmd-1", "status": "PENDING"})
        if path.startswith("/commands/"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return _Response({"id": "cmd-1", "status": status, "error_code": None})
        if path.endswith("/cancel"):
            return _Response({"id": "cmd-1", "status": "CANCELLED"})
        raise AssertionError(f"unexpected request {path}")

    def created(self):
        return [r for r in self.requests if r[1].endswith("/commands") and r[0] == "POST"]


class FakeKeyring:
    def __init__(self):
        self.saved = {}

    def get_password(self, service, user):
        return self.saved.get((service, user))

    def set_password(self, service, user, password):
        self.saved[(service, user)] = password

    def delete_password(self, service, user):
        self.saved.pop((service, user), None)


def _service(server=None, keyring=None):
    server = server or FakeServer()
    store = TokenStore(keyring or FakeKeyring())
    service = CctvService(SERVER, token_store=store, sleep=lambda s: None,
                          client_factory=lambda url, token=None: CctvClient(url, token=token, opener=server))
    return service, server, store


def _until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _signed_in_service():
    service, server, store = _service()
    service.sign_in("me@example.com", "right")
    assert _until(lambda: service.snapshot()["devices"])
    return service, server, store


# ------------------------------------------------------------------ allowlist
def test_the_command_allowlist_matches_the_backend():
    assert {c.api_type for c in CCTV_COMMANDS.values()} == {"REFRESH_STATUS", "SWITCH_CAMERA", "CAPTURE_SNAPSHOT"}
    assert cctv_command("cctv:capture_snapshot").needs_confirmation
    assert not cctv_command("cctv:refresh_status").needs_confirmation
    assert cctv_command("cctv:format_disk") is None and cctv_command("smart_home:lamp") is None


@pytest.mark.parametrize("url", ["http://localhost:8000/api", "http://127.0.0.1:8000/api",
                                 "http://192.168.1.20:8000/api", "https://cctv.example.com/api"])
def test_accepted_server_addresses(url):
    assert validate_server_url(url + "/") == url


@pytest.mark.parametrize("url", ["ftp://localhost/api", "localhost:8000", "http://cctv.example.com/api",
                                 "http://8.8.8.8/api"])
def test_refused_server_addresses(url):
    with pytest.raises(CctvError):
        validate_server_url(url)


# ------------------------------------------------------------------ client
def test_client_errors_are_classified():
    server = FakeServer()
    client = CctvClient(SERVER, opener=server)
    with pytest.raises(CctvError) as auth:
        client.login("me@example.com", "wrong")
    assert auth.value.kind == "auth"
    with pytest.raises(CctvError) as missing:
        client.devices()
    assert missing.value.kind == "auth"  # not signed in: nothing is sent
    assert len(server.requests) == 1
    client.login("me@example.com", "right")
    server.fail_network = 1
    with pytest.raises(CctvError) as network:
        client.devices()
    assert network.value.kind == "network"
    server.refuse = (429, "Too many unfinished commands")
    with pytest.raises(CctvError) as refused:
        client.create_command("cam-1", "REFRESH_STATUS", "key-123456")
    assert refused.value.kind == "http" and refused.value.status == 429 and "Too many" in str(refused.value)


def test_the_password_is_never_stored_and_the_session_goes_to_the_keyring():
    service, server, store = _signed_in_service()
    assert store.load(SERVER) == "tok-123"
    assert "right" not in str(store._keyring.saved)
    service.sign_out()
    assert store.load(SERVER) is None and not service.signed_in


def test_without_a_credential_store_the_session_is_kept_in_memory_only():
    store = TokenStore(backend=False)
    store._keyring = None
    store.save(SERVER, "tok")
    assert store.load(SERVER) == "tok" and not store.persistent


# ------------------------------------------------------------------ service
def test_sending_needs_a_session_and_a_camera():
    service, server, _ = _service()
    assert not service.send("capture_snapshot")
    assert server.created() == []
    assert not service.send("not_a_command")


def test_a_command_is_followed_to_its_result():
    service, server, _ = _signed_in_service()
    assert service.snapshot()["device_id"] == "cam-1"  # the only camera is chosen
    assert service.send("capture_snapshot")
    assert _until(lambda: service.snapshot()["recent"][0].status == "SUCCEEDED")
    method, url, body, headers = server.created()[0]
    assert body["command_type"] == "CAPTURE_SNAPSHOT" and body["source"] == "touchlessx"
    assert headers["Authorization"] == "Bearer tok-123"


def test_network_retries_reuse_the_same_idempotency_key():
    service, server, _ = _signed_in_service()
    server.fail_network = 2
    service.send("switch_camera")
    assert _until(lambda: service.snapshot()["recent"][0].status == "SUCCEEDED")
    attempts = server.created()  # two lost on the network, the third arrived
    assert len(attempts) == 3
    assert len({r[2]["idempotency_key"] for r in attempts}) == 1  # so the server sees one command


def test_network_failures_are_retried_a_bounded_number_of_times():
    service, server, _ = _signed_in_service()
    server.fail_network = 99
    service.send("refresh_status")
    assert _until(lambda: service.snapshot()["recent"][0].status == "FAILED")
    attempts = [r for r in server.requests if r[1].endswith("/commands")]
    assert len(attempts) == 3 and len({r[2]["idempotency_key"] for r in attempts}) == 1


def test_a_refusal_from_the_server_is_not_retried():
    service, server, _ = _signed_in_service()
    server.refuse = (409, "already used")
    service.send("refresh_status")
    assert _until(lambda: service.snapshot()["recent"][0].status == "FAILED")
    assert len([r for r in server.requests if r[1].endswith("/commands")]) == 1


def test_an_expired_session_signs_out():
    service, server, store = _signed_in_service()
    service.client.token = "stale"
    service.refresh_devices()
    assert _until(lambda: not service.signed_in)
    assert store.load(SERVER) is None


def test_a_slow_camera_never_holds_up_the_next_command():
    service, server, _ = _signed_in_service()
    server.statuses = ["ACKNOWLEDGED"]  # the camera never finishes
    service.send("capture_snapshot")
    assert _until(lambda: service.snapshot()["recent"][0].status == "ACKNOWLEDGED")
    service.send("refresh_status")
    assert _until(lambda: len(server.created()) == 2, timeout=1.0)


def test_each_gesture_is_a_new_command():
    service, server, _ = _signed_in_service()
    service.send("refresh_status")
    service.send("refresh_status")
    assert _until(lambda: len(server.created()) == 2)
    assert len({r[2]["idempotency_key"] for r in server.created()}) == 2


# ------------------------------------------------------------------ gestures, profiles, voice
def _cctv_controller():
    controller, executor, safety = _controller()
    clock = _Clock()
    controller._clock = clock
    sent = []
    controller.callbacks["cctv"] = lambda name: sent.append(name) or True
    controller.mappings = {"fist": "cctv:capture_snapshot", "two_fingers": "cctv:refresh_status"}
    return controller, sent, clock


def test_a_status_refresh_is_sent_at_once():
    controller, sent, _ = _cctv_controller()
    assert controller.run_action("cctv:refresh_status").label == "CCTV: Camera Status"
    assert sent == ["refresh_status"]


def test_a_snapshot_waits_for_the_confirmation_gesture():
    controller, sent, clock = _cctv_controller()
    assert controller.run_action("cctv:capture_snapshot").label == "Confirm CCTV: Take Snapshot?"
    assert sent == []
    assert controller.run_action("confirm").label == "CCTV: Take Snapshot"
    assert sent == ["capture_snapshot"]
    assert controller.run_action("confirm") is None  # one confirmation, one command


def test_a_late_confirmation_sends_nothing():
    controller, sent, clock = _cctv_controller()
    controller.run_action("cctv:switch_camera")
    clock.now += controller.confirm_seconds + 1
    assert controller.run_action("confirm") is None and sent == []


def test_practice_mode_never_reaches_the_camera():
    controller, sent, _ = _cctv_controller()
    controller.dry_run = True
    controller.run_action("cctv:refresh_status")
    controller.run_action("cctv:capture_snapshot")
    controller.run_action("confirm")
    assert sent == []


def test_cctv_actions_can_be_mapped_and_survive_profile_validation():
    assert set(cctv_actions()) <= set(actions_for_gesture("fist"))
    profile = validate_profile({"mappings": {"fist": "cctv:capture_snapshot", "pinch": "cctv:format_disk"}})
    assert profile["mappings"]["fist"] == "cctv:capture_snapshot"
    assert profile["mappings"]["pinch"] == "none"  # not on the allowlist


def test_voice_phrases():
    assert parse_command("take snapshot").action == "cctv:capture_snapshot"
    assert parse_command("flip camera").action == "cctv:switch_camera"
    assert parse_command("camera status").action == "cctv:refresh_status"
    assert parse_command("confirm").action == "confirm"
    assert parse_command("switch app").action == "switch_app"
