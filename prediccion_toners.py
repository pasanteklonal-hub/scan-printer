"""Predice cuándo se agotarán los tóners a partir de un snapshot JSON.

El snapshot no contiene el consumo histórico de cada impresora. Por eso el
modelo usa un promedio diario configurable y un rendimiento estimado por
modelo de impresora. Los resultados son aproximaciones para priorizar compras.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app_paths import APP_DIR, BUNDLE_DIR, resource_path


BASE_DIR = APP_DIR
CARPETA_ESTADOS = APP_DIR / "estado-impresoras"
CARPETA_ESTADOS_BUNDLE = BUNDLE_DIR / "estado-impresoras"
ARCHIVO_IMPRESORAS = resource_path("impresoras.xlsx")

# Páginas estimadas con un cartucho al 100%. Ajustar con datos del fabricante.
RENDIMIENTO_POR_MODELO = {
    "samsung ml-371x": 5_000,
    "ricoh sp 3710sf": 7_000,
    "hp laserjet cp1525nw": 2_000,
    "hp laserjet 5200": 12_000,
    "hp laserjet m402dn": 9_000,
}

# Se usa solo si un modelo no está incluido en la tabla de rendimientos.
RENDIMIENTO_POR_DEFECTO = 5_000
UMBRAL_CAMBIO_TONER = 18.0

COLUMNAS_REPORTE = [
    "IP",
    "Área",
    "Marca",
    "Modelo",
    "Número de Serie",
    "% de Tóner Actual",
    "Páginas Restantes Estimadas",
    "Días Restantes para Agotarse",
    "Fecha Estimada de Agotamiento",
]

COLUMNAS_PROXIMA_COMPRA = [
    "Área",
    "Marca",
    "Modelo",
    "Agotamiento estimado",
    "Horas restantes (Días)",
]


def fecha_desde_nombre(ruta: Path) -> datetime:
    """Extrae YYYYMMDD_HHMMSS de nombres como impresoras_20260916_144734.json."""
    coincidencia = re.search(r"(\d{8})_(\d{6})", ruta.name)
    if not coincidencia:
        raise ValueError(f"El nombre no contiene fecha y hora: {ruta.name}")
    return datetime.strptime(
        f"{coincidencia.group(1)}_{coincidencia.group(2)}", "%Y%m%d_%H%M%S"
    )


def normalizar_clave_toner(clave: Any) -> str:
    """Reduce nombres de cartuchos a una categoría comparable."""
    texto = re.sub(r"[^a-z0-9]+", " ", str(clave).lower()).strip()
    if texto in {"black", "negro", "1"} or "black" in texto or "negro" in texto:
        return "black"
    if any(color in texto for color in ("cyan", "cian")):
        return "cyan"
    if "magenta" in texto:
        return "magenta"
    if any(color in texto for color in ("yellow", "amarillo")):
        return "yellow"
    return texto or "unknown"


def normalizar_mac(mac: Any) -> str:
    return re.sub(r"[^0-9a-f]", "", str(mac or "").lower())


def cargar_areas() -> dict[str, str]:
    """Carga el sector de cada impresora desde la planilla base."""
    if not ARCHIVO_IMPRESORAS.exists():
        return {}
    try:
        tabla = pd.read_excel(ARCHIVO_IMPRESORAS)
    except (OSError, ValueError, ImportError):
        return {}
    columnas = {str(columna).strip().lower(): columna for columna in tabla.columns}
    columna_mac = columnas.get("mac")
    columna_area = columnas.get("sector") or columnas.get("área") or columnas.get("area")
    if not columna_mac or not columna_area:
        return {}
    return {
        normalizar_mac(mac): str(area).strip()
        for mac, area in zip(tabla[columna_mac], tabla[columna_area])
        if normalizar_mac(mac) and pd.notna(area) and str(area).strip()
    }


def niveles_toner(toner: Any) -> dict[str, float]:
    """Normaliza las claves y convierte los niveles a porcentajes válidos."""
    if not isinstance(toner, dict):
        return {}
    niveles: dict[str, float] = {}
    for clave, valor in toner.items():
        try:
            porcentaje = max(0.0, min(100.0, float(valor)))
        except (TypeError, ValueError):
            continue
        categoria = normalizar_clave_toner(clave)
        # Si hay dos lecturas para una categoría, usamos la más baja para
        # priorizar correctamente la impresora que se agotará primero.
        niveles[categoria] = min(niveles.get(categoria, 100.0), porcentaje)
    return niveles


def rendimiento_para_modelo(modelo: Any) -> int:
    modelo_normalizado = str(modelo or "").lower()
    for patron, rendimiento in RENDIMIENTO_POR_MODELO.items():
        if patron in modelo_normalizado:
            return rendimiento
    return RENDIMIENTO_POR_DEFECTO


def clave_impresora(registro: dict[str, Any]) -> str:
    """Identifica una impresora aunque cambie de IP entre snapshots."""
    return str(
        registro.get("numero_serie")
        or registro.get("mac")
        or registro.get("ip")
        or ""
    ).strip().lower()


def clave_modelo(registro: dict[str, Any]) -> str:
    return str(registro.get("modelo") or "").strip().lower()


def consumo_historico_por_impresora(
    historial: list[tuple[datetime, list[dict[str, Any]]]],
) -> dict[str, float]:
    """Devuelve páginas por hora usando deltas positivos entre snapshots."""
    observaciones: dict[str, list[tuple[datetime, int]]] = {}
    for momento, registros in historial:
        for registro in registros:
            clave = clave_impresora(registro)
            paginas = registro.get("paginas", {}).get("impresiones_totales")
            if not clave or paginas is None:
                continue
            try:
                observaciones.setdefault(clave, []).append((momento, int(paginas)))
            except (TypeError, ValueError):
                continue

    consumos: dict[str, float] = {}
    for clave, lecturas in observaciones.items():
        lecturas.sort(key=lambda lectura: lectura[0])
        tasas = []
        for anterior, actual in zip(lecturas, lecturas[1:]):
            horas = (actual[0] - anterior[0]).total_seconds() / 3600
            delta_paginas = actual[1] - anterior[1]
            # Los deltas negativos suelen indicar reinicio o reemplazo del
            # contador; no representan consumo y se descartan.
            if horas > 0 and delta_paginas > 0:
                tasas.append(delta_paginas / horas)
        if tasas:
            consumos[clave] = float(pd.Series(tasas).median())
    return consumos


def consumo_historico_por_modelo(
    historial: list[tuple[datetime, list[dict[str, Any]]]],
) -> dict[str, float]:
    """Calcula páginas por día frecuentes agrupando por modelo.

    La mediana de las cinco tasas más recientes por impresora evita que una
    lectura atrasada o un intervalo muy corto domine el modelo actual.
    """
    observaciones: dict[str, dict[str, list[tuple[datetime, int]]]] = {}
    for momento, registros in historial:
        for registro in registros:
            modelo = clave_modelo(registro)
            impresora = clave_impresora(registro)
            paginas = registro.get("paginas", {}).get("impresiones_totales")
            if not modelo or not impresora or paginas is None:
                continue
            try:
                observaciones.setdefault(modelo, {}).setdefault(impresora, []).append(
                    (momento, int(paginas))
                )
            except (TypeError, ValueError):
                continue

    consumos: dict[str, float] = {}
    for modelo, impresoras in observaciones.items():
        tasas = []
        for lecturas in impresoras.values():
            lecturas.sort(key=lambda lectura: lectura[0])
            tasas_impresora = []
            for anterior, actual in zip(lecturas, lecturas[1:]):
                dias = (actual[0] - anterior[0]).total_seconds() / 86400
                delta_paginas = actual[1] - anterior[1]
                if dias > 0 and delta_paginas > 0:
                    tasas_impresora.append(delta_paginas / dias)
            if tasas_impresora:
                tasas.append(float(pd.Series(tasas_impresora[-5:]).median()))
        if tasas:
            consumos[modelo] = float(pd.Series(tasas).median())
    return consumos


def construir_reporte(
    registros: list[dict[str, Any]],
    fecha_snapshot: datetime,
    hoy: date | None = None,
    consumos_hora: dict[str, float] | None = None,
    areas: dict[str, str] | None = None,
    momento_actual: datetime | None = None,
    consumos_modelo: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Construye el DataFrame final ordenado por días restantes."""
    if momento_actual is None:
        momento_actual = datetime.combine(hoy, datetime.min.time()) if hoy else datetime.now()
    filas = []
    for registro in registros:
        niveles = niveles_toner(registro.get("toner"))
        if not niveles or registro.get("error") is not None:
            continue
        porcentaje = min(niveles.values())
        rendimiento = rendimiento_para_modelo(registro.get("modelo"))
        porcentaje_utilizable = max(0.0, porcentaje - UMBRAL_CAMBIO_TONER)
        paginas_restantes = rendimiento * porcentaje_utilizable / 100
        paginas_dia = (consumos_modelo or {}).get(clave_modelo(registro))
        paginas_hora = (consumos_hora or {}).get(clave_impresora(registro))
        if paginas_dia and paginas_dia > 0:
            paginas_hora = paginas_dia / 24
        if not paginas_hora or paginas_hora <= 0:
            continue
        horas_restantes = paginas_restantes / paginas_hora
        dias_restantes = horas_restantes / 24
        filas.append(
            {
                "IP": registro.get("ip", ""),
                "Área": (areas or {}).get(normalizar_mac(registro.get("mac")), "Sin área asignada"),
                "Marca": registro.get("marca", ""),
                "Modelo": registro.get("modelo", ""),
                "Número de Serie": registro.get("numero_serie", ""),
                "% de Tóner Actual": round(porcentaje, 2),
                "Páginas Restantes Estimadas": round(paginas_restantes),
                "Días Restantes para Agotarse": round(dias_restantes, 2),
                "Fecha Estimada de Agotamiento": (
                    momento_actual + timedelta(hours=horas_restantes)
                ).strftime("%Y-%m-%d %H:%M"),
                "Consumo histórico (páginas/hora)": round(paginas_hora, 2),
            }
        )

    reporte = pd.DataFrame(filas, columns=COLUMNAS_REPORTE)
    if not reporte.empty:
        reporte = reporte.sort_values(
            "Días Restantes para Agotarse", ascending=True
        ).reset_index(drop=True)
    return reporte


