"""Real worker exit/destruction threads, including concurrent deferred dialogs."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QObject, QThread, Slot, Qt
from PySide6.QtWidgets import QApplication
from dicom_inventory.gui.workers import DiscoveryWorker, InventoryWorker, ExportWorker
from dicom_inventory.gui.storage_ui import SnapshotWorker
from dicom_inventory.gui.handling_ui import ReviewWorker
from dicom_inventory.gui.comparison_ui import ComparisonWorker
from test_comparison import case, before
from test_workbench import wait_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class LifecycleProbe(QObject):
    def __init__(self):
        super().__init__()
        self.results = []
        self.failures = []
        self.destroyed_threads = []
        self.received_threads = []

    @Slot(object)
    @Slot(str, object)
    def completed(self, *args):
        self.results.append(args)
        self.received_threads.append(QThread.currentThread())

    @Slot(str)
    @Slot(str, str)
    def failed(self, *args):
        self.failures.append(args)
        self.received_threads.append(QThread.currentThread())

    @Slot()
    def worker_destroyed(self):
        # Direct connection measures the real destructor thread, not a
        # subsequently queued observer callback on the GUI thread.
        self.destroyed_threads.append(QThread.currentThread())


@pytest.mark.parametrize("kind", ["discovery", "inventory", "export", "snapshot", "review", "comparison"])
@pytest.mark.parametrize("failure", [False, True])
def test_workers_return_and_are_destroyed_on_creator_thread(app, case, monkeypatch, kind, failure):
    baseline = before(case)
    key = "synthetic-missing-run" if failure else baseline.scan_run.run_id
    if kind == "discovery":
        worker = DiscoveryWorker(case.root / "missing" if failure else case.root)
    elif kind == "inventory":
        worker = InventoryWorker(case.paths, source_root=case.root)
        if failure:
            from dicom_inventory.gui import workers
            def fail(*args, **kwargs):
                raise ValueError("synthetic read failure")
            monkeypatch.setattr(workers, "inventory_files", fail)
    elif kind == "export":
        worker = ExportWorker(baseline, case.root if failure else case.output, case.root, "files")
    elif kind == "snapshot":
        worker = SnapshotWorker("load", case.work, run_id=key)
    elif kind == "review":
        worker = ReviewWorker(case.work, case.root, key, "read", ())
    else:
        worker = ComparisonWorker(case.work, case.root, "baseline", (key,))
    owner = QThread.currentThread()
    thread = QThread()
    probe = LifecycleProbe()
    worker.completed.connect(probe.completed)
    worker.failed.connect(probe.failed)
    worker.destroyed.connect(probe.worker_destroyed, Qt.ConnectionType.DirectConnection)
    worker.owner_ready.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.start()
    try:
        wait_until(app, lambda: bool(probe.destroyed_threads) and thread.isFinished(), 15)
        assert probe.destroyed_threads == [owner]
        assert probe.received_threads == [owner]
        assert bool(probe.failures) is failure
        assert bool(probe.results) is not failure
        assert thread.wait(0)
    finally:
        # No terminate: all synthetic tasks above have bounded local inputs.
        if thread.isRunning():
            thread.quit()
            thread.wait(15000)
        thread.deleteLater()
        probe.deleteLater()
        app.processEvents()
