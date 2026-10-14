"""Offline SQLite scan snapshots. Connections live only in the calling thread."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .core.models import (InventoryResult, InventorySummary, InventoryRecord, QualityIssue,
                          StudySummary, SeriesSummary, ScanRun)
from .snapshot_codec import dumps,loads,decode
from .handling import HandlingMixin,HANDLING_TABLES
from .comparison_store import ComparisonMixin, COMPARISON_TABLES

DB_NAME = "dicom_inventory.sqlite3"
APPLICATION_ID = 0x44494349
SCHEMA_VERSION = 3
PAYLOAD_VERSION = 1
SNAPSHOT_TABLES = {"scan_runs","files","issues","studies","series"}
TABLES = SNAPSHOT_TABLES | HANDLING_TABLES | COMPARISON_TABLES


class StorageError(ValueError):
    """Fixed user-facing message; raw SQLite errors are not logged."""


@dataclass(frozen=True)
class SavedScan:
    run_id: str
    finished_at: str
    source_root: str
    status: str
    file_count: int
    issue_count: int


def validate_result(result):
    run = result.scan_run
    if run is None or not run.run_id or not Path(run.source_root).is_absolute():
        raise StorageError("结果没有完整批次标识或绝对来源目录，不能保存。")
    records = {record.file_path:record for record in result.records}
    issues = {issue.issue_id:issue for issue in result.issues}
    summary = result.summary
    if (len(records) != len(result.records) or len(issues) != len(result.issues)
        or summary != run.summary or summary.file_count != len(records)
        or summary.issue_count != len(issues)
        or summary.success_count != sum(record.read_success for record in result.records)
        or summary.failure_count != summary.file_count-summary.success_count):
        raise StorageError("扫描计数或批次关联不一致，不能保存或打开。")
    for record in result.records:
        if not Path(record.file_path).is_absolute():
            raise StorageError("保存文件路径必须为绝对路径。")
    for objects in (result.records,result.studies,result.series):
        for item in objects:
            if any(issues.get(issue.issue_id) != issue for issue in item.issues):
                raise StorageError("问题关联不完整。")
    if any(not set(issue.affected_files).issubset(records) for issue in result.issues):
        raise StorageError("受影响文件关联不完整。")
    for groups in (result.studies,result.series):
        names = [item.group_key for item in groups]
        paths = [path for item in groups for path in item.files]
        if len(set(names)) != len(names) or len(set(paths)) != len(paths) or set(paths) != set(records):
            raise StorageError("检查或序列分组不完整。")
    series = {item.group_key:item for item in result.series}
    if any(key not in series for study in result.studies for key in study.series_keys):
        raise StorageError("检查与序列关联不完整。")
    if any(path not in records for paths in result.duplicates.values() for path in paths):
        raise StorageError("重复实例关联不完整。")


class SnapshotStore(HandlingMixin, ComparisonMixin):
    def __init__(self,directory,source_root=None):
        self.directory = Path(directory).expanduser().resolve()
        self.db_path = self.directory / DB_NAME
        self.source_root = Path(source_root).expanduser().resolve() if source_root else None
        self._guard()

    def _guard(self,result=None):
        if not self.directory.is_dir():
            raise StorageError("工作目录不存在或不是文件夹。")
        roots = [self.source_root] if self.source_root else []
        if result:
            roots.append(Path(result.scan_run.source_root).resolve())
            roots.extend(Path(record.file_path).resolve().parent for record in result.records)
        if any(self.directory == root or self.directory.is_relative_to(root) for root in roots):
            raise StorageError("工作目录不能位于原始DICOM目录或输入文件目录及其子目录。")
        for name in (DB_NAME,DB_NAME+"-journal",DB_NAME+"-wal",DB_NAME+"-shm"):
            path = self.directory / name
            if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
                raise StorageError("数据库或附属文件不能是符号链接或多链接文件。")

    @contextmanager
    def _connection(self,write=False,create=True):
        self._guard()
        connection = None
        try:
            mode = ("rwc" if create else "rw") if write else "ro"
            connection = sqlite3.connect(self.db_path.as_uri()+"?mode="+mode,
                                         uri=True,timeout=2,isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        except StorageError:
            raise
        except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,OverflowError):
            raise StorageError("工作库操作未完成；请检查版本、数据完整性、写入权限或是否被占用。") from None
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def _check(self,connection,allow_empty=False,allow_legacy=False):
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if allow_empty and app_id == 0 and version == 0 and not tables:
            return 0
        if app_id != APPLICATION_ID:
            raise StorageError("不是本工具的工作库；不会覆盖或改写其他数据库。")
        if version > SCHEMA_VERSION:
            raise StorageError("工作库版本较新，请使用对应版本的软件；未进行降级。")
        if not (version == SCHEMA_VERSION and tables == TABLES
                or allow_legacy and version == 1 and tables == SNAPSHOT_TABLES
                or allow_legacy and version == 2 and tables == SNAPSHOT_TABLES | HANDLING_TABLES):
            raise StorageError("工作库版本或结构不受支持。")
        for (source,) in connection.execute("SELECT DISTINCT source_root FROM scan_runs"):
            root = Path(source).resolve()
            if self.directory == root or self.directory.is_relative_to(root):
                raise StorageError("工作库位于其记录的DICOM来源目录内，请先迁移工作库。")
        return version

    def initialize(self):
        """Transactional 0 -> 1 -> 2 -> 3; retain immutable legacy observations."""
        if not self.db_path.exists() and any(self.directory.iterdir()):
            raise StorageError("首次使用请选空的独立工作目录，不要选择原数据或个人文件目录。")
        with self._connection(write=True) as connection:
            version = self._check(connection,allow_empty=True,allow_legacy=True)
            if version == SCHEMA_VERSION:
                return self.db_path
            allowed={DB_NAME,DB_NAME+"-journal",DB_NAME+"-wal",DB_NAME+"-shm"}
            if version == 0 and any(item.name not in allowed for item in self.directory.iterdir()):
                raise StorageError("初始化工作库请使用空的独立目录；该目录含其他文件。")
            connection.execute("BEGIN IMMEDIATE")
            # Recheck under the writer lock; another process may have initialized.
            version = self._check(connection,allow_empty=True,allow_legacy=True)
            if version == 0:
                self._migrate_0_to_1(connection)
                version = 1
            if version == 1:
                self._migrate_1_to_2(connection)
                version = 2
            if version == 2:
                self._migrate_2_to_3(connection)
            connection.commit()
        return self.db_path

    def _migrate_0_to_1(self,connection):
        connection.execute("""CREATE TABLE scan_runs (
            run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            source_root TEXT NOT NULL, status TEXT NOT NULL, rule_version TEXT NOT NULL,
            summary_json TEXT NOT NULL, notes_json TEXT NOT NULL, duplicates_json TEXT NOT NULL,
            counts_json TEXT NOT NULL, payload_version INTEGER NOT NULL)""")
        for table,key in (("files","file_path"),("issues","issue_id"),("studies","group_key"),("series","group_key")):
            connection.execute(f"""CREATE TABLE {table} (
                run_id TEXT NOT NULL REFERENCES scan_runs(run_id), ordinal INTEGER NOT NULL,
                {key} TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id,{key}), UNIQUE(run_id,ordinal))""")
        connection.execute("CREATE INDEX scan_time ON scan_runs(finished_at,run_id)")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")

    def save(self,result):
        validate_result(result)
        self._guard(result)
        # Validate every whitelisted value and reference before any DB write.
        issue_map = {issue.issue_id:issue for issue in result.issues}
        sections = (("files",result.records,InventoryRecord,"file_path"),
                    ("issues",result.issues,QualityIssue,"issue_id"),
                    ("studies",result.studies,StudySummary,"group_key"),
                    ("series",result.series,SeriesSummary,"group_key"))
        payloads = {}
        for table,items,kind,key in sections:
            rows = []
            for ordinal,item in enumerate(items):
                payload = dumps(item)
                decode(loads(payload),kind,issue_map)
                rows.append((result.scan_run.run_id,ordinal,getattr(item,key),payload))
            payloads[table] = rows
        run = result.scan_run
        header = (run.run_id,run.started_at,run.finished_at,run.source_root,run.status,run.rule_version,
                  dumps(result.summary),dumps(run.notes),dumps(result.duplicates),
                  dumps({table:len(rows) for table,rows in payloads.items()}),PAYLOAD_VERSION)
        self.initialize()
        with self._connection(write=True) as connection:
            self._check(connection)
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM scan_runs WHERE run_id=?",(run.run_id,)).fetchone():
                raise StorageError("该批次已经保存；不会覆盖历史快照。")
            connection.execute("INSERT INTO scan_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",header)
            for table,rows in payloads.items():
                connection.executemany(f"INSERT INTO {table} VALUES (?,?,?,?)",rows)
            self._register_handling(connection,run.run_id)
            connection.commit()
        return run.run_id

    def list_scans(self):
        with self._connection() as connection:
            self._check(connection)
            result = []
            for row in connection.execute("""SELECT run_id,finished_at,source_root,status,summary_json
                                             FROM scan_runs ORDER BY finished_at DESC,run_id DESC"""):
                summary = decode(loads(row[4]),InventorySummary)
                result.append(SavedScan(*row[:4],summary.file_count,summary.issue_count))
            return tuple(result)

    def load(self,run_id):
        with self._connection() as connection:
            self._check(connection)
            connection.execute("BEGIN")  # One consistent read snapshot across all tables.
            return self._load_result(connection, run_id)

    def _load_result(self, connection, run_id):
        header = connection.execute("SELECT * FROM scan_runs WHERE run_id=?",(run_id,)).fetchone()
        if header is None:
            raise StorageError("所选扫描批次不存在。")
        if header[10] != PAYLOAD_VERSION:
            raise StorageError("扫描快照格式不受支持。")
        raw = {table:list(connection.execute(
            f"SELECT ordinal,payload FROM {table} WHERE run_id=? ORDER BY ordinal",(run_id,)))
               for table in ("files","issues","studies","series")}
        counts = loads(header[9])
        if counts != {table:len(rows) for table,rows in raw.items()}:
            raise StorageError("保存批次记录不完整。")
        if any([row[0] for row in rows] != list(range(len(rows))) for rows in raw.values()):
            raise StorageError("保存顺序或记录不完整。")
        issues = tuple(decode(loads(row[1]),QualityIssue) for row in raw["issues"])
        issue_map = {issue.issue_id:issue for issue in issues}
        summary = decode(loads(header[6]),InventorySummary)
        run = ScanRun(*header[:6],summary,decode(loads(header[7]),tuple[str,...]))
        result = InventoryResult(
            records=[decode(loads(row[1]),InventoryRecord,issue_map) for row in raw["files"]],
            summary=summary,duplicates=decode(loads(header[8]),dict[str,list[str]]),
            issues=issues,scan_run=run,
            studies=tuple(decode(loads(row[1]),StudySummary,issue_map) for row in raw["studies"]),
            series=tuple(decode(loads(row[1]),SeriesSummary,issue_map) for row in raw["series"]))
        validate_result(result)
        self._guard(result)
        return result
