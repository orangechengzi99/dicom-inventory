"""QObject workers that keep discovery and inventory off the GUI thread."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, Signal, Slot

from ..core import InventoryRecord, InventoryResult, discover_dicom_files, inventory_files, export_csv, export_issues_csv, export_series_csv
from ..core.cancellation import CancellationToken
from .worker_lifecycle import BackgroundWorker


class ScanWorker(BackgroundWorker):
    phase_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.cancellation = CancellationToken()
        self._phase = ""

    def _checkpoint(self, stage):
        if stage != self._phase:
            self._phase = stage
            self.phase_changed.emit(stage)
        self.cancellation.checkpoint(stage)


class DiscoveryWorker(ScanWorker):
    completed = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    @Slot()
    def run(self) -> None:
        try:
            paths = discover_dicom_files(self.root, cancel_check=self._checkpoint)
            if getattr(paths, "cancelled", False):
                self.cancelled.emit(inventory_files(paths, source_root=self.root))
            else:
                self.completed.emit(paths)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self._finish_in_owner_thread()


class InventoryWorker(ScanWorker):
    progress = Signal(int, int)
    records_ready = Signal(object)
    error_line = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[Path], batch_size: int = 100, *, source_root: Path | None = None,
                 discovery_complete: bool = True, discovery_notes: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.paths = list(paths)
        self.batch_size = batch_size
        self.source_root = source_root
        self.discovery_complete = discovery_complete
        self.discovery_notes = discovery_notes
        self._batch: list[InventoryRecord] = []
        self._last_progress_time = 0.0

    def _on_progress(
        self, processed: int, total: int, record: InventoryRecord
    ) -> None:
        self._batch.append(record)
        if not record.read_success:
            self.error_line.emit(f"{record.file_path}\n{record.error_message}")
        if len(self._batch) >= self.batch_size:
            self.records_ready.emit(self._batch)
            self._batch = []
        now = monotonic()
        if processed == total or now - self._last_progress_time >= 0.05:
            self.progress.emit(processed, total)
            self._last_progress_time = now

    @Slot()
    def run(self) -> None:
        try:
            result: InventoryResult = inventory_files(
                self.paths,
                progress_callback=self._on_progress,
                source_root=self.source_root,
                discovery_complete=self.discovery_complete,
                discovery_notes=self.discovery_notes,
                cancel_check=self._checkpoint,
            )
            if self._batch:
                self.records_ready.emit(self._batch)
                self._batch = []
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self._finish_in_owner_thread()


class ExportWorker(BackgroundWorker):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, result: InventoryResult, output_dir: Path, source_root: Path, kind: str) -> None:
        super().__init__()
        self.result, self.output_dir, self.source_root, self.kind = result, output_dir, source_root, kind
        self.work_dir = None

    @Slot()
    def run(self) -> None:
        try:
            if self.kind == "comparison":
                from ..core.comparison_export import export_comparison_csv
                destination = export_comparison_csv(self.comparison_report, self.baseline_result, self.result, self.output_dir)
            elif self.kind == "issues" and self.work_dir is not None:
                from ..handling_export import export_handling_csv
                destination = export_handling_csv(self.result, self.output_dir, self.work_dir, source_root=self.source_root)
            else:
                exporter = {"files": export_csv, "issues": export_issues_csv, "series": export_series_csv}[self.kind]
                destination = exporter(self.result, self.output_dir, source_root=self.source_root)
            self.completed.emit(destination)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: 导出未完成，请核对独立输出目录和写入权限。")
        finally:
            self._finish_in_owner_thread()
