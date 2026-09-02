"""Detached driver for the seamless video-wallpaper lead-in.

Runs in its own process (spawned by the wlroots backend) so it outlives the GUI,
which exits right after apply. The job: make a video wallpaper come alive the
instant the swww transition ends, with no cold-start delay tacked on.

How: the backend kicks off a swww transition to a still of the video's first
frame. This driver launches mpvpaper *paused on that same first frame* `prewarm`
seconds before the transition ends, so mpv pays its cold-start cost while the
animation is still playing. Then it unpauses over mpv's IPC socket the moment
the transition duration has elapsed and mpv is reachable, so motion begins
exactly on cue.

`prewarm` is the backend's call, not a fixed head start: mpvpaper paints the
whole screen the instant its surface maps, so it is only harmless once the
animation has visually finished — which, for a back-loaded easing, is the very
last frame. The backend derives it from the transition's curve; this driver
just honours it.

Unpausing is not fire-and-forget (see `_hold_playing`): `mpvpaper -p` races us
and can pause the video right back, permanently, so the driver stays around
long enough to see real playback before it exits.

Stdlib only and self-contained (no core imports): it is executed as a plain
script via `python core/seamless.py <json-config>`, with no package context.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time

_CONNECT_TIMEOUT_S = 0.5
_REPLY_TIMEOUT_S = 0.5
_UNPAUSE_DEADLINE_S = 4.0  # give up if mpv never comes up; better than blocking forever
# How long to keep watching after the unpause. Must outlast mpvpaper's two-second
# deadman switch (see _hold_playing), which is what steals the playback back.
_CONFIRM_WINDOW_S = 3.0
# Uninterrupted playback that counts as safe: once frames are flowing, mpvpaper's
# deadman switch can no longer fire, so there is nothing left to watch for.
_CONFIRM_STABLE_S = 1.0
_POLL_INTERVAL_S = 0.15


class _Ipc:
    """Minimal mpv JSON-IPC client over an open socket.

    One command at a time, replies matched by `request_id`: mpv interleaves
    asynchronous event lines with command replies on the same connection, so a
    reader that takes the next line as its answer eventually reads an event
    instead.
    """

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self._buf = b""
        self._next_id = 1

    def command(self, *args: object) -> tuple[bool, object]:
        """Run one mpv command; returns (succeeded, data)."""
        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"command": list(args), "request_id": request_id})
        try:
            self._conn.sendall(payload.encode() + b"\n")
        except OSError:
            return False, None
        deadline = time.monotonic() + _REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            line = self._readline()
            if line is None:
                return False, None
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == request_id:
                return msg.get("error") == "success", msg.get("data")
        return False, None

    def _readline(self) -> bytes | None:
        """Next newline-terminated message, or None if the connection is done."""
        while b"\n" not in self._buf:
            try:
                chunk = self._conn.recv(4096)
            except OSError:
                return None  # includes the read timeout
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line


def _connect(sock: str, not_before: float) -> socket.socket | None:
    """Open mpv's IPC socket once `not_before` has passed and mpv is reachable.

    mpv creates the socket during startup, so connection refused simply means
    it isn't up yet — we retry. Gating on `not_before` guarantees motion never
    begins before the transition has visually finished, even if mpv mapped early.
    """
    deadline = time.monotonic() + _UNPAUSE_DEADLINE_S
    while time.monotonic() < deadline:
        wait = not_before - time.monotonic()
        if wait > 0:
            time.sleep(min(0.02, wait))
            continue
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(_CONNECT_TIMEOUT_S)
        try:
            conn.connect(sock)
        except OSError:
            conn.close()
            time.sleep(0.03)
            continue
        conn.settimeout(_REPLY_TIMEOUT_S)
        return conn
    return None


def _start_playback(sock: str, not_before: float) -> bool:
    """Unpause the waiting mpvpaper and make sure it stays playing.

    False means the handoff is beyond saving (mpv unreachable, gone, or frozen
    despite the retries) and the caller should fall back to a hard cut.
    """
    conn = _connect(sock, not_before)
    if conn is None:
        return False
    with conn:
        ipc = _Ipc(conn)
        started, _ = ipc.command("set_property", "pause", False)
        if not started:
            return False
        return _hold_playing(ipc)


def _hold_playing(ipc: _Ipc) -> bool:
    """Watch the freshly unpaused video until mpvpaper can no longer steal it.

    `mpvpaper -p` runs a two-second deadman switch: if its surface got no frame
    callback during a cycle, it decides the wallpaper is hidden and pauses mpv —
    then blocks waiting for a frame before it will unpause again. A paused mpv
    renders nothing, so that wait never ends. mpvpaper suppresses the check
    while it believes mpv is paused, which is why the lead-in normally survives
    it; the hole is the gap between our unpause landing and mpv's first rendered
    frame. A cycle boundary falling inside that gap leaves the wallpaper frozen
    on frame 0 forever.

    Re-sending the unpause is enough to break it: mpv renders, mpvpaper's wait
    ends, and it clears its own pause flag. So poll until playback has actually
    been running for a moment — nothing can re-arm the deadman switch once
    frames are flowing.

    A legitimate auto-pause (a fullscreen window covering the wallpaper right
    at apply) is indistinguishable from the bug here and gets fought for the
    length of the window; the next deadman cycle re-pauses it once we are gone.
    """
    deadline = time.monotonic() + _CONFIRM_WINDOW_S
    playing_since: float | None = None
    last_pos: object = None
    paused = True
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        ok, paused = ipc.command("get_property", "pause")
        if not ok:
            return False
        pos_ok, pos = ipc.command("get_property", "time-pos")
        # Position *changed*, not increased: a short clip loops back to zero.
        moving = pos_ok and last_pos is not None and pos != last_pos
        last_pos = pos if pos_ok else None
        now = time.monotonic()
        if paused:
            playing_since = None
            ipc.command("set_property", "pause", False)
        elif moving:
            playing_since = playing_since if playing_since is not None else now
            if now - playing_since >= _CONFIRM_STABLE_S:
                return True
        else:
            playing_since = None
    # Out of time. Only report a failed handoff if it is demonstrably stuck:
    # unpaused but slow to produce frames is not worth a visible relaunch.
    return not paused


def main(argv: list[str]) -> int:
    cfg = json.loads(argv[0])
    start = time.monotonic()
    # Cold-start mpv during the transition, not after it: launch `prewarm`
    # seconds before the animation ends so its surface is mapped (frozen on
    # frame 0) by the time we unpause.
    time.sleep(max(0.0, cfg["duration"] - cfg["prewarm"]))
    paused = subprocess.Popen(
        cfg["cmd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if _start_playback(cfg["sock"], start + cfg["duration"]):
            return 0
        # The paused mpvpaper would sit frozen on frame 0 forever. Kill it and
        # relaunch a plain pause=no instance so a failed handoff degrades to a
        # hard cut.
        _recover_hard_cut(paused, cfg["fallback"])
        return 0
    finally:
        # Drop the socket pathname on every exit (success or fallback), not just
        # success — mpv keeps its listening fd, so this only removes the dangling
        # name and stops per-launch sockets from piling up. A cancelled driver is
        # SIGKILLed before this runs; the backend unlinks that one instead.
        try:
            os.unlink(cfg["sock"])
        except OSError:
            pass


def _recover_hard_cut(paused: subprocess.Popen, fallback_cmd: list[str]) -> None:
    """Replace a stuck paused mpvpaper with a plain playing one."""
    try:
        os.killpg(paused.pid, signal.SIGKILL)  # pid is the session leader
    except OSError:
        pass  # already exited
    subprocess.Popen(
        fallback_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
