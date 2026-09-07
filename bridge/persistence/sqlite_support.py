# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W09.01 — Explicit SQLite connection lifecycle helpers.

Python's sqlite3 connection context manager commits/rolls back the
transaction but never closes the connection (docs: library/sqlite3).
Repositories that used ``with self._connect()`` leaked one connection per
call until GC; under sustained traffic this exhausts file descriptors.

Use :func:`managed_sqlite_connection` to get transaction + close semantics:

    with managed_sqlite_connection(self._connect) as connection:
        ...
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator


@contextlib.contextmanager
def managed_sqlite_connection(
    connect: Callable[[], sqlite3.Connection],
) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success, rolls back on error,
    and is always closed on exit."""
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()
