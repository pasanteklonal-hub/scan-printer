"""Interfaz gráfica del escáner de impresoras Samsung SyncThru.

Este módulo contiene la ventana Tkinter, el manejo de la lista de IPs,
el inicio del escaneo y la exportación de resultados a JSON.
"""
import os
import asyncio
import json
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from scanner_db import load_printers, save_ips, save_scan_results, load_last_results, sanitize_for_json
from scanner_service import IP_REGEX, consultar_todas, limpiar_ip


class ScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Escáner de impresoras")
        self.geometry("720x560")
        self.resizable(True, True)

        self.ips: list[dict] = []
        self.resultados_finales: list[dict] = []

        self._construir_ui()
        self._cargar_estado_guardado()

    def _construir_ui(self):
        adv = tk.Label(
            self,
            text=(
                "Seleccione la marca de cada impresora. Samsung usa SyncThru; HP y otras "
                "marcas se consultan mediante SNMP."
            ),
            wraplength=700,
            justify="left",
            fg="#8a5a00",
            bg="#fff3cd",
            padx=10,
            pady=8,
        )
        adv.pack(fill="x", padx=10, pady=(10, 5))

        frame_ip = ttk.Frame(self)
        frame_ip.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_ip, text="IP de la impresora:").pack(side="left")
        self.entry_ip = ttk.Entry(frame_ip)
        self.entry_ip.pack(side="left", fill="x", expand=True, padx=8)
        self.entry_ip.bind("<Return>", lambda e: self.agregar_ip())

        ttk.Label(frame_ip, text="Marca:").pack(side="left")
        self.combo_marca = ttk.Combobox(
            frame_ip, values=("Samsung", "HP"), state="readonly", width=10
        )
        self.combo_marca.set("Samsung")
        self.combo_marca.pack(side="left", padx=(4, 8))

        ttk.Button(frame_ip, text="Agregar IP", command=self.agregar_ip).pack(side="left", padx=2)
        ttk.Button(frame_ip, text="Quitar seleccionada", command=self.quitar_ip).pack(side="left", padx=2)
        ttk.Button(frame_ip, text="Limpiar listado", command=self.limpiar_listado).pack(side="left", padx=2)

        frame_lista = ttk.Frame(self)
        frame_lista.pack(fill="both", expand=False, padx=10, pady=5)

        ttk.Label(frame_lista, text="Listado de IPs a escanear:").pack(anchor="w")
        self.listbox = tk.Listbox(frame_lista, height=8, selectmode="extended")
        self.listbox.pack(fill="x", expand=True)

        frame_acciones = ttk.Frame(self)
        frame_acciones.pack(fill="x", padx=10, pady=8)

        self.btn_escanear = ttk.Button(
            frame_acciones, text="▶ Escanear listado y generar JSON", command=self.iniciar_escaneo
        )
        self.btn_escanear.pack(side="left")

        ttk.Label(frame_acciones, text="Timeout (s):").pack(side="left", padx=(20, 4))
        self.spin_timeout = ttk.Spinbox(frame_acciones, from_=2, to=60, width=5)
        self.spin_timeout.set(8)
        self.spin_timeout.pack(side="left")

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 5))

        ttk.Label(self, text="Registro:").pack(anchor="w", padx=10)
        self.log = tk.Text(self, height=14, state="disabled", bg="#111", fg="#0f0")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _cargar_estado_guardado(self):
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

        resultados = asyncio.run(consultar_todas(self.ips, timeout, progreso_callback))
        self.resultados_finales = resultados
        save_scan_results(resultados)
        self.after(0, lambda: self._finalizar_escaneo(resultados))

    def _finalizar_escaneo(self, resultados: list[dict]):
        self.btn_escanear.config(state="normal")

        ok = sum(1 for r in resultados if r["error"] is None)
        self.log_msg(f"\nEscaneo terminado: {ok}/{len(resultados)} impresoras respondieron correctamente.")

        # 1. Definir y asegurar que exista la carpeta "estado-impresoras"
        carpeta_destino = "estado-impresoras"
        if not os.path.exists(carpeta_destino):
            os.makedirs(carpeta_destino)

        default_name = f"impresoras_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        ruta = filedialog.asksaveasfilename(
            title="Guardar JSON final",
            initialdir=carpeta_destino,
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
        messagebox.showinfo("Listo", f"JSON generado:\n{ruta}\n\n{ok}/{len(resultados)} impresoras OK.")


def main():
    app = ScannerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
