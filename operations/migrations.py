"""Explicit store versioning. Reads never execute migrations."""
from .errors import VersionError

SCHEMA_VERSION = 1
DDL = (
    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
    "CREATE TABLE store_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE records (record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, payload BLOB NOT NULL, digest TEXT NOT NULL UNIQUE)",
    "CREATE TABLE operational_events (sequence INTEGER PRIMARY KEY, record_id TEXT NOT NULL UNIQUE REFERENCES records(record_id), payload BLOB NOT NULL, digest TEXT NOT NULL UNIQUE)",
    "CREATE TABLE request_receipts (request_key TEXT PRIMARY KEY, request_digest TEXT NOT NULL, record_id TEXT NOT NULL UNIQUE REFERENCES records(record_id), sequence INTEGER NOT NULL UNIQUE REFERENCES operational_events(sequence), committed_at TEXT NOT NULL)",
    "CREATE TABLE record_projection (record_id TEXT PRIMARY KEY REFERENCES records(record_id), record_type TEXT NOT NULL, sequence INTEGER NOT NULL)",
    "CREATE TABLE case_projection (case_id TEXT PRIMARY KEY, known_cash_delta_cents INTEGER NOT NULL, unknown_cash_events INTEGER NOT NULL, operator_minutes INTEGER NOT NULL, unknown_time_events INTEGER NOT NULL, physical_state TEXT NOT NULL, financial_state TEXT NOT NULL, last_sequence INTEGER NOT NULL)",
    "CREATE TABLE projection_versions (name TEXT PRIMARY KEY, version INTEGER NOT NULL, through_sequence INTEGER NOT NULL)",
    "CREATE TABLE resource_projection (scope TEXT NOT NULL, synthetic INTEGER NOT NULL, payload BLOB NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(scope,synthetic))",
)


def initialize_schema(connection, *, applied_at, schema_digest):
    for statement in DDL:
        connection.execute(statement)
    for table in ("records", "operational_events", "request_receipts", "schema_migrations", "store_metadata"):
        for action in ("UPDATE", "DELETE"):
            connection.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable operational history'); END")
    connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (SCHEMA_VERSION, applied_at))
    connection.execute("INSERT INTO store_metadata VALUES ('contract_digest', ?)", (schema_digest,))
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def migration_plan(connection):
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    return {"current_version": current, "target_version": SCHEMA_VERSION, "supported": current == SCHEMA_VERSION, "steps": [], "writes": False}


def require_current(connection):
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        raise VersionError(f"Store schema {current} is unsupported; expected {SCHEMA_VERSION}. No automatic migration is available.")
