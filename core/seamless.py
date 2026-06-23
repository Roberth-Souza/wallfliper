"""Detached driver for the seamless video-wallpaper lead-in.

Runs in its own process (spawned by the wlroots backend) so it outlives the GUI,
which exits right after apply. The job: make a video wallpaper come alive the
instant the swww transition ends, with no cold-start delay tacked on.

How: the backend kicks off a swww transition to a still of the video's first
frame. This driver launches mpvpaper *paused on that same first frame* partway
through the transition — so mpv pays its cold-start cost while the animation is
still playing, and because it's frozen on frame 0 (identical to the transition's
endpoint) it can map on top of swww without showing motion or cutting the wipe.
Then it unpauses over mpv's IPC socket the moment the transition duration has
elapsed and mpv is reachable, so motion begins exactly on cue.

Stdlib only and self-contained (no core imports): it is executed as a plain
script via `python core/seamless.py <json-config>`, with no package context.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time

_UNPAUSE = json.dumps({"command": ["set_property", "pause", False]}).encode() + b"\n"
_CONNECT_TIMEOUT_S = 0.5
_UNPAUSE_DEADLINE_S = 4.0  # give up if mpv never comes up; better than blocking forever


def _unpause(sock: str, not_before: float) -> bool:
    """Send the unpause command once `not_before` has passed and mpv is reachable.

    mpv creates the IPC socket during startup, so connection refused simply means
    it isn't up yet — we retry. Gating on `not_before` guarantees motion never
    begins before the transition has visually finished, even if mpv mapped early.
    """
    deadline = time.monotonic() + _UNPAUSE_DEADLINE_S
    while time.monotonic() < deadline:
        wait = not_before - time.monotonic()
        if wait > 0:
            time.sleep(min(0.02, wait))
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(_CONNECT_TIMEOUT_S)
                client.connect(sock)
                client.sendall(_UNPAUSE)
            return True
        except OSError:
            time.sleep(0.03)
    return False


def main(argv: list[str]) -> int:
    cfg = json.loads(argv[0])
    start = time.monotonic()
    # Cold-start mpv during the transition, not after it: launch `prewarm`
    # seconds before the animation ends so its surface is mapped (frozen on
    # frame 0) by the time we unpause.
    time.sleep(max(0.0, cfg["duration"] - cfg["prewarm"]))
    subprocess.Popen(
        cfg["cmd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    _unpause(cfg["sock"], not_before=start + cfg["duration"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
