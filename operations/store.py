"""Explicit local SQLite store. Immutable facts precede rebuildable exports.

This is a trusted-host journal, not nonrepudiation against an administrator.
No transaction in this module performs an external action or touches pilot state.
"""
from contextlib import contextmanager
import csv
import ctypes
import io
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time

from .clock import parse_timestamp, utc_now
from .contracts import read_record, schema_snapshot
from .errors import BusyError, ConflictError, IntegrityError, StoreError, VersionError
from .migrations import initialize_schema, migration_plan, require_current
from .serialization import canonical_bytes, digest, load_json

DATABASE_NAME = "operations.sqlite3"
RESOURCE_RECORDS = frozenset(("capacity_snapshot","reservation","resource_action"))
PROTECTED_RECORDS = RESOURCE_RECORDS | {"readiness_snapshot","human_decision","execution_attempt","cohort_inclusion","case_followup","portfolio_decision","documentation_assignment"}
_MAX_SQL_INT = 2**63 - 1


def _local_path(path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StoreError("Operational paths must not traverse links or reparse points")
    if os.name == "nt":
        if str(path).startswith("\\\\") or ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise StoreError("Network operational stores are not supported")
    return path


def _new_private_directory(path):
    path = _local_path(path)
    if not path.parent.is_dir():
        raise StoreError("Private store parent must already exist")
    try:
        path.mkdir(mode=0o700)
        if os.name == "nt":
            identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True)
            sid = next(csv.reader(io.StringIO(identity.stdout.strip())))[1]
            if not sid.startswith("S-1-"):
                raise StoreError("Cannot determine host user SID")
            # Protect only this newly created directory; never alter a parent's ACL.
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise StoreError("Cannot create a new private operational directory") from exc
    return path


@contextmanager
def _connection(path, *, write=False, timeout=2.0, check_version=True, recovery=False):
    path = _local_path(path)
    if not path.is_file():
        raise StoreError("Operational database is missing; initialize explicitly")
    try:
        with path.open("rb") as stream:
            header = stream.read(100)
    except OSError as exc:
        raise StoreError("Operational database header is unreadable") from exc
    if len(header) < 100 or header[:16] != b"SQLite format 3\0":
        raise IntegrityError("Not a SQLite operational database")
    # mode=ro alone can create WAL sidecars; immutable=1 could ignore committed
    # WAL facts. This first store supports rollback journals only.
    if header[18:20] != b"\x01\x01":
        raise StoreError("WAL or unknown SQLite journal format is unsupported")
    if check_version and int.from_bytes(header[60:64], "big") != 1:
        raise VersionError("Unsupported store version; refused before SQLite can recover or modify it")
    if not recovery:
        journal = Path(str(path) + "-journal")
        deadline = time.monotonic() + timeout if write else time.monotonic()
        # A supported concurrent writer may currently own this journal. Give it
        # the bounded busy interval to finish, without asking SQLite to recover.
        while journal.exists() and write and time.monotonic() < deadline:
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if journal.exists():
            raise StoreError("Journal is active or interrupted; retry after the writer finishes or explicitly recover")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if not write:
            connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        if check_version:
            require_current(connection)
            contract = connection.execute("SELECT value FROM store_metadata WHERE key='contract_digest'").fetchone()
            if contract is None or contract[0] != digest(schema_snapshot(), domain="schema"):
                raise IntegrityError("Stored operational contract differs from this reader; explicit compatibility is required")
        yield connection
        if write:
            connection.commit()
    except sqlite3.OperationalError as exc:
        if getattr(exc, "sqlite_errorcode", 0) & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise BusyError("Operational store is busy; no request was committed by this call") from exc
        raise StoreError("Operational SQLite request failed") from exc
    except sqlite3.DatabaseError as exc:
        raise IntegrityError("Operational database integrity failure") from exc
    finally:
        if connection is not None:
            connection.close()  # Rolls back any uncommitted transaction.


def initialize(directory):
    """Create a new private directory/store. Existing directories are refused."""
    directory = _new_private_directory(directory)
    path = directory / DATABASE_NAME
    connection = None
    try:
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        initialize_schema(connection, applied_at=utc_now(), schema_digest=digest(schema_snapshot(), domain="schema"))
        _rebuild(connection)
        connection.commit()
    except sqlite3.DatabaseError as exc:
        raise StoreError("Cannot initialize the operational database; no existing history was replaced") from exc
    finally:
        if connection is not None:
            connection.close()
    return path


def _receipt(row):
    return {key: row[key] for key in ("request_key", "request_digest", "record_id", "sequence", "committed_at")}


def _validated_row(row):
    record = read_record(load_json(row["payload"]))
    if canonical_bytes(record) != row["payload"] or digest(record) != row["digest"] or record["record_id"] != row["record_id"] or record["record_type"] != row["record_type"]:
        raise IntegrityError("Stored record bytes or identity do not match their digest")
    return record


