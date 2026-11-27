"""Start Shotorko locally:  python run.py   ->  http://localhost:8000

If port 8000 is already taken (often an older Shotorko window that is still running),
the app starts on the next free port and says so, instead of failing or showing the old
version. The browser opens only once the server answers, so it never lands on an error page.
"""
import os
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _pick_port(wanted: int) -> int:
    for p in range(wanted, wanted + 20):
        if _port_free(p):
            return p
    raise SystemExit(f"Ports {wanted}-{wanted + 19} are all busy. Close other servers or set PORT=...")


def _open_when_ready(url: str, timeout: float = 120.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
                if r.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.5)


if __name__ == "__main__":
    wanted = int(os.environ.get("PORT", 8000))
    port = _pick_port(wanted)
    url = f"http://localhost:{port}"
    if port != wanted:
        print(f"\n  ! Port {wanted} is already in use (maybe an older Shotorko window is still running;"
              f" close it with Ctrl+C there).\n    Starting this version on port {port} instead.")
    if not os.environ.get("NO_BROWSER"):
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    print(f"\n  Shotorko by TRIGUN is starting on {url}  (loading the model takes a few seconds; Ctrl+C to stop)\n")
    uvicorn.run("src.api:app", host="127.0.0.1", port=port, log_level="warning")
