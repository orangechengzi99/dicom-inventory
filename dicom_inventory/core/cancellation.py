"""Cooperative cancellation, independent of Qt and DICOM rules."""
from collections.abc import Callable
from threading import Event, Lock

CancelCheck = Callable[[str], None] | None


class ScanCancelled(Exception):
    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(stage)


class CancellationToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._stage = "准备扫描"
        self._requested_stage = ""

    @property
    def requested(self):
        return self._event.is_set()

    def request(self):
        with self._lock:
            if not self._event.is_set():
                self._requested_stage = self._stage
                self._event.set()

    def checkpoint(self, stage: str):
        with self._lock:
            self._stage = stage
            if self._event.is_set():
                raise ScanCancelled(self._requested_stage)


def checkpoint(check: CancelCheck, stage: str):
    if check is not None:
        check(stage)


def checked(items, check: CancelCheck, stage: str):
    for item in items:
        checkpoint(check, stage)
        yield item
    checkpoint(check, stage)