def _events(connection):
    previous = None
    events = []
    for sequence, row in enumerate(connection.execute("SELECT e.sequence,e.record_id,e.payload AS event_payload,e.digest AS event_digest,r.record_type,r.payload,r.digest FROM operational_events e JOIN records r USING(record_id) ORDER BY e.sequence"), 1):
        record = _validated_row(row)
        event = load_json(row["event_payload"])
        expected = {"sequence": sequence, "record_id": row["record_id"], "record_digest": row["digest"], "previous_digest": previous}
        if event != expected or row["sequence"] != sequence or canonical_bytes(event) != row["event_payload"] or digest(event, domain="event") != row["event_digest"]:
            raise IntegrityError("Operational event chain is inconsistent")
        previous = row["event_digest"]
        events.append((sequence, record))
    if len(events) != connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]:
        raise IntegrityError("Record has no corresponding operational event")
    return events


def _projection(events):
    records, cases, outcomes, superseded, case_kinds = [], {}, {}, set(), {}
    for sequence, record in events:
        records.append((record["record_id"], record["record_type"], sequence))
        if record["record_type"] != "outcome_event":
            continue
        identity = (record["synthetic"], record["cohort_id"])
        if case_kinds.setdefault(record["case_id"], identity) != identity:
            raise IntegrityError("Case cannot mix synthetic status or cohort membership")
        previous = record["supersedes_event_id"]
        if previous is not None:
            original = outcomes.get(previous)
            if original is None or previous in superseded or any(original[key] != record[key] for key in ("case_id", "cohort_id", "synthetic")):
                raise IntegrityError("Correction must replace one prior outcome in the same case/cohort")
            superseded.add(previous)
        outcomes[record["record_id"]] = record
    chronological = sorted(((sequence, record) for sequence, record in events if record["record_type"] == "outcome_event"), key=lambda pair: (parse_timestamp(pair[1]["occurred_at"]), pair[0]))
    for sequence, record in chronological:
        if record["record_type"] != "outcome_event" or record["record_id"] in superseded:
            continue
        case = cases.setdefault(record["case_id"], [record["case_id"], 0, 0, 0, 0, "", "", 0])
        cash, minutes = record["cash_delta_cents"], record["operator_minutes"]
        case[1] += cash if cash is not None else 0
        case[2] += cash is None
        case[3] += minutes if minutes is not None else 0
        case[4] += minutes is None
        case[5:] = [record["physical_state"], record["financial_state"], sequence]
        if any(abs(case[index]) > _MAX_SQL_INT for index in (1, 2, 3, 4)):
            raise IntegrityError("Projection exceeds supported signed 64-bit range")
    return records, list(cases.values())


def _rebuild(connection):
    from .capacity import projection_rows
    from .decisions import _manual_attempts
    events = _events(connection)
    _manual_attempts(events)
    records, cases = _projection(events)
    resources = projection_rows(events)
    connection.execute("DELETE FROM record_projection")
    connection.execute("DELETE FROM case_projection")
    connection.executemany("INSERT INTO record_projection VALUES (?,?,?)", records)
    connection.executemany("INSERT INTO case_projection VALUES (?,?,?,?,?,?,?,?)", cases)
    connection.execute("DELETE FROM resource_projection")
    connection.executemany("INSERT INTO resource_projection VALUES (?,?,?,?)", resources)
    connection.execute("INSERT OR REPLACE INTO projection_versions VALUES ('records_and_cases',1,?)", (len(events),))


def append(path, record, *, request_key, timeout=2.0):
    """Atomic local append with full-payload retry identity; no external effect."""
    record = read_record(record)
    if record["record_type"] in PROTECTED_RECORDS:
        raise IntegrityError("Use the specialized resource service for protected operational records")
    with _connection(path, write=True, timeout=timeout) as connection:
        return _append(connection,record,request_key=request_key)