def construir_reporte_proxima_compra(reporte: pd.DataFrame) -> pd.DataFrame:
    """Oculta datos operativos y conserva solo la información de compra."""
    if reporte.empty:
        return pd.DataFrame(columns=COLUMNAS_PROXIMA_COMPRA)
    if "Área" not in reporte.columns:
        reporte = reporte.assign(Área="Sin área asignada")
    salida = reporte.loc[reporte["Días Restantes para Agotarse"] < 10].copy()
    salida["Agotamiento estimado"] = salida["Fecha Estimada de Agotamiento"]
    salida["Horas restantes (Días)"] = salida.apply(
        lambda fila: f"{fila['Días Restantes para Agotarse'] * 24:.1f} ({fila['Días Restantes para Agotarse']:.1f})",
        axis=1,
    )
    return salida[COLUMNAS_PROXIMA_COMPRA].reset_index(drop=True)


def cargar_snapshot(ruta: Path) -> tuple[list[dict[str, Any]], datetime]:
    with ruta.open("r", encoding="utf-8") as archivo:
        datos = json.load(archivo)
    if not isinstance(datos, list):
        raise ValueError("El snapshot debe ser una lista de objetos JSON")
    return datos, fecha_desde_nombre(ruta)


def cargar_historial() -> list[tuple[datetime, list[dict[str, Any]]]]:
    """Carga todos los estados fechados para calcular consumos históricos."""
    historial = []
    carpetas = [CARPETA_ESTADOS]
    if not CARPETA_ESTADOS.exists() and CARPETA_ESTADOS_BUNDLE != CARPETA_ESTADOS:
        carpetas.append(CARPETA_ESTADOS_BUNDLE)
    for carpeta in carpetas:
        for ruta in carpeta.glob("impresoras_*.json"):
            try:
                registros, momento = cargar_snapshot(ruta)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            historial.append((momento, registros))
    return sorted(historial, key=lambda item: item[0])


