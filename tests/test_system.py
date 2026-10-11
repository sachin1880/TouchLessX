"""Tests for the operating-system integration: data folder, start at login, update check."""
from __future__ import annotations

import io
import json
import plistlib
import sys
import urllib.error
from pathlib import Path

import pytest

import src.system as system
from src.system import LoginItem, check_for_update, data_root, is_newer, launch_command, parse_version


def test_source_runs_keep_data_in_the_project_and_packaged_apps_do_not(monkeypatch, tmp_path):
    assert data_root(tmp_path) == tmp_path
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(system.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(system.platform, "system", lambda: "Darwin")
    assert data_root(Path("/Applications/TouchlessX.app/Contents")) == tmp_path / "Library/Application Support/TouchlessX"
    monkeypatch.setattr(system.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert data_root(Path("C:/Program Files/TouchlessX")) == tmp_path / "Roaming" / "TouchlessX"


def test_launch_command_for_source_and_for_a_mac_app_bundle(monkeypatch):
    assert launch_command(Path("/code/app.py")) == [sys.executable, "/code/app.py"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/TouchlessX.app/Contents/MacOS/TouchlessX")
    monkeypatch.setattr(system.platform, "system", lambda: "Darwin")
    assert launch_command(Path("ignored")) == ["/usr/bin/open", "-a", "/Applications/TouchlessX.app"]
    monkeypatch.setattr(system.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sys, "executable", "C:/Apps/TouchlessX/TouchlessX.exe")
    assert launch_command(Path("ignored")) == ["C:/Apps/TouchlessX/TouchlessX.exe"]


def test_start_at_login_on_macos_writes_and_removes_a_launch_agent(tmp_path):
    item = LoginItem(home=tmp_path, system="Darwin")
    assert item.is_enabled() is False
    item.enable(["/usr/bin/open", "-a", "/Applications/TouchlessX.app"])
    assert item.path == tmp_path / "Library/LaunchAgents/com.touchlessx.app.plist" and item.is_enabled()
    agent = plistlib.loads(item.path.read_bytes())
    assert agent["ProgramArguments"] == ["/usr/bin/open", "-a", "/Applications/TouchlessX.app"]
    assert agent["RunAtLoad"] is True and agent["Label"] == "com.touchlessx.app"
    item.disable()
    assert item.is_enabled() is False
    item.disable()  # again: nothing to remove, no error


def test_start_at_login_on_linux_uses_an_autostart_entry(tmp_path):
    item = LoginItem(home=tmp_path, system="Linux")
    item.enable(["/opt/touch less/python", "/opt/touch less/app.py"])
    text = item.path.read_text()
    assert 'Exec="/opt/touch less/python" "/opt/touch less/app.py"' in text and item.is_enabled()
    item.disable()
    assert not item.path.exists()


def test_versions_are_compared_by_number_not_by_text():
    assert parse_version("v1.10.2") == (1, 10, 2) and parse_version("TouchlessX 2.0") == (2, 0)
    assert parse_version("nightly") == ()
    assert is_newer("v1.10.0", "1.9.9") and is_newer("2.0", "1.99.99") and is_newer("1.3.1", "1.3")
    assert not is_newer("1.3.0", "1.3") and not is_newer("1.2.9", "1.3.0") and not is_newer("", "1.0")


def _release(payload=None, error=None):
    def opener(request, timeout):
        assert request.full_url == "https://api.github.com/repos/sachin1880/TouchLessX/releases/latest"
        if error is not None:
            raise error
        return io.BytesIO(json.dumps(payload).encode())
    return opener


def test_update_check_reports_newer_same_none_and_errors():
    page = "https://github.com/sachin1880/TouchLessX/releases/tag/v9.0.0"
    assert check_for_update("1.3.0", _release({"tag_name": "v9.0.0", "html_url": page})) == {
        "status": "update", "latest": "v9.0.0", "url": page}
    assert check_for_update("9.0.0", _release({"tag_name": "v9.0.0", "html_url": page}))["status"] == "current"
    assert check_for_update("1.0", _release({"tag_name": "latest-build"}))["status"] == "none"
    missing = urllib.error.HTTPError("u", 404, "Not Found", None, None)
    assert check_for_update("1.0", _release(error=missing))["status"] == "none"       # nothing released yet
    limited = urllib.error.HTTPError("u", 403, "rate limited", None, None)
    assert check_for_update("1.0", _release(error=limited))["status"] == "error"
    assert check_for_update("1.0", _release(error=OSError("offline")))["status"] == "error"


def test_update_check_only_ever_links_to_this_projects_release_page():
    hostile = check_for_update("1.0", _release({"tag_name": "v9", "html_url": "https://evil.example/download.dmg"}))
    assert hostile["status"] == "update" and hostile["url"] == "https://github.com/sachin1880/TouchLessX/releases"
