"""Persistencia SQLite para el escáner de impresoras.

Este módulo gestiona la base de datos local donde se guardan las IPs del
listado y los resultados del último escaneo para poder recuperarlos después.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app_paths import APP_DIR


DEFAULT_DB_PATH = APP_DIR / "estado-impresoras" / "scanner.db"
LEGACY_DB_PATH = APP_DIR / "scanner.db"
COMMANDS_PATH = APP_DIR / "estado-impresoras" / "comandos_impresoras.json"
AREAS_PATH = APP_DIR / "estado-impresoras" / "areas.json"
DEFAULT_AREAS = [
    "INGENIERIA",
    "GARANTIA",
    "DESARROLLO",
    "MATERIAS-PRIMAS",
    "CONTROL",
    "NO BETA",
    "SUPERVISORES",
    "AR",
    "CONTADURIA",
    "VENTAS",
    "PLANEAMIENTO",
    "DESARROLLO1",
    "DESARROLLO2",
    "MATERIAS PRIMAS",
    "CONTROL1",
    "CONTROL2",
]


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
    migrate_legacy = db_path is None and not path.exists() and LEGACY_DB_PATH.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ips (
                ip TEXT PRIMARY KEY,
                marca TEXT NOT NULL DEFAULT 'Samsung',
                area TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ips)")}
        if "marca" not in columns:
            conn.execute("ALTER TABLE ips ADD COLUMN marca TEXT NOT NULL DEFAULT 'Samsung'")
        if "area" not in columns:
            conn.execute("ALTER TABLE ips ADD COLUMN area TEXT NOT NULL DEFAULT ''")
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

    if migrate_legacy:
        legacy = sqlite3.connect(LEGACY_DB_PATH)
        current = sqlite3.connect(path)
        try:
            legacy_rows = legacy.execute("SELECT ip FROM ips ORDER BY ip").fetchall()
            current.executemany(
                "INSERT OR IGNORE INTO ips(ip, marca) VALUES (?, 'Samsung')",
                legacy_rows,
            )
            result = legacy.execute(
                "SELECT payload FROM scan_results ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if result:
                current.execute("INSERT INTO scan_results(payload) VALUES (?)", result)
            current.commit()
        finally:
            legacy.close()
            current.close()
    return path


def save_ips(ips: list[str | dict], db_path: Optional[Path | str] = None) -> None:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM ips")
        conn.executemany(
            "INSERT INTO ips(ip, marca, area) VALUES (?, ?, ?)",
            [
                (item["ip"], item.get("marca", "Samsung"), item.get("area", ""))
                if isinstance(item, dict)
                else (item, "Samsung", "")
                for item in ips
            ],
        )
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


def load_printers(db_path: Optional[Path | str] = None) -> list[dict]:
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT ip, marca, area FROM ips ORDER BY ip").fetchall()
    finally:
        conn.close()
    return [{"ip": row[0], "marca": row[1], "area": row[2]} for row in rows]


def load_area_options(areas_path: Optional[Path | str] = None) -> list[str]:
    path = Path(areas_path or AREAS_PATH)
    if not path.exists():
        save_area_options(DEFAULT_AREAS, areas_path=path)
        return DEFAULT_AREAS.copy()
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_AREAS.copy()
    if not isinstance(payload, list):
        return DEFAULT_AREAS.copy()
    options = list(dict.fromkeys(
        str(area).strip() for area in payload if str(area).strip()
    ))
    return options or DEFAULT_AREAS.copy()


def save_area_options(areas: list[str], areas_path: Optional[Path | str] = None) -> None:
    path = Path(areas_path or AREAS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    options = list(dict.fromkeys(str(area).strip() for area in areas if str(area).strip()))
    with path.open("w", encoding="utf-8") as file:
        json.dump(options, file, indent=2, ensure_ascii=False)


def load_printer_commands(commands_path: Optional[Path | str] = None) -> list[dict]:
    path = Path(commands_path or COMMANDS_PATH)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_printer_commands(commands: list[dict], commands_path: Optional[Path | str] = None) -> None:
    path = Path(commands_path or COMMANDS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(sanitize_for_json(commands), file, indent=2, ensure_ascii=False)


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
