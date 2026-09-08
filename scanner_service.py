"""Lógica de negocio para consultar impresoras Samsung SyncThru. 
Aquí se encuentra la validación de IPs, la limpieza de datos y la consulta asíncrona de cada impresora 
para obtener modelo, estado, porcentaje de tóner y contadores de páginas. """ 

import asyncio 
import re 
from typing import Callable, Optional 
import xml.etree.ElementTree as ET 

from scriptGeneral import obtener_estado_toner

try: 
    import aiohttp 
except ImportError:  # pragma: no cover - handled at runtime 
    aiohttp = None 

try: 
    from pysyncthru import SyncThru 
except ImportError:  # pragma: no cover - handled at runtime 
    SyncThru = None 

IP_REGEX = re.compile( 
    r"^(https?://)?" 
    r"((25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}" 
    r"(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)$" 
) 

def limpiar_ip(texto: str) -> str: 
    """Quita espacios y prefijo http(s):// si lo tiene, deja solo la IP.""" 
    texto = texto.strip() 
    texto = re.sub(r"^https?://", "", texto) 
    texto = texto.rstrip("/") 
    return texto 

# --- SUBTAREAS DE EXTRACCIÓN DE DATOS ---

def _extraer_toner(printer: SyncThru) -> dict:
    """Subtarea encargada de mapear los niveles de tóner instalados."""
    toners = {} 
    try: 
        for color, info in (printer.toner_status() or {}).items(): 
            if not info: 
                continue 
            toners[color] = info.get("remaining") 
    except Exception: 
        pass 
    return toners

def _extraer_paginas(printer: SyncThru) -> dict:
    """Subtarea encargada de procesar e indexar los contadores de uso."""
    contadores = {}
    try:
        datos_raw = printer.raw() or {}
        
        # 1. Intentar con el bloque de información de gastos (billingCounter)
        billing = datos_raw.get("billingCounter", {})
        if billing:
            if "billing_total" in billing:
                contadores["impresiones_totales"] = billing["billing_total"].get("infTotal") or billing["billing_total"].get("total")
            if "billing_simplex" in billing:
                contadores["mono_simplex"] = billing["billing_simplex"].get("total")
            if "billing_duplex" in billing:
                contadores["mono_duplex"] = billing["billing_duplex"].get("total")

        # 2. Si viene vacío, intentar con el bloque secundario (usageCounter)
        if not contadores:
            usage = datos_raw.get("usageCounter", {})
            if usage:
                contadores["mono_simplex"] = usage.get("simplex", {}).get("mono")
                contadores["mono_duplex"] = usage.get("duplex", {}).get("mono")
                contadores["impresiones_totales"] = usage.get("total", {}).get("mono")

        # 3. Respaldo final con métodos estándar de la librería
        if not contadores or not contadores.get("impresiones_totales"):
            total_std = printer.print_count()
            if total_std and total_std != -1:
                contadores["impresiones_totales"] = total_std

    except Exception:
        pass
    
    # Retorna solo los valores numéricos válidos parseados como enteros
    return {k: int(v) for k, v in contadores.items() if v is not None}

def _extraer_mac(printer: SyncThru) -> Optional[str]:
    """Subtarea encargada de aislar la dirección MAC del bloque identity."""
    try:
        datos_raw = printer.raw() or {}
        identity = datos_raw.get("identity", {})
        mac = identity.get("mac_addr")
        if mac:
            return str(mac).strip().upper()
    except Exception:
        pass
    return None


def _extraer_numero_serie(printer: SyncThru) -> Optional[str]:
    try:
        datos_raw = printer.raw() or {}
        identity = datos_raw.get("identity", {})
        numero_serie = (
            identity.get("serial_number")
            or identity.get("serialNumber")
            or identity.get("serial_no")
            or identity.get("serialNo")
        )
        return str(numero_serie).strip() if numero_serie else None
    except Exception:
        return None

# --- FUNCIÓN PRINCIPAL ORQUESTADORA ---

