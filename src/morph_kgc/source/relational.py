from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Relational database adapter
============================
Implements DataSourceAdapter for all RDBMS sources (MySQL, MariaDB, MSSQL,
Oracle, PostgreSQL, SQLite, Databricks, Snowflake) plus the generic RDB type.

Public surface
--------------
RelationalAdapter — DataSourceAdapter implementation
get_rdb_reference_datatype() — used by materializer for SQL type inference
"""

import logging
from typing import Any

import pandas as pd

from ..constants.misc import LOGGING_NAMESPACE
from ..constants.rml import RML_TABLE_NAME, RML_QUERY
from ..constants.sources import MYSQL, MARIADB, MSSQL, DATABRICKS
from ..constants.xsd import *

LOGGER = logging.getLogger(LOGGING_NAMESPACE)


SQL_RDF_DATATYPE: dict[str | Any, str | Any] = {
    # Python dicts preserve insertion order, mind types are not intercepted by
    # the wrong, shorter key before ever reaching their correct match

    # binary
    'LONG RAW': XSD_HEX_BINARY,
    'VARBINARY': XSD_HEX_BINARY,
    'BINARY': XSD_HEX_BINARY,
    'BLOB': XSD_HEX_BINARY,
    'BFILE': XSD_HEX_BINARY,
    'RAW': XSD_HEX_BINARY,

    # boolean (must precede INT-family keys)
    'TINYINT': XSD_BOOLEAN,
    'BOOLEAN': XSD_BOOLEAN,
    'BOOL': XSD_BOOLEAN,

    # integer
    'BIGSERIAL': XSD_INTEGER,
    'SMALLSERIAL': XSD_INTEGER,
    'SMALLINT': XSD_INTEGER,
    'BIGINT': XSD_INTEGER,
    'SERIAL2': XSD_INTEGER,
    'SERIAL4': XSD_INTEGER,
    'SERIAL8': XSD_INTEGER,
    'INTEGER': XSD_INTEGER,
    'INT8': XSD_INTEGER,
    'INT4': XSD_INTEGER,
    'INT2': XSD_INTEGER,
    'INT': XSD_INTEGER,

    # decimal
    'DECIMAL': XSD_DECIMAL,
    'NUMERIC': XSD_DECIMAL,

    # double / float (longer/more specific keys first)
    'DOUBLE PRECISION': XSD_DOUBLE,
    'FLOAT8': XSD_DOUBLE,
    'DOUBLE': XSD_DOUBLE,
    'NUMBER': XSD_DOUBLE,
    'FLOAT': XSD_DOUBLE,
    'REAL': XSD_DOUBLE,

    # temporal (must precede shorter substrings, e.g. DATE before it's swallowed by nothing,
    # but DATETIME/TIMESTAMP must precede DATE/TIME)
    'TIMESTAMP': XSD_DATETIME,
    'DATETIME': XSD_DATETIME,
    'DATE': XSD_DATE,
    'TIME': XSD_TIME,

    # string / text
    'NVARCHAR': XSD_STRING,
    'VARCHAR': XSD_STRING,
    'NCHAR': XSD_STRING,
    'CHAR': XSD_STRING,
    'CLOB': XSD_STRING,
    'TEXT': XSD_STRING,
    'STRING': XSD_STRING,
    'UUID': XSD_STRING,
    'JSON': XSD_STRING,
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _replace_query_enclosing_characters(sql_query: str, db_dialect: str) -> str:
    """Swap backtick identifier quoting for dialect-specific quoting."""
    if db_dialect in (MYSQL, MARIADB):
        # backticks are already correct for MySQL/MariaDB
        return sql_query

    if db_dialect == MSSQL:
        # backticks alternate open/close; can't be done with a single replace
        square_brackets = ['[', ']']
        num_enclosing_char = 0
        result = []
        for char in sql_query:
            if char == '`':
                result.append(square_brackets[num_enclosing_char % 2])
                num_enclosing_char += 1
            else:
                result.append(char)
        return ''.join(result)

    if db_dialect == DATABRICKS:
        # Databricks doesn't require identifier quoting; strip backticks
        return sql_query.replace('`', '')

    # ANSI-compliant dialects (Oracle, PostgreSQL, SQLite, Snowflake, default)
    return sql_query.replace('`', '"')

def _relational_db_connection(config, source_name: str):
    """Return (connection, dialect_string) for *source_name*."""
    import ast

    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    connect_args = (
        ast.literal_eval(config.get_connect_args(source_name))
        if config.has_connect_args(source_name)
        else {}
    )

    engine = create_engine(
        config.get_db_url(source_name), connect_args=connect_args, poolclass=NullPool
    )
    connection = engine.connect()
    dialect = engine.dialect.name.upper()

    return connection, dialect

def _get_column_table_datatype(config, source_name: str, table_name: str, column_name: str) -> str | None:
    """
    Query the information schema to obtain the SQL datatype of *column_name*,
    mapped to its corresponding XSD datatype via SQL_RDF_DATATYPE.
    Returns None when the column cannot be found (views, CTEs, etc.) or when
    the SQL datatype has no known XSD mapping.
    """
    from sqlalchemy import inspect

    connection, _ = _relational_db_connection(config, source_name)

    insp = inspect(connection)
    try:
        columns = insp.get_columns(table_name)
    except Exception:
        return None

    data_type = None
    for col in columns:
        if col["name"].lower() == column_name.lower():
            data_type = str(col["type"]).upper()
            break

    if data_type is None:
        return None

    for sql_type, xsd_type in SQL_RDF_DATATYPE.items():
        if sql_type in data_type:
            return xsd_type

    return None

def _build_sql_query(config, rml_rule, references) -> str | None:
    """
    Construct the SQL SELECT that fetches exactly the *references* columns.
    Returns None when the rule has no column references (all constants).
    """
    col_refs = list(references)
    if not col_refs:
        return None

    ls_type = rml_rule.logical_source.value_type
    ls_value = rml_rule.logical_source.value

    if ls_type == RML_QUERY:
        return ls_value
    elif ls_type == RML_TABLE_NAME and len(references):
        quoted = ", ".join(f'"{r}"' for r in col_refs)
        from_clause = f'"{ls_value}"'
        conditions = " AND ".join(f'"{r}" IS NOT NULL' for r in col_refs)
        filter_null = f" WHERE {conditions}"

        return f"SELECT {quoted} FROM {from_clause}{filter_null}"
    else:
        return None


# ── Public helpers ────────────────────────────────────────────────────────────

def get_rdb_reference_datatype(config, logical_source, reference: str) -> str | None:
    """
    Return the SQL datatype string for *reference* in *rml_rule*'s table.
    Used by the materializer for automatic XSD datatype assignment.
    Returns None for query-based sources or when the column is not found.
    """
    ls_type = logical_source.value_type
    ls_value = logical_source.value

    if ls_type in (RML_QUERY, RML_TABLE_NAME):
        return None  # cannot inspect arbitrary SQL queries

    return _get_column_table_datatype(
        config, logical_source.name, ls_value, reference
    )

# ── Adapter ───────────────────────────────────────────────────────────────────

class RelationalAdapter:
    """DataSourceAdapter for relational databases."""

    def get_data(
        self,
        config: Any,
        rml_rule: Any,
        references: set[str],
        python_source: dict | None = None,
    ) -> pd.DataFrame:
        sql_query = _build_sql_query(config, rml_rule, references)
        if sql_query is None:
            return pd.DataFrame(columns=list(references))

        conn, dialect = _relational_db_connection(config, rml_rule.logical_source.name)
        sql_query = _replace_query_enclosing_characters(sql_query, dialect)

        LOGGER.debug(
            f"SQL query for mapping rule `{rml_rule.triples_map_id}`: [{sql_query}]"
        )

        return pd.read_sql_query(sql_query, con=conn, coerce_float=False)