#!/usr/bin/env python3
"""Root-side DHT11 polling daemon for LabSafe.

The Flask app reads the JSON state file and does not touch GPIO directly.
"""

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime


READER = os.environ.get("LABSAFE_DHT11_READER", "/home/elf/labsafe/sensors/dht11_read")
CHIP = os.environ.get("LABSAFE_DHT11_CHIP", "/dev/gpiochip3")
OFFSET = os.environ.get("LABSAFE_DHT11_OFFSET", "8")
STATE_FILE = os.environ.get("LABSAFE_DHT11_STATE_FILE", "/tmp/labsafe_dht11.json")
INTERVAL = float(os.environ.get("LABSAFE_DHT11_INTERVAL", "2.5"))


def write_state(state):
    directory = os.path.dirname(STATE_FILE) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".labsafe_dht11.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, STATE_FILE)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_once():
    proc = subprocess.run(
        [READER, CHIP, OFFSET],
        text=True,
        capture_output=True,
        timeout=3.0,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(msg or f"dht11_read exited {proc.returncode}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main():
    last_ok = {
        "temperature": None,
        "humidity": None,
        "chip": CHIP,
        "offset": int(OFFSET),
    }
    while True:
        now = datetime.now().isoformat()
        try:
            data = read_once()
            last_ok.update({
                "temperature": float(data["temperature"]),
                "humidity": float(data["humidity"]),
                "chip": data.get("chip", CHIP),
                "offset": int(data.get("offset", OFFSET)),
            })
            state = {
                **last_ok,
                "status": "ok",
                "updated_at": now,
                "error": "",
            }
        except Exception as e:
            state = {
                **last_ok,
                "status": "error",
                "updated_at": now,
                "error": str(e)[:200],
            }
        write_state(state)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
