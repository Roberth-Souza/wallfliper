"""Pick a folder through xdg-desktop-portal's FileChooser, over D-Bus.
"""

from __future__ import annotations

import os
from itertools import count
from urllib.parse import unquote, urlparse

from PySide6.QtCore import SLOT, QObject, Signal, Slot
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusInterface,
    QDBusMessage,
    QDBusPendingCallWatcher,
)

_PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_FILECHOOSER_IFACE = "org.freedesktop.portal.FileChooser"
_REQUEST_IFACE = "org.freedesktop.portal.Request"

# Request.Response codes (portal spec): 0 success, 1 user cancelled, 2 other.
_RESPONSE_SUCCESS = 0
_RESPONSE_USER_CANCELLED = 1

# SLOT signature for the Request.Response signal (u response, a{sv} results).
# QtDBus' Python binding takes the old-style SLOT() string here, not a callable.
_RESPONSE_SLOT = SLOT("_on_response(uint,QVariantMap)")


class FolderChooser(QObject):
    """Opens the user's portal file chooser to pick one directory.

    Emits exactly one terminal signal per `open()`, so callers can rely on it to
    restore UI state:
      - `picked(path)` — a local filesystem path was chosen;
      - `cancelled()`  — the user dismissed the chooser;
      - `unavailable()`— no FileChooser portal answered (e.g. xdg-desktop-portal
        not installed) or the request failed, so a fallback should take over.
    """

    picked = Signal(str)
    cancelled = Signal()
    unavailable = Signal()

    _tokens = count()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._request_path: str | None = None
        self._busy = False

    @Slot()
    @Slot(str)
    def open(self, title: str = "Choose wallpaper folder") -> None:
        # One picker at a time: a second open() while one is live is a no-op.
        if self._busy:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            self.unavailable.emit()
            return
        self._busy = True

        token = f"wallfliper_{os.getpid()}_{next(self._tokens)}"
        sender = bus.baseService().removeprefix(":").replace(".", "_")
        self._request_path = f"{_PORTAL_PATH}/request/{sender}/{token}"


        bus.connect(
            _PORTAL_SERVICE,
            self._request_path,
            _REQUEST_IFACE,
            "Response",
            self,
            _RESPONSE_SLOT,  # type: ignore[arg-type]  # stub says bytes; SLOT() str is required
        )

        options = {
            "handle_token": token,
            "directory": True,  # select a folder, not files (portal v3+)
            "modal": True,
        }
        iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _FILECHOOSER_IFACE, bus)
        pending = iface.asyncCallWithArgumentList("OpenFile", ["", title, options])
        watcher = QDBusPendingCallWatcher(pending, self)
        watcher.finished.connect(self._on_open_dispatched)

    @Slot(QDBusPendingCallWatcher)
    def _on_open_dispatched(self, watcher: QDBusPendingCallWatcher) -> None:
        """Detect a failed OpenFile call (e.g. no FileChooser backend).

        On success the real result still comes later via the Response signal;
        this only catches the call itself erroring so we never strand the caller
        waiting on a Response that will never arrive.
        """
        watcher.deleteLater()
        if watcher.reply().type() == QDBusMessage.MessageType.ErrorMessage:
            # The call itself errored (no FileChooser backend answered the bus
            # name); the Response signal will never arrive, so settle here.
            self._settle("unavailable")

    @Slot("uint", "QVariantMap")
    def _on_response(self, code: int, results: dict) -> None:
        if code == _RESPONSE_SUCCESS:
            uris = results.get("uris") or []
            path = _uri_to_path(uris[0]) if uris else None
            self._settle("picked" if path else "cancelled", path)
        elif code == _RESPONSE_USER_CANCELLED:
            self._settle("cancelled")
        else:
            # Code 2 ("other"): the portal ended the request without a result,
            # e.g. no usable backend — treat as unavailable so a fallback runs.
            self._settle("unavailable")

    def _settle(self, outcome: str, path: str | None = None) -> None:
        """End the current request exactly once, emitting one terminal signal."""
        if not self._busy:
            return
        self._busy = False
        if self._request_path is not None:
            QDBusConnection.sessionBus().disconnect(
                _PORTAL_SERVICE,
                self._request_path,
                _REQUEST_IFACE,
                "Response",
                self,
                _RESPONSE_SLOT,  # type: ignore[arg-type]  # stub says bytes; SLOT() str is required
            )
            self._request_path = None
        if outcome == "picked" and path:
            self.picked.emit(path)
        elif outcome == "unavailable":
            self.unavailable.emit()
        else:
            self.cancelled.emit()


def portal_available() -> bool:
    """Whether the FileChooser portal can be reached on the session bus.

    True if `org.freedesktop.portal.Desktop` is already running or is
    D-Bus-activatable. Lets the caller skip hiding its window for a chooser that
    will never appear (e.g. xdg-desktop-portal not installed) and go straight to
    a fallback. Deliberately conservative: any uncertainty returns True so the
    async `open()` path — which settles `unavailable` on a failed call — stays
    the real arbiter rather than this pre-check wrongly masking a working chooser.
    """
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    iface = bus.interface()
    if iface is None:
        return True
    try:
        if iface.isServiceRegistered(_PORTAL_SERVICE).value():
            return True
    except Exception:
        return True
    try:
        reply = iface.call("ListActivatableNames")
        args = reply.arguments()
        return _PORTAL_SERVICE in (args[0] if args else [])
    except Exception:
        return True


def _uri_to_path(uri: str) -> str | None:
    """Convert a portal `file://` URI to a local path. Non-file URIs → None."""
    if uri.startswith("file://"):
        return unquote(urlparse(uri).path) or None
    return None
