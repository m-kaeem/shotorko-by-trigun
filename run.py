"""Start Shotorko locally:  python run.py   ->  http://localhost:8000"""
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"\n  Shotorko by TRIGUN is starting on http://localhost:{port}  (Ctrl+C to stop)\n")
    uvicorn.run("src.api:app", host="127.0.0.1", port=port, log_level="warning")