def _append(connection, record, *, request_key):
    """Internal writer; caller owns the one active transaction and domain checks."""
    record = read_record(record)
    if type(request_key) is not str or not request_key.strip():
        raise ConflictError("A nonempty request key is required")
    request_digest = digest({"operation": "append", "record": record}, domain="request")
    payload_digest = digest(record)
    old = connection.execute("SELECT * FROM request_receipts WHERE request_key=?", (request_key,)).fetchone()
    if old is not None:
        if old["request_digest"] != request_digest:
            raise ConflictError("Request key was already used for a different complete payload")
        saved = connection.execute("SELECT * FROM records WHERE record_id=?", (old["record_id"],)).fetchone()
        if saved is None or _validated_row(saved) != record:
            raise IntegrityError("Receipt disagrees with stored record")
        return _receipt(old)
    if connection.execute("SELECT 1 FROM records WHERE record_id=?", (record["record_id"],)).fetchone():
        raise ConflictError("Record identity already exists; use the original request key to retry")
    previous = connection.execute("SELECT sequence,digest FROM operational_events ORDER BY sequence DESC LIMIT 1").fetchone()
    sequence = previous["sequence"] + 1 if previous else 1
    event = {"sequence": sequence, "record_id": record["record_id"], "record_digest": payload_digest, "previous_digest": previous["digest"] if previous else None}
    connection.execute("INSERT INTO records VALUES (?,?,?,?)", (record["record_id"], record["record_type"], canonical_bytes(record), payload_digest))
    connection.execute("INSERT INTO operational_events VALUES (?,?,?,?)", (sequence, record["record_id"], canonical_bytes(event), digest(event, domain="event")))
    committed_at = utc_now()
    connection.execute("INSERT INTO request_receipts VALUES (?,?,?,?,?)", (request_key, request_digest, record["record_id"], sequence, committed_at))
    _rebuild(connection)
    return {"request_key": request_key, "request_digest": request_digest, "record_id": record["record_id"], "sequence": sequence, "committed_at": committed_at}


def get_record(path, record_id):
    with _connection(path) as connection:
        row = connection.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
        return _validated_row(row) if row else None


def status(path):
    with _connection(path) as connection:
        # Derive from retained events so a missing/stale cache cannot invent a balance.
        events = _events(connection)
        _, cases = _projection(events)
        names = ("case_id", "known_cash_delta_cents", "unknown_cash_events", "operator_minutes", "unknown_time_events", "physical_state", "financial_state", "last_sequence")
        return {"schema_version": 1, "records": len(events), "events": len(events), "receipts": connection.execute("SELECT COUNT(*) FROM request_receipts").fetchone()[0], "cases": [dict(zip(names, case)) for case in sorted(cases)], "purchase_authorized": False}


def rebuild_projections(path):
    with _connection(path, write=True) as connection:
        _rebuild(connection)


def recover(path):
    """Explicitly let SQLite recover a rollback journal, then rebuild caches."""
    with _connection(path, write=True, recovery=True) as connection:
        _rebuild(connection)
    return integrity_scan(path)


def migration_status(path):
    with _connection(path, check_version=False) as connection:
        return migration_plan(connection)


def integrity_scan(path):
    from .capacity import projection_rows
    from .decisions import _manual_attempts
    with _connection(path) as connection:
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"] or list(connection.execute("PRAGMA foreign_key_check")):
            raise IntegrityError("SQLite structural integrity check failed")
        events = _events(connection)
        _manual_attempts(events)
        receipts = list(connection.execute("SELECT * FROM request_receipts ORDER BY sequence"))
        if len(receipts) != len(events):
            raise IntegrityError("Receipt/event count mismatch")
        for (sequence, record), receipt in zip(events, receipts):
            expected = digest({"operation": "append", "record": record}, domain="request")
            if receipt["sequence"] != sequence or receipt["record_id"] != record["record_id"] or receipt["request_digest"] != expected:
                raise IntegrityError("Receipt payload or event binding mismatch")
        records, cases = _projection(events)
        actual_records = [tuple(row) for row in connection.execute("SELECT * FROM record_projection ORDER BY sequence")]
        actual_cases = [list(row) for row in connection.execute("SELECT * FROM case_projection ORDER BY case_id")]
        actual_resources = [tuple(row) for row in connection.execute("SELECT * FROM resource_projection ORDER BY scope,synthetic")]
        projection = connection.execute("SELECT * FROM projection_versions WHERE name='records_and_cases'").fetchone()
        return {"integrity": "ok", "events": len(events), "projections_current": actual_records == records and actual_cases == sorted(cases) and actual_resources == projection_rows(events) and projection is not None and tuple(projection) == ("records_and_cases", 1, len(events)), "administrator_tampering_detectable": False}


def export_records(path, destination):
    """Regenerate a derived export after commit. Never overwrite retained inputs."""
    destination = _local_path(destination)
    if destination.exists():
        raise StoreError("Export destination must be new")
    with _connection(path) as connection:
        records = [record for _, record in _events(connection)]
    try:
        with destination.open("xb") as stream:
            stream.write(canonical_bytes({"schema_version": 1, "records": records, "purchase_authorized": False}))
    except OSError as exc:
        raise StoreError("Export failed; committed records remain durable and can be exported to a new file") from exc
    return destination


def backup(path, directory):
    """SQLite consistent backup to a new private directory, never over history."""
    target = _new_private_directory(directory) / DATABASE_NAME
    with _connection(path) as source:
        # Establish the source snapshot before copying, using SQLite's backup API.
        _events(source)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    integrity_scan(target)
    return target


def restore(backup_path, directory):
    """Recovery is a new copy. No API restores old balances over existing state."""
    integrity_scan(backup_path)
    return backup(backup_path, directory)