def encontrar_snapshot() -> Path:
    carpeta = CARPETA_ESTADOS if CARPETA_ESTADOS.exists() else CARPETA_ESTADOS_BUNDLE
    archivos = sorted(carpeta.glob("impresoras_*.json"), key=fecha_desde_nombre)
    if not archivos:
        raise FileNotFoundError(f"No hay snapshots en {CARPETA_ESTADOS}")
    return archivos[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "snapshot",
        nargs="?",
        type=Path,
        help="JSON de estado; por defecto se usa el snapshot más reciente.",
    )
    parser.add_argument("--csv", type=Path, help="Ruta opcional para exportar el reporte.")
    args = parser.parse_args()

    ruta = args.snapshot or encontrar_snapshot()
    registros, fecha_snapshot = cargar_snapshot(ruta)
    reporte = construir_reporte(
        registros,
        fecha_snapshot,
        consumos_hora=consumo_historico_por_impresora(cargar_historial()),
        consumos_modelo=consumo_historico_por_modelo(cargar_historial()),
        areas=cargar_areas(),
    )
    reporte_compra = construir_reporte_proxima_compra(reporte)
    if args.csv:
        reporte_compra.to_csv(args.csv, index=False, encoding="utf-8-sig")
    print(f"Snapshot: {ruta.name} ({fecha_snapshot:%Y-%m-%d %H:%M:%S})")
    print(reporte_compra.to_string(index=False))


if __name__ == "__main__":
    main()