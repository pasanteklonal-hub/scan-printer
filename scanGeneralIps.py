import asyncio
from pysnmp.hlapi.v3arch.asyncio import (
    get_cmd,
    next_cmd,
    SnmpEngine,
    CommunityData,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
)

# OIDs
OID_MODELO = "1.3.6.1.2.1.25.3.2.1.3.1"
OID_NOMBRES = "1.3.6.1.2.1.43.11.1.1.6.1"
OID_MAX_CAP = "1.3.6.1.2.1.43.11.1.1.8.1"
OID_ACTUAL = "1.3.6.1.2.1.43.11.1.1.9.1"
OID_CONTADOR_TOTAL = "1.3.6.1.2.1.43.10.2.1.4.1.1"  # prtMarkerLifeCount (paginas totales impresas)


async def consultar_snmp_get(snmp_engine, target, oid, comunidad="public"):
    try:
        errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
            snmp_engine,
            CommunityData(comunidad, mpModel=0),
            target,
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )

        if errorIndication or errorStatus or not varBinds:
            return None

        return str(varBinds[0][1])

    except Exception:
        return None


async def consultar_snmp_walk(snmp_engine, target, oid_base, comunidad="public"):
    valores = []

    try:
        errorIndication, errorStatus, errorIndex, varBinds = await next_cmd(
            snmp_engine,
            CommunityData(comunidad, mpModel=0),
            target,
            ContextData(),
            ObjectType(ObjectIdentity(oid_base)),
            lexicographicMode=False,
        )

        if errorIndication or errorStatus:
            return valores

        for item in varBinds:
            valores.append(str(item[1]))

    except Exception:
        pass

    return valores


async def obtener_estado_toner(ip, comunidad="public"):
    snmp_engine = SnmpEngine()

    try:
        target = await UdpTransportTarget.create(
            (ip, 161),
            timeout=3,
            retries=1,
        )
    except Exception:
        snmp_engine.close_dispatcher()
        return {"ip": ip, "error": "No responde"}

    try:
        # Ejecutar consultas en paralelo
        modelo_task = consultar_snmp_get(
            snmp_engine, target, OID_MODELO, comunidad
        )
        nombres_task = consultar_snmp_walk(
            snmp_engine, target, OID_NOMBRES, comunidad
        )
        max_task = consultar_snmp_walk(
            snmp_engine, target, OID_MAX_CAP, comunidad
        )
        actual_task = consultar_snmp_walk(
            snmp_engine, target, OID_ACTUAL, comunidad
        )
        contador_task = consultar_snmp_get(
            snmp_engine, target, OID_CONTADOR_TOTAL, comunidad
        )

        modelo, nombres, capacidades, actuales, contador_total = await asyncio.gather(
            modelo_task,
            nombres_task,
            max_task,
            actual_task,
            contador_task,
        )

        if not nombres:
            return {"ip": ip, "error": "Sin consumibles"}

        toners = []

        for nombre, max_cap, actual in zip(nombres, capacidades, actuales):

            try:
                max_cap = int(max_cap)
                actual = int(actual)
            except Exception:
                continue

            if actual == -3:
                estado = "OK (No mide porcentaje)"
            elif actual == -2:
                estado = "Nivel Bajo"
            elif actual < 0 or max_cap <= 0:
                estado = "Error"
            else:
                estado = f"{actual/max_cap*100:.1f}%"

            toners.append({
                "nombre": nombre,
                "estado": estado,
                "actual": actual,
                "max": max_cap,
            })

        # Normalizar el contador total (puede venir como None si no responde)
        try:
            contador_total = int(contador_total)
        except (TypeError, ValueError):
            contador_total = None

        return {
            "ip": ip,
            "modelo": modelo,
            "contador_total": contador_total,
            "toners": toners,
        }

    finally:
        snmp_engine.close_dispatcher()


async def main():

    ips = [
        "192.168.0.59",
        "192.168.0.30",
        "192.168.0.71"

    ]

    tareas = [obtener_estado_toner(ip) for ip in ips]

    resultados = await asyncio.gather(*tareas)

    for resultado in resultados:

        print("\n" + "=" * 70)
        print(f"IP: {resultado['ip']}")

        if "error" in resultado:
            print(resultado["error"])
            continue

        print(f"Modelo: {resultado['modelo']}")

        if resultado["contador_total"] is not None:
            print(f"Impresiones totales: {resultado['contador_total']}")
        else:
            print("Impresiones totales: No disponible")

        for toner in resultado["toners"]:
            print(
                f"  {toner['nombre']:<20} {toner['estado']}"
            )


if __name__ == "__main__":
    asyncio.run(main())