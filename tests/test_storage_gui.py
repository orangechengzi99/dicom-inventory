"""Real Qt save/restart/open and failure flows with anonymous generated data."""
from dataclasses import replace
import os
from pathlib import Path
import threading
import time
import pytest
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog
from PySide6.QtTest import QTest
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.storage_ui import SavedScansDialog
from dicom_inventory.storage import SnapshotStore,DB_NAME
from test_ui_visual import build_sample
from test_ui_gray import assert_no_purple
from test_workbench import wait_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    result=[]
    for name in ("warning","information"):
        monkeypatch.setattr(QMessageBox,name,lambda *args: result.append(args[2]))
    monkeypatch.setattr(QMessageBox,"question",lambda *args: QMessageBox.StandardButton.Yes)
    return result


@pytest.mark.parametrize("repeat",range(3))
def test_background_save_close_reopen_offline_and_preserve_gui(app,tmp_path,make_dicom,monkeypatch,messages,repeat):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"工作目录"
    work.mkdir()
    main_thread=threading.get_ident()
    calls=[]
    for name in ("initialize","save","list_scans","load"):
        original=getattr(SnapshotStore,name)
        def tracked(self,*args,_name=name,_original=original,**kwargs):
            calls.append((_name,threading.get_ident()!=main_thread))
            time.sleep(.025)
            return _original(self,*args,**kwargs)
        monkeypatch.setattr(SnapshotStore,name,tracked)
    beats=[]
    timer=QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda:beats.append(True))
    timer.start()
    restored=None
    try:
        window.show()
        controller=window.workspace
        controller.prepare(work)
        assert window._is_busy() and not controller.button.isEnabled()
        wait_until(app,lambda:not window._is_busy())
        assert controller.directory==work.resolve()
        assert controller.save_action.isEnabled()
        window.anomaly_checkbox.setChecked(True)
        assert window.proxy_model.rowCount()<len(result.records)
        controller.save()
        assert window._is_busy()
        wait_until(app,lambda:not window._is_busy())
        assert controller.snapshot_label.text()=="本次扫描 · 已保存"
        assert not controller.save_action.isEnabled()
        assert controller.open_action.isEnabled()
        assert SnapshotStore(work).load(result.scan_run.run_id)==result
        window.close()
        # Offline source, no DICOM reread during restoring.
        Path(result.scan_run.source_root).rename(tmp_path/"原数据暂时离线")
        import pydicom
        monkeypatch.setattr(pydicom,"dcmread",lambda *a,**k:pytest.fail("Restoring must not reread DICOM"))
        restored=MainWindow()
        restored.show()
        restored.workspace.prepare(work)
        wait_until(app,lambda:not restored._is_busy())
        monkeypatch.setattr(restored.workspace,"_select_scan",lambda scans:scans[0].run_id)
        restored.workspace.open()
        wait_until(app,lambda:not restored._is_busy())
        assert restored.result==result
        assert restored.workspace.is_history
        assert restored.scan_status.text().startswith("历史快照")
        assert "未重新读取" in restored.overview_detail.text()
        assert "历史快照" in restored.workspace.snapshot_label.text()
        assert not restored.start_button.isEnabled()
        assert restored.export_button.isEnabled()
        assert restored.proxy_model.rowCount()==len(result.records)
        assert not restored.anomaly_checkbox.isChecked()
        output=os.environ.get("DICOM_STORAGE_OUTPUT")
        if output and repeat==0:
            image=restored.grab()
            assert_no_purple(image)
            assert image.save(str(Path(output)/"dicom-storage-restored-1320.png"))
        restored._jump_to_series()
        assert restored._current_node.kind=="series"
        assert restored.splitter.widget(1).horizontalScrollBar().maximum()==0
        assert beats and all(background for name,background in calls if name!="load")  # Direct API validation below is deliberate.
        assert any(name=="load" and background for name,background in calls)
        assert not messages
    finally:
        timer.stop()
        for item in (window,restored):
            if item is not None:
                wait_until(app,lambda:not item._is_busy())
                item.close()
        app.processEvents()


