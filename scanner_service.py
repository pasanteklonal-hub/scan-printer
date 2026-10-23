"""Lógica de negocio para consultar impresoras Samsung SyncThru. 
Aquí se encuentra la validación de IPs, la limpieza de datos y la consulta asíncrona de cada impresora 
para obtener modelo, estado, porcentaje de tóner y contadores de páginas. """ 

import asyncio 
import json
import re 
import subprocess
from typing import Callable, Optional 
import xml.etree.ElementTree as ET 

from scriptGeneral import (
    consultar_mac_snmp,
    consultar_numero_serie_snmp,
    obtener_estado_ricoh,
    obtener_estado_toner,
)

STANDARD_RESULT = {
    "mac": None,
    "modelo": None,
    "numero_serie": None,
    "online": True,
    "estado": "OK",
    "toner": {},
    "paginas": {},
    "error": None,
}
COMMAND_OUTPUT_SCHEMA = (
    {"clave": "toner", "tipo": "object"},
    {"clave": "paginas", "tipo": "object"},
)

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
        if isinstance(datos_raw, dict):
            identity = datos_raw.get("identity", {})
            numero_serie = (
                identity.get("serial_number")
                or identity.get("serialNumber")
                or identity.get("serial_no")
                or identity.get("serialNo")
            )
            if numero_serie:
                return str(numero_serie).strip()

        html = datos_raw if isinstance(datos_raw, str) else str(datos_raw)
        match = re.search(
            r'<div[^>]*class=["\'][^"\']*x-column[^"\']*["\'][^>]*>\s*([^<]+?)\s*</div>',
            html,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match else None
    except Exception:
        return None

# --- FUNCIÓN PRINCIPAL ORQUESTADORA ---

async def consultar_una_impresora(ip: str, timeout: int = 8, marca: str = "Samsung") -> dict:
    """Consulta una impresora según su marca o mediante OIDs SNMP estándar.""" 
    if marca == "Ricoh":
        return await obtener_estado_ricoh(ip, timeout=timeout)

    if marca in ("HP", "Otra"):
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
            resultado["mac"] = _extraer_mac(printer)
            if resultado["mac"] is None:
                resultado["mac"] = await consultar_mac_snmp(ip, timeout=timeout)
            
            if resultado["online"]: 
                resultado["modelo"] = printer.model() 
                
                # Ejecución de las tareas delegadas
                resultado["toner"] = _extraer_toner(printer) 
                resultado["paginas"] = _extraer_paginas(printer) 
                resultado["numero_serie"] = _extraer_numero_serie(printer)
                if resultado["numero_serie"] is None:
                    resultado["numero_serie"] = await consultar_numero_serie_snmp(
                        ip, timeout=timeout
                    )
                
            else: 
                resultado["error"] = "La impresora respondió pero figura como offline." 
                
    except asyncio.TimeoutError: 
        resultado["error"] = f"Timeout ({timeout}s) sin respuesta." 
    except aiohttp.ClientError as e: 
        resultado["error"] = f"Error de conexión: {e}" 
    except Exception as e: 
        resultado["error"] = f"No es una impresora Samsung SyncThru válida o falló el parseo: {e}" 
        
    return resultado 


async def consultar_con_comando(
    ip: str,
    marca: str,
    comando: str,
    salida: list[dict | str],
    timeout: int = 8,
    modelo: Optional[str] = None,
) -> dict:
    """Ejecuta un comando configurado por el usuario y normaliza su JSON."""
    comando_ip = comando.replace("{ip}", ip)
    try:
        proceso = await asyncio.to_thread(
            subprocess.run,
            comando_ip,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proceso.returncode != 0:
            raise RuntimeError((proceso.stderr or "comando terminó con error").strip())
        datos = json.loads(proceso.stdout)
        if not isinstance(datos, dict):
            raise ValueError("la salida debe ser un objeto JSON")
        faltantes = []
        tipos_invalidos = []
        for esperado in salida:
            if isinstance(esperado, str):
                clave, tipo = esperado, None
            else:
                clave, tipo = esperado.get("clave"), esperado.get("tipo")
            if clave not in datos:
                faltantes.append(clave)
                continue
            if tipo and not _tipo_json_coincide(datos[clave], tipo):
                tipos_invalidos.append(f"{clave} (esperado {tipo})")
        if faltantes:
            raise ValueError(f"faltan claves declaradas: {', '.join(faltantes)}")
        if tipos_invalidos:
            raise ValueError(f"tipos inválidos: {', '.join(tipos_invalidos)}")
        return {
            **STANDARD_RESULT,
            **datos,
            "ip": ip,
            "marca": marca,
            "modelo": modelo or datos.get("modelo"),
            "error": None,
        }
    except subprocess.TimeoutExpired:
        error = f"Timeout ({timeout}s) sin respuesta."
    except (json.JSONDecodeError, ValueError) as exc:
        error = f"Salida inválida del comando: {exc}"
    except Exception as exc:
        error = f"Error ejecutando comando: {exc}"
    return {"ip": ip, "marca": marca, **STANDARD_RESULT, "online": False, "error": error}


def _tipo_json_coincide(valor: object, tipo: str) -> bool:
    """Comprueba un tipo declarado para un campo de salida JSON."""
    tipos = {
        "string": lambda dato: isinstance(dato, str),
        "integer": lambda dato: isinstance(dato, int) and not isinstance(dato, bool),
        "number": lambda dato: isinstance(dato, (int, float)) and not isinstance(dato, bool),
        "boolean": lambda dato: isinstance(dato, bool),
        "object": lambda dato: isinstance(dato, dict),
        "array": lambda dato: isinstance(dato, list),
        "null": lambda dato: dato is None,
    }
    return tipo in tipos and tipos[tipo](valor)


async def consultar_todas( 
    ips: list[str | dict],
    timeout: int, 
    progreso_callback: Optional[Callable[[int, int, str, Optional[dict]], None]] = None,
    comandos: Optional[dict[str, dict]] = None,
) -> list[dict]: 
    resultados = [] 
    for i, item in enumerate(ips, start=1):
        ip = item["ip"] if isinstance(item, dict) else item
        marca = item.get("marca", "Samsung") if isinstance(item, dict) else "Samsung"
        if progreso_callback: 
            progreso_callback(i, len(ips), ip, None) 
        try:
            configuracion = (comandos or {}).get(marca)
            consulta = (
                consultar_con_comando(
                    ip,
                    marca,
                    configuracion["comando"],
                    configuracion["salida"],
                    timeout,
                    modelo=configuracion.get("modelo"),
                )
                if configuracion
                else consultar_una_impresora(ip, timeout=timeout, marca=marca)
            )
            data = await asyncio.wait_for(
                consulta,
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            data = {
                "ip": ip,
                "marca": marca,
                "online": False,
                "error": f"Timeout ({timeout}s) sin respuesta.",
            }
        except Exception as e:
            data = {
                "ip": ip,
                "marca": marca,
                "online": False,
                "error": f"Error durante la consulta: {e}",
            }
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




