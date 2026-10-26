"""Starts MTG Collection Manager as a program: the server and then your browser.

This is what the .exe runs (see build_exe.bat). Everything the app keeps (mtg.db, .env, backups, the stream
state files) lives next to the .exe, so the program can sit in any folder and moving it moves the data.
"""

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# The working directory is where the data lives. Do this before the app is imported: it reads .env and opens
# "./mtg.db" relative to it.
HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
os.chdir(HOME)

import uvicorn  # noqa: E402

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8017"))
URL = f"http://127.0.0.1:{PORT}/"


def is_ours() -> bool:
    """Is MTG Collection Manager already answering on this port?"""
    try:
        with urllib.request.urlopen(URL, timeout=2) as response:
            return b"MTG Collection Manager" in response.read(20000)
    except OSError:
        return False


def open_browser_when_ready() -> None:
    if os.getenv("NO_BROWSER"):  # e.g. for a test run
        return
    for _ in range(100):
        if is_ours():
            webbrowser.open(URL)
            return
        time.sleep(0.3)


def main() -> None:
    if is_ours():  # started twice: just show the one that is already running
        print(f"MTG Collection Manager is already running at {URL}\n"
              "That is the copy you will see: it may be another one (or an older version) than this program.\n"
              "Close it first if you want to start this one. This window closes in a few seconds.")
        if not os.getenv("NO_BROWSER"):
            webbrowser.open(URL)
        time.sleep(8)
        return
    from app.main import app  # imported here so the working directory is set first

    print(f"MTG Collection Manager: {URL}\nData folder: {HOME}\nClose this window to stop the program.\n")
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
