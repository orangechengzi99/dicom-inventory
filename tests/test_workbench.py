from __future__ import annotations

import csv
import os
import time
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from dicom_inventory.core import inventory_files
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.table_model import AnomalyFilterProxyModel, InventoryTableModel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate(), "后台任务未在限定时间内结束"
    app.processEvents()


def sample_result(tmp_path, make_dicom):
    source = tmp_path / "source"
    normal = make_dicom(source / "01-已检查.dcm")
    warning = make_dicom(source / "02-空间字段待核对.dcm", overrides={"PixelSpacing": None, "SliceThickness": None})
    duplicate = make_dicom(source / "03-重复实例.dcm")
    uid = inventory_files([duplicate]).records[0].sop_instance_uid
    other = make_dicom(source / "04-重复实例副本.dcm", sop_uid=uid)
    bad = source / "05-读取失败.dcm"
    bad.write_bytes(b"synthetic broken file")
    return source, inventory_files([normal, warning, duplicate, other, bad], source_root=source)


def test_combined_filters_match_same_issue(app, tmp_path, make_dicom):
    source, result = sample_result(tmp_path, make_dicom)
    model = InventoryTableModel()
    model.set_records(result.records)
    proxy = AnomalyFilterProxyModel()
    proxy.setSourceModel(model)
    assert proxy.rowCount() == 5
    proxy.set_anomalies_only(True)
    assert proxy.rowCount() == 4
    proxy.set_issue_type("标签缺失")
    proxy.set_severity("INFO")
    assert proxy.rowCount() == 1
    proxy.set_issue_type("SOPInstanceUID重复")
    assert proxy.rowCount() == 0
    proxy.set_severity("WARNING")
    assert proxy.rowCount() == 2
    proxy.set_anomalies_only(False)
    assert proxy.rowCount() == 2  # Turning checkbox off does not discard other filters.
    proxy.set_severity("")
    proxy.set_issue_type("")
    assert proxy.rowCount() == 5


def test_details_multiple_issues_links_and_metadata_disclosure(app, tmp_path, make_dicom):
    source, result = sample_result(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    window._inventory_completed(result)
    assert not window.uid_text.isVisible()
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(1, 0)))
    assert window.issue_selector.count() == 2
    descriptions = set()
    for index in range(window.issue_selector.count()):
        window.issue_selector.setCurrentIndex(index)
        descriptions.add(window.issue_description.text())
        assert window.issue_impact.text() != "—"
        assert window.issue_advice.text() != "—"
    assert len(descriptions) == 2
    assert "PixelSpacing: 未提供" in window.metadata_text.toPlainText()
    assert "SOPClassUID" in window.uid_text.toPlainText()
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(2, 0)))
    assert window.affected_model.rowCount() == 2
    target = window.affected_model.index(1)
    expected_path = target.data()
    window.anomaly_checkbox.setChecked(True)
    window._jump_to_affected(target)
    assert window._current_record.file_path == expected_path
    assert not window.anomaly_checkbox.isChecked()
    window.close()
    app.processEvents()


