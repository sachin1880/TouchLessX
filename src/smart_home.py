"""Optional Integration Layer: triggers smart-home devices through webhooks.

A "device" is a name plus an HTTP(S) webhook URL (and an optional bearer token) - the
shape exposed by Home Assistant webhooks/automations, IFTTT, Node-RED and most hubs.
TouchlessX never talks to hardware directly; it only POSTs to an endpoint you configure.

Devices are stored in their own file (not settings.json) because they can hold tokens.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


class SmartHomeError(ValueError):
    """Raised for an invalid device definition."""


class SmartHomeClient:
    def __init__(self, path: Path | None = None, opener=urllib.request.urlopen, timeout: float = 4.0) -> None:
        self._path = path
        self._opener = opener
        self._timeout = timeout
        self.devices: list[dict] = []
        self.last_status: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for item in data.get("devices", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("name") and item.get("url"):
                self.devices.append(
                    {"name": str(item["name"]), "url": str(item["url"]), "token": str(item.get("token", ""))}
                )

    def save(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"devices": self.devices}, f, indent=2)
            os.chmod(self._path, 0o600)  # may contain tokens
        except OSError:
            pass

    def names(self) -> list[str]:
        return [d["name"] for d in self.devices]

    def add_device(self, name: str, url: str, token: str = "") -> None:
        name, url, token = name.strip(), url.strip(), token.strip()
        if not name:
            raise SmartHomeError("Give the device a name.")
        if name in self.names():
            raise SmartHomeError(f"A device named '{name}' already exists.")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SmartHomeError("The webhook URL must start with http:// or https://")
        self.devices.append({"name": name, "url": url, "token": token})
        self.save()

    def remove_device(self, name: str) -> None:
        self.devices = [d for d in self.devices if d["name"] != name]
        self.last_status.pop(name, None)
        self.save()

    def trigger(self, name: str, wait: bool = False) -> bool:
        """Fires the device's webhook. Returns False if no such device exists; the HTTP
        call itself runs on a background thread so a slow hub never stalls tracking."""
        device = next((d for d in self.devices if d["name"] == name), None)
        if device is None:
            return False
        self.last_status[name] = "Sending…"
        if wait:
            self._send(device)
        else:
            threading.Thread(target=self._send, args=(device,), daemon=True).start()
        return True

    def _send(self, device: dict) -> None:
        body = json.dumps({"source": "touchlessx", "device": device["name"]}).encode("utf-8")
        request = urllib.request.Request(device["url"], data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        if device.get("token"):
            request.add_header("Authorization", f"Bearer {device['token']}")
        try:
            with self._opener(request, timeout=self._timeout) as response:
                self.last_status[device["name"]] = f"OK ({response.status})"
        except Exception as exc:  # network errors, HTTP errors, bad hosts...
            self.last_status[device["name"]] = f"Failed: {exc}"
