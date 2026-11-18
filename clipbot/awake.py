"""Rechner wach halten, solange ClipBot laeuft. Schlaeft der Mac ein, reissen Mitschnitt und Chat ab
und nach dem Aufwachen sieht jeder Chat wie ein Peak aus. Ein zugeklappter Laptop schlaeft trotzdem."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("clipbot")


def keep_awake() -> str:
    """Verhindert den Ruhezustand bei Untaetigkeit. Gibt zurueck, wie ("caffeinate", "windows" oder "")."""
    try:
        if sys.platform == "darwin" and shutil.which("caffeinate"):
            # -i: kein Ruhezustand bei Untaetigkeit, -w: endet von selbst mit ClipBot
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "caffeinate"
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            return "windows"
    except Exception as exc:
        log.debug("Wachhalten nicht moeglich: %s", exc)
    return ""
