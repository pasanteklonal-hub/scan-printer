"""Rutas compartidas para ejecución normal y aplicaciones congeladas."""

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else PROJECT_DIR
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))


def resource_path(relative_path: str) -> Path:
	"""Devuelve un recurso persistente o, si no existe, el incluido en el bundle."""
	persistent = APP_DIR / relative_path
	return persistent if persistent.exists() else BUNDLE_DIR / relative_path
