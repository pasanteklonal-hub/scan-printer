"""Persistencia SQLite para el escáner de impresoras.

Este módulo gestiona la base de datos local donde se guardan las IPs del
listado y los resultados del último escaneo para poder recuperarlos después.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


DEFAULT_DB_PATH = Path(__file__).with_name("scanner.db")


def sanitize_for_json(value: Any) -> Any:
    """Convierte valores no serializables a JSON-safe strings."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)


def init_db(db_path: Optional[Path | str] = None) -> Path:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ips (
                ip TEXT PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def save_ips(ips: list[str], db_path: Optional[Path | str] = None) -> None:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM ips")
        conn.executemany("INSERT INTO ips(ip) VALUES (?)", [(ip,) for ip in ips])
        conn.commit()
    finally:
        conn.close()


def load_ips(db_path: Optional[Path | str] = None) -> list[str]:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT ip FROM ips ORDER BY ip").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def save_scan_results(results: list[dict], db_path: Optional[Path | str] = None) -> None:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        payload = sanitize_for_json(results)
        conn.execute("DELETE FROM scan_results")
        conn.execute(
            "INSERT INTO scan_results(payload) VALUES (?)",
            (json.dumps(payload, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()


def load_last_results(db_path: Optional[Path | str] = None) -> list[dict]:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT payload FROM scan_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return []
    return json.loads(row[0])
