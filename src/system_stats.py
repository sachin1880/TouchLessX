"""CPU, memory and battery figures for the Holo Desk's System Info panel, read with the
system's own tools (no extra dependency). Parsing is kept separate so it can be tested.
Every reader returns None where a figure isn't available."""
from __future__ import annotations

import os
import platform
import re
import subprocess


def _run(*command: str) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_cpu(ps_output: str, cpu_count: int) -> float | None:
    """Total of `ps -A -o %cpu` as a share of all cores, 0-100."""
    total = 0.0
    for line in ps_output.splitlines()[1:]:
        try:
            total += float(line.strip().replace(",", "."))
        except ValueError:
            continue
    return max(0.0, min(100.0, total / max(1, cpu_count)))


def parse_memory(vm_stat_output: str, total_bytes: int) -> float | None:
    """Memory in use (active + wired + compressed) from macOS `vm_stat`, 0-100."""
    size = re.search(r"page size of (\d+) bytes", vm_stat_output)
    if not size or total_bytes <= 0:
        return None
    page = int(size.group(1))

    def pages(name: str) -> int:
        found = re.search(rf"{name}:\s+(\d+)", vm_stat_output)
        return int(found.group(1)) if found else 0

    used = pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")
    return max(0.0, min(100.0, used * page * 100.0 / total_bytes))


def parse_battery(pmset_output: str) -> tuple[float, bool] | None:
    """(percent, charging) from `pmset -g batt`, or None without a battery."""
    found = re.search(r"(\d+)%;\s*([a-zA-Z ]+)", pmset_output)
    if not found:
        return None
    state = found.group(2).strip().lower()
    return float(found.group(1)), state in ("charging", "charged", "finishing charge", "ac attached")


def read_stats() -> dict:
    """{"cpu": %, "memory": %, "battery": (%, charging) or None}; slow-ish (tens of ms),
    so call it off the UI thread."""
    mac = platform.system() == "Darwin"
    stats = {"cpu": parse_cpu(_run("ps", "-A", "-o", "%cpu"), os.cpu_count() or 1), "memory": None, "battery": None}
    if mac:
        total = _run("sysctl", "-n", "hw.memsize").strip()
        stats["memory"] = parse_memory(_run("vm_stat"), int(total) if total.isdigit() else 0)
        stats["battery"] = parse_battery(_run("pmset", "-g", "batt"))
    return stats
