"""Keep Python-backed QObject destruction on the creating GUI thread.

Deleting a Python QObject on a worker thread can hold Qt's connection mutex
while waiting for the GIL. Simultaneous deferred QWidget destruction on the
GUI thread can hold the GIL and wait for that mutex. Return the childless
worker to its owner before quitting; finished -> deleteLater then runs there.
Computation and storage remain on the worker thread, not on the GUI thread.
"""
from PySide6.QtCore import QObject, QThread, Signal


class BackgroundWorker(QObject):
    owner_ready = Signal()

    def __init__(self):
        super().__init__()
        self._owner_thread = QThread.currentThread()

    def _finish_in_owner_thread(self):
        # Called from run's finally, i.e. the QObject's current owning thread.
        # These workers have no QObject children and never own GUI controls.
        if self.thread() != self._owner_thread:
            self.moveToThread(self._owner_thread)
        self.owner_ready.emit()
