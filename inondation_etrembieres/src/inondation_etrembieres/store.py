"""DuckDB query layer over the raw parquet files.

Parquet stays the source of truth (immutable, git/artifact friendly);
DuckDB builds tables from it for fast SQL queries.
"""
from pathlib import Path

import duckdb
import pandas as pd

from .config import DATA_PROCESSED, DATA_RAW

DB_PATH = DATA_PROCESSED / "inondation.duckdb"


def build(db_path: Path = DB_PATH, raw: Path = DATA_RAW) -> None:
    """(Re)create the tables from data/raw/**.parquet."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    weather = (raw / "weather" / "*.parquet").as_posix()
    discharge = (raw / "discharge" / "*_daily.parquet").as_posix()
    with duckdb.connect(str(db_path)) as con:
        con.execute(f"""
            CREATE OR REPLACE TABLE weather_daily AS
            SELECT regexp_extract(filename, '([^/]+)\\.parquet$', 1) AS point, * EXCLUDE (filename)
            FROM read_parquet('{weather}', filename = true)
        """)
        if list((raw / "discharge").glob("*_daily.parquet")):
            con.execute(f"""
                CREATE OR REPLACE TABLE discharge_daily AS
                SELECT regexp_extract(filename, '([^/]+)_daily\\.parquet$', 1) AS station, * EXCLUDE (filename)
                FROM read_parquet('{discharge}', filename = true)
            """)


def query(sql: str, db_path: Path = DB_PATH, **params) -> pd.DataFrame:
    """Run a read-only SQL query, e.g. query("SELECT * FROM weather_daily WHERE point = $p", p="flaine")."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        return con.execute(sql, params).df()
