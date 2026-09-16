"""Interfaz gráfica del escáner de impresoras Samsung SyncThru.

Este módulo contiene la ventana Tkinter, el manejo de la lista de IPs,
el inicio del escaneo y la exportación de resultados a JSON.
"""
import asyncio
import json
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from scanner_db import (
    load_last_results,
    load_printer_commands,
    load_printers,
    save_ips,
    save_printer_commands,
    save_scan_results,
    sanitize_for_json,
)
from app_paths import APP_DIR
import actualizar_listado
from scanner_service import IP_REGEX, consultar_todas, limpiar_ip
from prediccion_toners import (
    cargar_historial,
    cargar_areas,
    cargar_snapshot,
    construir_reporte,
    construir_reporte_proxima_compra,
    consumo_historico_por_impresora,
    consumo_historico_por_modelo,
    encontrar_snapshot,
)


class ScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Panel de impresoras")
        self.geometry("1180x760")
        self.minsize(1040, 680)
        self.resizable(True, True)
        self._configurar_estilo()
        self._ventana_maximizada = False
        self.bind("<F11>", lambda _event: self.alternar_maximizar())

        self.ips: list[dict] = []
        self.resultados_finales: list[dict] = []
        self.comandos: list[dict] = []

        self._construir_ui()
        self._cargar_estado_guardado()

    def _configurar_estilo(self):
        estilo = ttk.Style(self)
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure("TFrame", background="#f4f7fb")
        estilo.configure("TLabel", background="#f4f7fb", foreground="#243447", font=("DejaVu Sans", 10))
        estilo.configure("TNotebook", background="#e8eef5", borderwidth=0)
        estilo.configure("TNotebook.Tab", padding=(18, 9), font=("DejaVu Sans", 10, "bold"))
        estilo.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff"), ("!selected", "#dce6f0")],
            foreground=[("selected", "#1769aa"), ("!selected", "#526579")],
        )
        estilo.configure(
            "Accent.TButton",
            background="#1769aa",
            foreground="#ffffff",
            padding=(14, 8),
            font=("DejaVu Sans", 10, "bold"),
        )
        estilo.map("Accent.TButton", background=[("active", "#0d4f83")])
        estilo.configure("TButton", padding=(10, 6), font=("DejaVu Sans", 9))
        estilo.configure("Treeview", rowheight=28, font=("DejaVu Sans", 9), background="#ffffff")
        estilo.configure("Treeview.Heading", font=("DejaVu Sans", 9, "bold"), background="#d8e5f0")
        estilo.map("Treeview", background=[("selected", "#b9d9f2")], foreground=[("selected", "#17324d")])

    def alternar_maximizar(self):
        """Maximiza o restaura la ventana usando el estado del gestor de ventanas."""
        try:
            estado_actual = self.state()
            if estado_actual == "zoomed" or self._ventana_maximizada:
                self.state("normal")
                self._ventana_maximizada = False
                self.btn_maximizar.configure(text="Maximizar")
            else:
                self.state("zoomed")
                self._ventana_maximizada = True
                self.btn_maximizar.configure(text="Restaurar")
        except tk.TclError:
            # Algunos gestores Linux exponen la maximización mediante -zoomed.
            self._ventana_maximizada = not self._ventana_maximizada
            self.attributes("-zoomed", self._ventana_maximizada)
            self.btn_maximizar.configure(
                text="Restaurar" if self._ventana_maximizada else "Maximizar"
            )

    def _construir_ui(self):
        barra_superior = ttk.Frame(self)
        barra_superior.pack(fill="x", padx=14, pady=(10, 0))
        ttk.Label(
            barra_superior,
            text="Panel de impresoras",
            font=("DejaVu Sans", 14, "bold"),
        ).pack(side="left")
        self.btn_maximizar = ttk.Button(
            barra_superior,
            text="Maximizar",
            command=self.alternar_maximizar,
        )
        self.btn_maximizar.pack(side="right")

        pestañas = ttk.Notebook(self)
        pestañas.pack(fill="both", expand=True, padx=14, pady=14)
        pestaña_escaneo = ttk.Frame(pestañas)
        pestaña_comandos = ttk.Frame(pestañas)
        pestaña_ver = ttk.Frame(pestañas)
        pestaña_toners = ttk.Frame(pestañas)
        pestañas.add(pestaña_escaneo, text="Escanear impresoras")
        pestañas.add(pestaña_comandos, text="Agregar comando")
        pestañas.add(pestaña_ver, text="Ver")
        pestañas.add(pestaña_toners, text="Toners")
        self._construir_pestaña_comandos(pestaña_comandos)
        self._construir_pestaña_ver(pestaña_ver)
        self._construir_pestaña_toners(pestaña_toners)

        contenido = ttk.Frame(pestaña_escaneo)
        contenido.pack(fill="both", expand=True)
        adv = tk.Label(
            contenido,
            text="Seleccione la marca. Las marcas configuradas con comando usan su salida JSON.",
            wraplength=1080, justify="left", fg="#36566f", bg="#e5f2fb", padx=12, pady=10,
        )
        adv.pack(fill="x", padx=4, pady=(2, 8))

        frame_ip = ttk.Frame(contenido)
        frame_ip.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_ip, text="IP de la impresora:").pack(side="left")
        self.entry_ip = ttk.Entry(frame_ip)
        self.entry_ip.pack(side="left", fill="x", expand=True, padx=8)
        self.entry_ip.bind("<Return>", lambda e: self.agregar_ip())

        ttk.Label(frame_ip, text="Marca:").pack(side="left")
        self.combo_marca = ttk.Combobox(
            frame_ip, values=("Samsung", "HP", "Ricoh", "Otra"), state="readonly", width=10
        )
        self.combo_marca.set("Samsung")
        self.combo_marca.pack(side="left", padx=(4, 8))

        ttk.Button(frame_ip, text="Agregar IP", command=self.agregar_ip, style="Accent.TButton").pack(side="left", padx=2)
        ttk.Button(frame_ip, text="Quitar seleccionada", command=self.quitar_ip).pack(side="left", padx=2)
        ttk.Button(frame_ip, text="Limpiar listado", command=self.limpiar_listado).pack(side="left", padx=2)

        frame_lista = ttk.Frame(contenido)
        frame_lista.pack(fill="both", expand=False, padx=10, pady=6)

        ttk.Label(frame_lista, text="Listado de IPs a escanear:").pack(anchor="w")
        self.listbox = tk.Listbox(
            frame_lista, height=7, selectmode="extended", font=("DejaVu Sans", 10),
            bg="#ffffff", fg="#243447", selectbackground="#b9d9f2", relief="flat",
        )
        self.listbox.pack(fill="x", expand=True)

        frame_acciones = ttk.Frame(contenido)
        frame_acciones.pack(fill="x", padx=10, pady=8)

        self.btn_escanear = ttk.Button(
            frame_acciones, text="Escanear listado y generar JSON", command=self.iniciar_escaneo,
            style="Accent.TButton",
        )
        self.btn_escanear.pack(side="left")

        ttk.Label(frame_acciones, text="Timeout (s):").pack(side="left", padx=(20, 4))
        self.spin_timeout = ttk.Spinbox(frame_acciones, from_=2, to=60, width=5)
        self.spin_timeout.set(8)
        self.spin_timeout.pack(side="left")

        self.progress = ttk.Progressbar(contenido, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 5))

        ttk.Label(contenido, text="Registro:").pack(anchor="w", padx=10)
        self.log = tk.Text(
            contenido, height=10, state="disabled", bg="#17212b", fg="#d5f5e3",
            insertbackground="#ffffff", relief="flat", padx=10, pady=8,
            font=("DejaVu Sans Mono", 9),
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _construir_pestaña_comandos(self, parent):
        ttk.Label(parent, text="Marca nueva:").grid(row=0, column=0, sticky="w", padx=10, pady=(15, 4))
        self.entry_comando_marca = ttk.Entry(parent, width=40)
        self.entry_comando_marca.grid(row=0, column=1, sticky="ew", padx=10, pady=(15, 4))
        ttk.Label(parent, text="Script/comando:").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self.entry_comando = ttk.Entry(parent)
        self.entry_comando.grid(row=1, column=1, sticky="ew", padx=10, pady=4)
        ttk.Label(parent, text="Salida esperada:").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self.entry_salida = ttk.Entry(parent)
        self.entry_salida.insert(0, "modelo:string,numero_serie:string,mac:string,toner:object,paginas:object,estado:string")
        self.entry_salida.grid(row=2, column=1, sticky="ew", padx=10, pady=4)
        ttk.Label(
            parent,
            text="Use {ip} y declare campo:tipo separados por coma, en el orden esperado. Tipos: string, integer, number, boolean, object, array, null. Ejemplo JSON: {\"modelo\":\"X\",\"toner\":{\"Black\":80}}",
            wraplength=600,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        ttk.Button(parent, text="Guardar comando", command=self.guardar_comando, style="Accent.TButton").grid(
            row=4, column=1, sticky="e", padx=10, pady=8
        )
        self.lista_comandos = tk.Listbox(parent, height=12)
        self.lista_comandos.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=10, pady=8)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(5, weight=1)

    def _construir_pestaña_ver(self, parent):
        parent.columnconfigure(0, weight=1)
        ttk.Label(
            parent,
            text="Abrir archivos generados por la actualización del listado.",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(20, 12))
        ttk.Button(
            parent,
            text="Abrir PDF generado",
            command=self.abrir_pdf_generado,
        ).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        ttk.Button(
            parent,
            text="Abrir planilla de impresoras",
            command=self.abrir_planilla_generada,
        ).grid(row=2, column=0, sticky="w", padx=10, pady=5)

    def _construir_pestaña_toners(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)
        ttk.Label(
            parent,
            text="Gestión de consumibles y planificación de compras",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(20, 12))
        ttk.Button(
            parent,
            text="Próxima compra",
            command=self.proxima_compra,
            style="Accent.TButton",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.label_proxima_compra = ttk.Label(parent, text="Todavía no se generó el reporte.")
        self.label_proxima_compra.grid(row=1, column=0, sticky="e", padx=10, pady=5)

        marco_tabla = ttk.Frame(parent)
        marco_tabla.grid(row=2, column=0, sticky="nsew", padx=10, pady=(12, 10))
        marco_tabla.rowconfigure(0, weight=1)
        marco_tabla.columnconfigure(0, weight=1)
        columnas = ("Área", "Marca", "Modelo", "Agotamiento estimado", "Horas restantes (Días)")
        self.tabla_proxima_compra = ttk.Treeview(
            marco_tabla, columns=columnas, show="headings", height=13
        )
        for columna in columnas:
            self.tabla_proxima_compra.heading(columna, text=columna)
            self.tabla_proxima_compra.column(columna, width=150, anchor="w")
        self.tabla_proxima_compra.column("Marca", width=90)
        self.tabla_proxima_compra.column("Área", width=130)
        self.tabla_proxima_compra.column("Agotamiento estimado", width=180)
        self.tabla_proxima_compra.column("Horas restantes (Días)", width=170, anchor="e")
        self.tabla_proxima_compra.grid(row=0, column=0, sticky="nsew")
        scrollbar_vertical = ttk.Scrollbar(
            marco_tabla, orient="vertical", command=self.tabla_proxima_compra.yview
        )
        scrollbar_vertical.grid(row=0, column=1, sticky="ns")
        self.tabla_proxima_compra.configure(
            yscrollcommand=scrollbar_vertical.set,
        )

    @staticmethod
    def _abrir_archivo_generado(nombre: str, descripcion: str):
        ruta = Path(__file__).with_name(nombre)
        if not ruta.exists():
            messagebox.showwarning(
                "Archivo no encontrado",
                f"Todavía no existe el archivo {descripcion}:\n{ruta}",
            )
            return
        try:
            webbrowser.open(ruta.resolve().as_uri())
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el archivo", str(exc))

    def abrir_pdf_generado(self):
        self._abrir_archivo_generado("impresoras_al_dia.pdf", "PDF generado")

    def abrir_planilla_generada(self):
        self._abrir_archivo_generado("impresoras_al_dia.xlsx", "la planilla de impresoras")

    def proxima_compra(self):
        try:
            ruta_snapshot = encontrar_snapshot()
            registros, fecha_snapshot = cargar_snapshot(ruta_snapshot)
            consumos = consumo_historico_por_impresora(cargar_historial())
            consumos_modelo = consumo_historico_por_modelo(cargar_historial())
            reporte = construir_reporte_proxima_compra(
                construir_reporte(
                    registros,
                    fecha_snapshot,
                    consumos_hora=consumos,
                    consumos_modelo=consumos_modelo,
                    areas=cargar_areas(),
                )
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("No se pudo calcular la próxima compra", str(exc))
            return

        for fila in self.tabla_proxima_compra.get_children():
            self.tabla_proxima_compra.delete(fila)
        for _, fila in reporte.iterrows():
            self.tabla_proxima_compra.insert(
                "", "end", values=tuple(fila[columna] for columna in reporte.columns)
            )
        self.label_proxima_compra.config(
            text=f"{len(reporte)} impresoras | snapshot: {ruta_snapshot.name}"
        )
        if reporte.empty:
            messagebox.showinfo(
                "Próxima compra",
                "No hay impresoras con datos de tóner disponibles para calcular.",
            )

    def guardar_comando(self):
        marca = self.entry_comando_marca.get().strip()
        comando = self.entry_comando.get().strip()
        salida = []
        salida_invalida = False
        for elemento in self.entry_salida.get().split(","):
            elemento = elemento.strip()
            if not elemento:
                continue
            partes = [parte.strip() for parte in elemento.split(":", 1)]
            if len(partes) != 2 or not partes[0] or not partes[1]:
                salida_invalida = True
                break
            salida.append({"clave": partes[0], "tipo": partes[1].lower()})
        tipos_validos = {"string", "integer", "number", "boolean", "object", "array", "null"}
        if salida_invalida or any(item["tipo"] not in tipos_validos for item in salida):
            messagebox.showwarning("Salida inválida", "Use el formato campo:tipo y tipos JSON válidos.")
            return
        if not marca or not comando or not salida or "{ip}" not in comando:
            messagebox.showwarning("Datos incompletos", "Indique marca, comando con {ip} y al menos una salida campo:tipo.")
            return
        self.comandos = [item for item in self.comandos if item.get("marca") != marca]
        self.comandos.append({"marca": marca, "comando": comando, "salida": salida})
        save_printer_commands(self.comandos)
        self._actualizar_marcas()
        self._mostrar_comandos()
        self.entry_comando_marca.delete(0, "end")
        messagebox.showinfo("Comando guardado", f"La marca '{marca}' ya está disponible en el listado.")

    def _actualizar_marcas(self):
        marcas = ["Samsung", "HP", "Ricoh", "Otra"] + [item["marca"] for item in self.comandos]
        self.combo_marca["values"] = tuple(dict.fromkeys(marcas))

    def _mostrar_comandos(self):
        self.lista_comandos.delete(0, "end")
        for item in self.comandos:
            salida = ", ".join(
                f"{dato['clave']}:{dato['tipo']}" if isinstance(dato, dict) else dato
                for dato in item["salida"]
            )
            self.lista_comandos.insert("end", f"{item['marca']}: {item['comando']} -> {salida}")

    def _cargar_estado_guardado(self):
        self.comandos = load_printer_commands()
        self._actualizar_marcas()
        self._mostrar_comandos()
        impresoras_guardadas = load_printers()
        if impresoras_guardadas:
            self.ips = impresoras_guardadas
            for impresora in impresoras_guardadas:
                self.listbox.insert("end", self._texto_impresora(impresora))

        resultados_guardados = load_last_results()
        if resultados_guardados:
            self.resultados_finales = resultados_guardados
            self.log_msg("Se cargaron resultados previos desde la base de datos.")

    @staticmethod
    def _texto_impresora(impresora: dict) -> str:
        return f"{impresora['ip']}  [{impresora.get('marca', 'Samsung')}]"

    def log_msg(self, texto: str):
        self.log.config(state="normal")
        self.log.insert("end", texto + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def agregar_ip(self):
        ip_raw = self.entry_ip.get().strip()
        if not ip_raw:
            return
        ip = limpiar_ip(ip_raw)

        if not IP_REGEX.match(ip):
            messagebox.showwarning("IP inválida", f"'{ip_raw}' no parece una dirección IP válida.")
            return

        if any(item["ip"] == ip for item in self.ips):
            messagebox.showinfo("Ya existe", f"La IP {ip} ya está en el listado.")
            return

        impresora = {"ip": ip, "marca": self.combo_marca.get()}
        self.ips.append(impresora)
        self.listbox.insert("end", self._texto_impresora(impresora))
        self.entry_ip.delete(0, "end")
        save_ips(self.ips)

    def quitar_ip(self):
        seleccion = list(self.listbox.curselection())
        seleccion.reverse()
        for idx in seleccion:
            ip = self.ips[idx]["ip"]
            self.listbox.delete(idx)
            self.ips.pop(idx)
        save_ips(self.ips)

    def limpiar_listado(self):
        if not self.ips:
            return
        if messagebox.askyesno("Confirmar", "¿Vaciar todo el listado de IPs?"):
            self.ips.clear()
            self.listbox.delete(0, "end")
            save_ips(self.ips)

    def iniciar_escaneo(self):
        if not self.ips:
            messagebox.showwarning("Listado vacío", "Agregá al menos una IP antes de escanear.")
            return

        self.btn_escanear.config(state="disabled")
        self.progress.config(maximum=len(self.ips), value=0)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

        timeout = int(self.spin_timeout.get())
        hilo = threading.Thread(target=self._correr_escaneo_en_hilo, args=(timeout,), daemon=True)
        hilo.start()

    def _correr_escaneo_en_hilo(self, timeout: int):
        def progreso_callback(i, total, ip, data):
            def actualizar():
                self.progress.config(value=i)
                if data is None:
                    self.log_msg(f"[{i}/{total}] Consultando {ip} ...")
                else:
                    if data["error"]:
                        self.log_msg(f"[{i}/{total}] {ip} -> ERROR: {data['error']}")
                    else:
                        toner_txt = ", ".join(f"{c}={v}%" for c, v in data["toner"].items()) or "sin datos de tóner"
                        self.log_msg(f"[{i}/{total}] {ip} -> {data['modelo']} | {toner_txt}")

            self.after(0, actualizar)

        comandos = {item["marca"]: item for item in self.comandos}
        resultados = asyncio.run(consultar_todas(self.ips, timeout, progreso_callback, comandos))
        self.resultados_finales = resultados
        save_scan_results(resultados)
        self.after(0, lambda: self._finalizar_escaneo(resultados))

    def _finalizar_escaneo(self, resultados: list[dict]):
        self.btn_escanear.config(state="normal")

        ok = sum(1 for r in resultados if r["error"] is None)
        self.log_msg(f"\nEscaneo terminado: {ok}/{len(resultados)} impresoras respondieron correctamente.")

        # Guardar siempre junto a la aplicación, independientemente del directorio de ejecución.
        carpeta_destino = APP_DIR / "estado-impresoras"
        carpeta_destino.mkdir(parents=True, exist_ok=True)

        default_name = f"impresoras_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        ruta = filedialog.asksaveasfilename(
            title="Guardar JSON final",
            initialdir=str(carpeta_destino),
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("Archivo JSON", "*.json")],
        )
        if not ruta:
            self.log_msg("Guardado cancelado por el usuario. Los datos siguen en memoria y en SQLite.")
            return

        payload = sanitize_for_json(resultados)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        self.log_msg(f"JSON guardado en: {ruta}")
        threading.Thread(
            target=self._actualizar_listado_automaticamente,
            daemon=True,
        ).start()
        messagebox.showinfo("Listo", f"JSON generado:\n{ruta}\n\n{ok}/{len(resultados)} impresoras OK.")

    def _actualizar_listado_automaticamente(self):
        if getattr(sys, "frozen", False):
            try:
                actualizar_listado.actualizar()
                mensaje = "Listado actualizado automáticamente."
            except Exception as exc:
                mensaje = f"Error al actualizar el listado automáticamente: {exc}"
            self.after(0, lambda: self.log_msg(mensaje))
            return

        ruta_script = APP_DIR / "actualizar_listado.py"
        try:
            proceso = subprocess.run(
                [sys.executable, str(ruta_script)],
                cwd=str(ruta_script.parent),
                capture_output=True,
                text=True,
                check=False,
            )
            salida = (proceso.stdout or proceso.stderr).strip()
            mensaje = "Listado actualizado automáticamente."
            if proceso.returncode != 0:
                mensaje = f"No se pudo actualizar el listado (código {proceso.returncode})."
            if salida:
                mensaje += f" {salida.splitlines()[-1]}"
        except Exception as exc:
            mensaje = f"Error al actualizar el listado automáticamente: {exc}"
        self.after(0, lambda: self.log_msg(mensaje))


def main():
    app = ScannerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
