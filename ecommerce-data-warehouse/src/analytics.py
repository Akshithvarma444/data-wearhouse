"""Load and execute the named queries in sql/analytical_queries.sql."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from src.config.settings import SQL_DIR

QUERY_FILE = SQL_DIR / "analytical_queries.sql"
_NAME_RE = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


def load_queries(path: Path = QUERY_FILE) -> dict[str, str]:
    """Parse ``-- name: <id>`` sections into an ordered {name: sql} mapping."""
    content = path.read_text(encoding="utf-8")
    matches = list(_NAME_RE.finditer(content))
    queries: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[match.end():end].strip().rstrip(";").strip()
        queries[match.group(1)] = body
    return queries


def run_query(engine: Engine, name: str, queries: dict[str, str] | None = None) -> pd.DataFrame:
    """Execute one named query and return the result as a DataFrame."""
    queries = queries or load_queries()
    if name not in queries:
        raise KeyError(f"unknown query '{name}'. Available: {', '.join(queries)}")
    with engine.connect() as conn:
        result = conn.execute(text(queries[name]))
        return pd.DataFrame(result.all(), columns=list(result.keys()))
