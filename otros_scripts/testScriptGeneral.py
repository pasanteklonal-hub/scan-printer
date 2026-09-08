import asyncio
from pysnmp.hlapi.v3arch.asyncio import (
    get_cmd, next_cmd, SnmpEngine, CommunityData,
    UdpTransportTarget, ContextData, ObjectType, ObjectIdentity
)

# OIDs Estándar internacionales (RFC 3805 - Printer MIB)
OID_MODELO = "1.3.6.1.2.1.25.3.2.1.3.1"   
OID_NOMBRES = "1.3.6.1.2.1.43.11.1.1.6.1"  
OID_MAX_CAP = "1.3.6.1.2.1.43.11.1.1.8.1"  
OID_ACTUAL = "1.3.6.1.2.1.43.11.1.1.9.1"   


async def consultar_snmp_get(snmp_engine, target, oid, comunidad="public"):
    """Realiza un SNMP GET y extrae el valor usando la indexación de ObjectType."""
    try:
        res = await get_cmd(
            snmp_engine, CommunityData(comunidad, mpModel=0), target, ContextData(),
            ObjectType(ObjectIdentity(oid))
        )
        errorIndication, errorStatus, errorIndex, varBinds = res
        
        if errorIndication or errorStatus or not varBinds:
            return "Desconocido"
        
        # El primer elemento de varBinds es un ObjectType.
        # [1] nos da el valor parseado por PySNMP directamente.
        return str(varBinds[0][1])
    except Exception:
        return "Desconocido"


async def consultar_snmp_walk(snmp_engine, target, oid_base, comunidad="public"):
    """Realiza un SNMP WALK extrayendo los valores de la secuencia ObjectType."""
    valores = []
    try:
        res = await next_cmd(
            snmp_engine, CommunityData(comunidad, mpModel=0), target, ContextData(),
            ObjectType(ObjectIdentity(oid_base)), lexicographicMode=False
        )
        errorIndication, errorStatus, errorIndex, varBinds = res
        
        if not errorIndication and not errorStatus and varBinds:
            # En la versión 6, varBinds contiene directamente los objetos de la respuesta
            for item in varBinds:
                # item[1] es el valor de la variable SNMP (nombre, capacidad, etc.)
                valores.append(str(item[1]))
    except Exception as e:
        print(f"⚠️ Error en Walk: {e}")
    return valores


async def obtener_estado_toner(ip, comunidad="public"):
    print(f"\nConectando a {ip}...")
    snmp_engine = SnmpEngine()
    
    try:
        target = await UdpTransportTarget.create((ip, 161), timeout=3.0, retries=1)
    except Exception as e:
        print(f"❌ Error de red: {e}")
        snmp_engine.close_dispatcher()
        return
    
    # 1. Traer información del hardware
    modelo = await consultar_snmp_get(snmp_engine, target, OID_MODELO, comunidad)
    
    # 2. Traer listas de datos de los tóners
    nombres = await consultar_snmp_walk(snmp_engine, target, OID_NOMBRES, comunidad)
    capacidades_max = await consultar_snmp_walk(snmp_engine, target, OID_MAX_CAP, comunidad)
    niveles_actuales = await consultar_snmp_walk(snmp_engine, target, OID_ACTUAL, comunidad)
    
    if not nombres:
        print(f"❌ No se detectaron consumibles válidos en la IP {ip}.")
        snmp_engine.close_dispatcher()
        return

    print(f"🖨️  Dispositivo: {modelo}")
    print("-" * 60)
    
    # 3. Procesar y calcular porcentajes basándonos en tipos primitivos
    for i in range(len(nombres)):
        nombre_toner = nombres[i]
        try:
            max_cap = int(capacidades_max[i])
            actual = int(niveles_actuales[i])
        except (IndexError, ValueError, TypeError):
            continue

        if actual == -3:
            print(f"  🔹 {nombre_toner}: Estado OK (No mide porcentaje)")
        elif actual == -2:
            print(f"  🔹 {nombre_toner}: Nivel Bajo / Casi Agotado ⚠️")
        elif actual < 0 or max_cap <= 0:
            print(f"  🔹 {nombre_toner}: Error / Nivel no legible")
        else:
            porcentaje = (actual / max_cap) * 100
            print(f"  🔹 {nombre_toner}: {porcentaje:.1f}% restante ({actual}/{max_cap})")
            
    snmp_engine.close_dispatcher()


async def main():
    # IP validada que responde en tu red local
    IP_PRUEBA = "192.168.200.166"
    await obtener_estado_toner(IP_PRUEBA, comunidad="public")


if __name__ == "__main__":
    asyncio.run(main())