def test_filter_empty_state_does_not_show_stale_detail(app, tmp_path, make_dicom):
    source, result = sample_result(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    window._inventory_completed(result)
    window.severity_combo.setCurrentIndex(3)  # INFO
    window.type_combo.setCurrentIndex(2)  # Duplicate, no INFO duplicate issue
    assert window.proxy_model.rowCount() == 0
    assert window._current_record is None
    assert window.issue_selector.count() == 0
    assert window.affected_model.rowCount() == 0
    assert "没有记录" in window.empty_hint.text()
    window.close()


@pytest.mark.parametrize("repeat", range(4))
def test_real_background_discovery_reading_and_three_exports(app, tmp_path, make_dicom, monkeypatch, repeat):
    source, _ = sample_result(tmp_path, make_dicom)
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    from dicom_inventory.gui import workers
    main_thread = threading.get_ident()
    background_calls = []
    def track(original):
        def wrapped(*args, **kwargs):
            background_calls.append(threading.get_ident() != main_thread)
            time.sleep(0.02)  # Permit a GUI timer tick while real work is running.
            return original(*args, **kwargs)
        return wrapped
    for name in ("discover_dicom_files", "inventory_files", "export_csv", "export_issues_csv", "export_series_csv"):
        monkeypatch.setattr(workers, name, track(getattr(workers, name)))
    window = MainWindow()
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    try:
        window._begin_discovery(source)
        assert window._is_busy() and not window.choose_button.isEnabled()
        assert not window.progress_bar.isHidden() and window.scan_status.isHidden()
        wait_until(app, lambda: not window._is_busy())
        assert window.progress_bar.isHidden() and window.scan_status.text() == "等待扫描"
        assert len(window.candidate_paths) == 5
        assert window.start_button.isEnabled()
        window._start_inventory()
        assert not window.start_button.isEnabled()
        assert not window.progress_bar.isHidden() and window.scan_status.isHidden()
        wait_until(app, lambda: not window._is_busy())
        assert window.progress_bar.isHidden() and window.scan_status.text().startswith("扫描完成")
        assert window.result.summary.file_count == 5
        assert window.result.summary.failure_count == 1
        assert "发现 4 个需核对文件" == window.overview_title.text()
        assert window.export_button.isEnabled()
        window.anomaly_checkbox.setChecked(True)
        for kind in ("files", "issues", "series"):
            window._begin_export(output, kind)
            assert window._is_busy() and not window.export_button.isEnabled()
            wait_until(app, lambda: not window._is_busy())
            assert window.export_button.isEnabled()
        assert (output / "issues.csv").exists()
        assert (output / "series.csv").exists()
        files = next(output.glob("dicom_inventory_*.csv"))
        with files.open(encoding="utf-8-sig", newline="") as stream:
            assert len(list(csv.DictReader(stream))) == 5  # Not just filtered rows.
        assert beats
        assert background_calls == [True, True, True, True, True]
    finally:
        timer.stop()
        wait_until(app, lambda: not window._is_busy())
        window.close()
        app.processEvents()


def test_export_failure_keeps_result_and_controls_recover(app, tmp_path, make_dicom, monkeypatch):
    source, result = sample_result(tmp_path, make_dicom)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    window = MainWindow()
    window.source_root = source
    window._inventory_completed(result)
    window._begin_export(source, "issues")
    wait_until(app, lambda: not window._is_busy())
    assert window.result is result
    assert window.export_button.isEnabled()
    assert "导出未完成" in window.error_text.toPlainText()
    assert not (source / "issues.csv").exists()
    window.close()


@pytest.mark.parametrize("width", [1320, 980])
def test_painted_workbench_layout_and_keyboard(app, tmp_path, make_dicom, width):
    source, result = sample_result(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    window.path_edit.setText(str(source))
    window._inventory_completed(result)
    window.resize(width, 860)
    window.show()
    app.processEvents()
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(1, 0)))
    window.table.setFocus()
    QTest.keyClick(window.table, Qt.Key.Key_Down)
    assert window._current_record.file_path == result.records[2].file_path
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(1, 0)))
    assert window.splitter.widget(0).width() >= 440
    app.processEvents()  # Allow label height-for-width updates before image QA.
    assert window.splitter.widget(1).width() >= 330
    assert window.anomaly_checkbox.isVisible()
    assert window.issue_description.isVisible()
    assert window.technical_toggle.isVisible() or window.splitter.widget(1).verticalScrollBar().maximum() > 0
    assert window.export_button.mapTo(window.centralWidget(), window.export_button.rect().bottomRight()).x() < window.centralWidget().width()
    assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
    assert window.export_button.isEnabled()
    for label in (window.issue_description, window.issue_impact, window.issue_advice):
        assert label.height() >= label.heightForWidth(label.width())
    for row in range(window.proxy_model.rowCount()):
        index = window.proxy_model.index(row, 1)
        assert window.table.indexWidget(index) is None
    destination = os.environ.get("DICOM_VISUAL_OUTPUT")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(output / f"dicom-v12-files-{width}.png"))
    window.close()
    app.processEvents()