@pytest.mark.parametrize("mode",["prepare","load","save"])
def test_failed_operation_preserves_current_result(app,tmp_path,make_dicom,messages,mode):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"work"
    work.mkdir()
    try:
        window.show()
        if mode=="prepare":
            window.workspace.prepare(Path(result.scan_run.source_root))
        else:
            window.workspace.prepare(work)
            wait_until(app,lambda:not window._is_busy())
            if mode=="load":
                window.workspace.load("absent-run")
            else:
                SnapshotStore(work).save(result)
                window.workspace.save()  # Duplicate must not overwrite or claim new save.
        wait_until(app,lambda:not window._is_busy())
        assert window.result is result
        assert window.export_button.isEnabled()
        assert window.workspace.button.isEnabled()
        assert messages
        if mode=="prepare":
            assert window.workspace.directory is None
            assert not (Path(result.scan_run.source_root)/DB_NAME).exists()
        if mode=="save":
            assert window.workspace.snapshot_label.text()=="本次扫描 · 未保存"
            assert SnapshotStore(work).load(result.scan_run.run_id)==result
    finally:
        window.close()
        app.processEvents()


def test_reopen_current_source_can_start_new_discovery(app,tmp_path,make_dicom,messages):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"work"
    work.mkdir()
    try:
        window.workspace.prepare(work)
        wait_until(app,lambda:not window._is_busy())
        window.workspace.save()
        wait_until(app,lambda:not window._is_busy())
        window.workspace.load(result.scan_run.run_id)
        wait_until(app,lambda:not window._is_busy())
        assert window.workspace.is_history
        window._path_edited()  # Same source path must rediscover, not silently do nothing.
        wait_until(app,lambda:not window._is_busy())
        assert not window.workspace.is_history and window.result is None
        assert window.start_button.isEnabled()
        assert len(window.candidate_paths)==len(result.records)
    finally:
        wait_until(app,lambda:not window._is_busy())
        window.close()
        app.processEvents()


@pytest.mark.parametrize("width",[1320,980])
def test_saved_scan_dialog_keyboard_and_layout(app,tmp_path,make_dicom,messages,width):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"很长的独立工作目录名称用于布局验证"
    work.mkdir()
    dialog=None
    try:
        window.resize(width,860 if width==1320 else 660)
        window.show()
        window.workspace.prepare(work)
        wait_until(app,lambda:not window._is_busy())
        window.workspace.save()
        wait_until(app,lambda:not window._is_busy())
        rows=SnapshotStore(work).list_scans()
        dialog=SavedScansDialog(window,rows)
        dialog.show()
        app.processEvents()
        assert dialog.selected_id()==result.scan_run.run_id
        assert dialog.source_path.text()==result.scan_run.source_root
        assert dialog.source_path.isReadOnly()
        assert dialog.list.model().index(0).data(Qt.ItemDataRole.ToolTipRole)==result.scan_run.source_root
        dialog.list.setFocus()
        QTest.keyClick(dialog,Qt.Key.Key_Escape)
        assert dialog.result()==QDialog.DialogCode.Rejected
        assert window.table.horizontalScrollBar().maximum()==0
        assert window.splitter.widget(1).horizontalScrollBar().maximum()==0
        assert window.workspace.button.geometry().right()<window.width()
        output=os.environ.get("DICOM_STORAGE_OUTPUT")
        for name,widget in (("saved",window),):
            image=widget.grab()
            assert_no_purple(image)
            if output:
                assert image.save(str(Path(output)/f"dicom-storage-{name}-{width}.png"))
        dialog.show()
        app.processEvents()
        image=dialog.grab()
        assert_no_purple(image)
        if output:
            assert image.save(str(Path(output)/f"dicom-storage-dialog-{width}.png"))
    finally:
        if dialog: dialog.close()
        window.close()
        app.processEvents()


def test_open_can_keep_unsaved_result_when_user_declines(app,tmp_path,make_dicom,monkeypatch,messages):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"work"
    work.mkdir()
    try:
        SnapshotStore(work).save(result)
        window.workspace.prepare(work)
        wait_until(app,lambda:not window._is_busy())
        monkeypatch.setattr(window.workspace,"_select_scan",lambda scans:scans[0].run_id)
        monkeypatch.setattr(QMessageBox,"question",lambda *args:QMessageBox.StandardButton.No)
        window.workspace.open()
        wait_until(app,lambda:not window._is_busy())
        assert window.result is result and not window.workspace.is_history
        assert window.workspace.snapshot_label.text()=="本次扫描 · 未保存"
    finally:
        window.close()
        app.processEvents()


def test_new_source_cannot_contain_workspace(app,tmp_path,make_dicom,messages):
    window,result=build_sample(tmp_path,make_dicom)
    work=tmp_path/"work"
    work.mkdir()
    try:
        window.workspace.prepare(work)
        wait_until(app,lambda:not window._is_busy())
        window._begin_discovery(tmp_path)
        assert not window._is_busy()
        assert window.result is result
        assert window.source_root==Path(result.scan_run.source_root)
        assert "数据目录不能包含当前工作目录" in messages[-1]
    finally:
        window.close()
        app.processEvents()
