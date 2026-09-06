"""Persistence infrastructure (W02/W04/W09): SQLite lifecycle, Repository SPI."""

from bridge.persistence.decision_ledger_repository import (
    DecisionLedgerError,
    DecisionLedgerRepository,
    LedgerConflictError,
    LedgerEntry,
    LedgerIntegrityError,
    SQLiteDecisionLedgerRepository,
)
from bridge.persistence.sqlite_support import managed_sqlite_connection

__all__ = [
    "DecisionLedgerError",
    "DecisionLedgerRepository",
    "LedgerConflictError",
    "LedgerEntry",
    "LedgerIntegrityError",
    "SQLiteDecisionLedgerRepository",
    "managed_sqlite_connection",
]
