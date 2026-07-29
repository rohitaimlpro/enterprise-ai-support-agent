"""
Cross-dialect column types.

Production runs on Postgres (native UUID + JSONB), but the test suite
uses in-memory SQLite for speed. These wrappers store UUIDs as CHAR(36)
and JSON as TEXT on SQLite while using Postgres's native types when
available, so `models.py` doesn't need dialect-specific branches.
"""

import uuid

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, JSON, TypeDecorator


class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return str(value)


class JSONType(TypeDecorator):
    """Plain JSON everywhere; Postgres gets JSONB via variant in models.py if needed."""

    impl = JSON
    cache_ok = True