async def consultar_una_impresora(ip: str, timeout: int = 8, marca: str = "Samsung") -> dict:
    """Consulta una impresora Samsung SyncThru coordinando sus subtareas.""" 
    if marca != "Samsung":
        resultado = await obtener_estado_toner(ip, timeout=timeout)
        resultado["marca"] = marca
        return resultado

    if aiohttp is None or SyncThru is None:
        raise RuntimeError("Faltan dependencias. Instala pysyncthru y aiohttp.") 
    
    resultado = { 
        "ip": ip, 
        "marca": marca,
        "mac": None,
        "modelo": None, 
        "numero_serie": None,
        "online": False, 
        "estado": None, 
        "toner": {}, 
        "paginas": {}, 
        "error": None, 
    } 
    
    try: 
        timeout_cfg = aiohttp.ClientTimeout(total=timeout) 
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session: 
            printer = SyncThru(ip, session) 
            await printer.update() 
            
            resultado["online"] = bool(printer.is_online()) 
            resultado["estado"] = printer.device_status() 
            
            if resultado["online"]: 
                resultado["modelo"] = printer.model() 
                
                # Ejecución de las tareas delegadas
                resultado["toner"] = _extraer_toner(printer) 
                resultado["paginas"] = _extraer_paginas(printer) 
                resultado["mac"] = _extraer_mac(printer)
                resultado["numero_serie"] = _extraer_numero_serie(printer)
                
            else: 
                resultado["error"] = "La impresora respondió pero figura como offline." 
                
    except asyncio.TimeoutError: 
        resultado["error"] = f"Timeout ({timeout}s) sin respuesta." 
    except aiohttp.ClientError as e: 
        resultado["error"] = f"Error de conexión: {e}" 
    except Exception as e: 
        resultado["error"] = f"No es una impresora Samsung SyncThru válida o falló el parseo: {e}" 
        
    return resultado 


async def consultar_todas( 
    ips: list[str | dict],
    timeout: int, 
    progreso_callback: Optional[Callable[[int, int, str, Optional[dict]], None]] = None 
) -> list[dict]: 
    resultados = [] 
    for i, item in enumerate(ips, start=1):
        ip = item["ip"] if isinstance(item, dict) else item
        marca = item.get("marca", "Samsung") if isinstance(item, dict) else "Samsung"
        if progreso_callback: 
            progreso_callback(i, len(ips), ip, None) 
        data = await consultar_una_impresora(ip, timeout=timeout, marca=marca)
        resultados.append(data) 
        if progreso_callback: 
            progreso_callback(i, len(ips), ip, data) 
    return list(resultados) 

async def consultar_hp_xml(ip: str, timeout: int = 5) -> dict | None: 
    # Nota: Esta función original solo devuelve los tóners de HP. 
    # Para mantener consistencia con el resto del script, ahora devuelve un diccionario con ambas estructuras.
    url = f"http://{ip}/DevMgmt/ConsumableConfigDyn.xml" 
    timeout_cfg = aiohttp.ClientTimeout(total=timeout) 
    try:
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session: 
            async with session.get(url) as r: 
                if r.status != 200: 
                    return None 
                xml = await r.text() 
                
        ns = { 
            "dd": "http://hp.com", 
            "ccdyn": "http://hp.com", 
        } 
        root = ET.fromstring(xml) 
        
        toners = {} 
        for consumible in root.findall("ccdyn:ConsumableInfo", ns): 
            tipo = consumible.findtext("dd:ConsumableTypeEnum", namespaces=ns) 
            if tipo != "toner": 
                continue 
            nombre = consumible.findtext("dd:ConsumableFamilyName", namespaces=ns) 
            porcentaje = consumible.findtext("dd:ConsumablePercentageLevelRemaining", namespaces=ns) 
            codigo = consumible.findtext("dd:ProductNumber", namespaces=ns) 
            
            if porcentaje is not None:
                toners[f"{nombre} ({codigo})"] = float(porcentaje) 
        
        # Intentar buscar contador de páginas en HP (si la URL del XML de consumibles tuviera datos de uso)
        # Nota: HP suele guardar los contadores en '/DevMgmt/ProductUsageDyn.xml'.
        return {"toner": toners, "paginas": {}}
    except Exception:
        return None





