"""SQLite tests use generated anonymous data, never clinical files."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from dicom_inventory.core import inventory_files
from dicom_inventory.storage import SnapshotStore,StorageError,DB_NAME,APPLICATION_ID,SCHEMA_VERSION


@pytest.fixture
def saved_sample(tmp_path,make_dicom):
    root=tmp_path/"source"
    paths=[make_dicom(root/f"{i}.dcm") for i in range(3)]
    invalid=root/"invalid.dcm"
    invalid.write_bytes(b"synthetic invalid")
    paths.append(invalid)
    result=inventory_files(paths,source_root=root)
    workspace=tmp_path/"workspace"
    workspace.mkdir()
    return root,paths,result,SnapshotStore(workspace)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complete_roundtrip_and_independent_reopen(saved_sample):
    root,paths,result,store=saved_sample
    before={path:sha(path) for path in paths}
    store.save(result)
    other=SnapshotStore(store.directory)
    assert other.load(result.scan_run.run_id)==result
    assert other.list_scans()[0].file_count==4
    assert other.list_scans()[0].issue_count==len(result.issues)
    assert {path:sha(path) for path in paths}==before
    assert not (root/DB_NAME).exists()
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0]==SCHEMA_VERSION
        assert connection.execute("PRAGMA application_id").fetchone()[0]==APPLICATION_ID
    payload=store.db_path.read_bytes()
    for identity in (b"PatientName",b"PatientID",b"PRIVATE-ID","身份字段不得导出".encode(),b"PixelData"):
        assert identity not in payload


def test_reopen_in_fresh_process_without_reading_source(saved_sample):
    root,paths,result,store=saved_sample
    store.save(result)
    code="from dicom_inventory.storage import SnapshotStore; import sys; r=SnapshotStore(sys.argv[1]).load(sys.argv[2]); print(r.summary.file_count, r.scan_run.status)"
    completed=subprocess.run([sys.executable,"-B","-c",code,str(store.directory),result.scan_run.run_id],
                              capture_output=True,text=True,encoding="utf-8",check=True,timeout=15,
                              env={**os.environ,"PYTHONIOENCODING":"utf-8"})
    assert completed.stdout.strip()=="4 完成"


@pytest.mark.parametrize("state",["完成","未完成","已取消","失败"])
def test_terminal_state_retained(saved_sample,state):
    _,_,result,store=saved_sample
    modified=replace(result,scan_run=replace(result.scan_run,status=state))
    store.save(modified)
    assert store.load(modified.scan_run.run_id)==modified


def test_empty_batch_roundtrip(tmp_path):
    root=tmp_path/"source"
    root.mkdir()
    work=tmp_path/"work"
    work.mkdir()
    result=inventory_files([],source_root=root)
    store=SnapshotStore(work)
    store.save(result)
    assert store.load(result.scan_run.run_id)==result


@pytest.mark.parametrize("location",["root","child"])
def test_source_directory_blocked_before_database_creation(saved_sample,location):
    root,_,result,_=saved_sample
    work=root if location=="root" else root/"work"
    work.mkdir(exist_ok=True)
    with pytest.raises(StorageError,match="原始DICOM"):
        SnapshotStore(work,source_root=root)
    store=SnapshotStore(work)
    with pytest.raises(StorageError,match="原始DICOM"):
        store.save(result)
    assert not (work/DB_NAME).exists()


def test_database_links_blocked(saved_sample,tmp_path):
    _,_,_,store=saved_sample
    original=tmp_path/"do-not-change.sqlite3"
    original.write_bytes(b"synthetic hardlink guard")
    os.link(original,store.db_path)
    before=sha(original)
    with pytest.raises(StorageError,match="链接"):
        SnapshotStore(store.directory)
    assert sha(original)==before


@pytest.mark.parametrize("foreign",["foreign","future","invalid"])
def test_foreign_or_future_database_not_modified(saved_sample,foreign):
    _,_,_,store=saved_sample
    if foreign=="foreign":
        with sqlite3.connect(store.db_path) as connection:
            connection.execute("CREATE TABLE unrelated (name TEXT)")
    elif foreign=="future":
        store.initialize()
        with sqlite3.connect(store.db_path) as connection:
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION+1}")
    else:
        store.db_path.write_bytes(b"not an SQLite file")
    before=sha(store.db_path)
    with pytest.raises(StorageError):
        store.initialize()
    assert sha(store.db_path)==before


def test_failed_initial_migration_rolls_back(saved_sample,monkeypatch):
    _,_,_,store=saved_sample
    original=store._migrate_0_to_1
    def failure(connection):
        connection.execute("CREATE TABLE should_rollback (id INTEGER)")
        raise sqlite3.OperationalError("synthetic migration failure")
    monkeypatch.setattr(store,"_migrate_0_to_1",failure)
    with pytest.raises(StorageError):
        store.initialize()
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[]
        assert connection.execute("PRAGMA user_version").fetchone()[0]==0
    monkeypatch.setattr(store,"_migrate_0_to_1",original)
    store.initialize()
    assert store.list_scans()==()


def test_failed_batch_insert_rolls_back_preserves_old(saved_sample):
    _,_,result,store=saved_sample
    store.save(result)
    later=replace(result,scan_run=replace(result.scan_run,run_id="another-run"))
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("""CREATE TRIGGER force_failure BEFORE INSERT ON files
        WHEN NEW.run_id='another-run' AND NEW.ordinal=1
        BEGIN SELECT RAISE(ABORT,'synthetic insert failure'); END""")
    with pytest.raises(StorageError):
        store.save(later)
    assert len(store.list_scans())==1
    assert store.load(result.scan_run.run_id)==result
    with sqlite3.connect(store.db_path) as connection:
        for table in ("scan_runs","files","issues","studies","series"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id='another-run'").fetchone()[0]==0


def test_repeated_save_never_overwrites(saved_sample):
    _,_,result,store=saved_sample
    store.save(result)
    with pytest.raises(StorageError,match="不会覆盖"):
        store.save(result)
    assert store.load(result.scan_run.run_id)==result
    assert len(store.list_scans())==1


@pytest.mark.parametrize("corruption",["unknown_field","missing_file","bad_reference","payload_version","bad_type"])
def test_corrupt_snapshot_rejected(saved_sample,corruption):
    _,_,result,store=saved_sample
    store.save(result)
    with sqlite3.connect(store.db_path) as connection:
        if corruption=="missing_file":
            connection.execute("DELETE FROM files WHERE ordinal=0")
        elif corruption=="payload_version":
            connection.execute("UPDATE scan_runs SET payload_version=999")
        else:
            value=json.loads(connection.execute("SELECT payload FROM files WHERE ordinal=0").fetchone()[0])
            if corruption=="unknown_field": value["PatientName"]="synthetic forbidden"
            if corruption=="bad_reference": value["issues"]=["missing-issue"]
            if corruption=="bad_type": value["rows"]={"unsafe":"type"}
            connection.execute("UPDATE files SET payload=? WHERE ordinal=0",(json.dumps(value),))
    with pytest.raises(StorageError):
        store.load(result.scan_run.run_id)


def test_multiple_runs_empty_missing_and_unknown_paths(saved_sample):
    _,_,result,store=saved_sample
    store.initialize()
    assert store.list_scans()==()
    with pytest.raises(StorageError,match="不存在"):
        store.load("unknown")
    store.save(result)
    later=replace(result,scan_run=replace(result.scan_run,run_id="later",finished_at="2099-01-01T00:00:00"))
    store.save(later)
    assert [row.run_id for row in store.list_scans()]==["later",result.scan_run.run_id]


def test_missing_scan_identity_rejected_before_write(saved_sample):
    _,_,result,store=saved_sample
    with pytest.raises(StorageError):
        store.save(replace(result,scan_run=None))
    assert not store.db_path.exists()


def test_reading_missing_database_does_not_create_it(saved_sample):
    _,_,_,store=saved_sample
    with pytest.raises(StorageError):
        store.list_scans()
    assert not store.db_path.exists()


def test_unregistered_nonempty_folder_not_used_for_new_database(saved_sample):
    _,_,_,store=saved_sample
    marker=store.directory/"personal-file.txt"
    marker.write_bytes(b"synthetic private marker")
    with pytest.raises(StorageError,match="空的独立"):
        store.initialize()
    assert marker.read_bytes()==b"synthetic private marker"
    assert not store.db_path.exists()
