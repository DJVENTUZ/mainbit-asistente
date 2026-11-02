#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mainbit Asistente 3.0 — migración de datos + configuración + relación de equipos.

Un solo programa para las dos PCs. Funciona SIN internet y sin instalar nada en los clientes
(se compila una vez a .exe con compilar_exe.bat).
"""
import ctypes
import os
import queue
import socket
import sys
import shutil
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk, font as tkfont

import mb_motor as M
import mb_sistema as SYS
import mb_util as U
import mb_local as L0
import mb_servicio as SV
import mb_manual as MAN
import mb_arranque as ARR
import mb_secretos as SEC
import mb_widgets as W
from mb_relacion import COLUMNAS, Relacion, escribir_evidencia, faltantes, texto_informe_importacion
from mb_util import acortar, fmt_bytes, fmt_tiempo, ultimos5
from mb_widgets import (C, COL, S, Capsula, Card, FilaEstado, RoundedButton, ScrollFrame,
                        Segmentado, Switch)

IP_DIRECTA_NUEVA = "192.168.77.2"
IP_DIRECTA_VIEJA = "192.168.77.1"
VELOCIDAD_ESTIMADA = 60 * 1024 * 1024      # B/s para el cálculo de tiempo (disco viejo + Gigabit)


# ----------------------------------------------------------------------
# Credenciales (nunca van dentro del programa; solo en un .ini opcional)
# ----------------------------------------------------------------------
def ruta_credenciales():
    return os.path.join(U.dir_app(), "credenciales.ini")


def leer_credenciales():
    d = {}
    try:
        with open(ruta_credenciales(), "r", encoding="utf-8") as f:
            for ln in f:
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.rstrip("\r\n").split("=", 1)
                    d[k.strip()] = v
    except OSError:
        pass
    return d


def guardar_credenciales(d):
    try:
        with open(ruta_credenciales(), "w", encoding="utf-8") as f:
            f.write("# Mainbit Asistente: credenciales en TEXTO PLANO. Borra este archivo al terminar.\n")
            for k, v in d.items():
                f.write(f"{k}={v}\n")
        return True
    except OSError:
        return False


def borrar_credenciales():
    try:
        os.remove(ruta_credenciales())
        return True
    except OSError:
        return False


class App:
    # ==================================================================
    # Arranque
    # ==================================================================
    def __init__(self, root):
        self.root = root
        W.ESC = max(1.0, root.winfo_fpixels("1i") / 96.0)
        root.title(f"{U.APP_NAME} v{U.VERSION_COMPLETA}")
        root.configure(bg=C["bg"])
        fams = set(tkfont.families())
        self.F = next((f for f in ("SF Pro Display", "SF Pro Text", "Segoe UI Variable Display",
                                   "Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans")
                       if f in fams), "Helvetica")
        self.cfg = U.cargar_config()
        self.est_path = os.path.join(U.dir_datos(), "estado.json")
        self.estado = U.leer_json(self.est_path, {}) or {}
        self.relacion = Relacion(L0.raiz_datos())
        self.q = queue.Queue()
        self.job = None
        self.ocupado = False
        self.tareas = set()
        self.btns_accion = []
        self.info = {}
        self.adaptadores = []
        self.dc_ip = None
        self.res = {}                 # resultados de chequeos para la evidencia
        self.rutas_extra = []
        self.vars_unidad = {}         # letra -> BooleanVar
        self.ip_fija_activa = False
        self.fw_abierto = False
        self._vel, self._t_last, self._b_last = 0.0, time.monotonic(), 0
        self.idx_pendiente = None
        self.vigilando_ivanti = False
        self.destino = self.cfg["destino_backup"]
        if not SYS.IS_WIN and not os.path.isdir(os.path.dirname(self.destino) or "."):
            self.destino = os.path.join(os.path.expanduser("~"), "BACKUP_MIGRACION")
        cred = leer_credenciales()

        # variables de interfaz
        sv = tk.StringVar
        self.v_serie, self.v_monitor = sv(), sv()
        self.v_serie_v, self.v_monitor_v = sv(), sv()
        self.v_base = sv(value=self.cfg["base_hostname"])
        self.v_guion = tk.BooleanVar(value=self.cfg["guion"])
        self.v_user_dom = sv(value=cred.get("usuario", self.cfg["usuario_dominio"]))
        self.v_pass_dom = sv(value=cred.get("clave", ""))
        self.v_ssid = sv(value=cred.get("wifi_ssid", self.cfg["wifi_ssid"]))
        self.v_pass_wifi = sv(value=cred.get("wifi_clave", ""))
        self.v_recordar = tk.BooleanVar(value=bool(cred))
        self.v_ip = sv()
        self.v_users = tk.BooleanVar(value=True)
        self.v_cache = tk.BooleanVar(value=self.cfg["excluir_cache"])
        self.r_nombre, self.r_apellido = sv(), sv()
        self.r_mon_a, self.r_cpu_a, self.r_tipo = sv(), sv(), sv(value=self.cfg.get("tipo_defecto", "D2 Y P03"))
        for v in (self.v_serie, self.v_base):
            v.trace_add("write", lambda *a: self.actualizar_hostname())
        self.v_guion.trace_add("write", lambda *a: self._regenerar_base())

        self._ventana()
        self._construir()
        U_modo = self._modo_inicial()
        self.seg.seleccionar(U_modo)
        self.cambiar_modo(U_modo)
        self._titulo_oscuro()
        root.attributes("-alpha", 0.95)
        root.protocol("WM_DELETE_WINDOW", self.cerrar)
        root.after(120, self.tick)
        self.en_hilo("info", self._cargar_info)
        self._arranque_automatico()

    def _arranque_automatico(self):
        """Lo que la app hace sola al abrir (lo usan la interfaz clásica y la nueva)."""
        self.dir_instaladores()
        self._iniciar_vigilancia_usb()
        if os.path.isfile(os.path.join(U.dir_app(), "modo_vieja.flag")):
            try:
                self.seg.seleccionar(1)
            except Exception:
                pass
            self.cambiar_modo(1)
            threading.Thread(target=self._auto_buscar_receptor, daemon=True).start()
        elif self.estado.get("unido") and not self.estado.get("final_auto"):
            def _auto_final():
                time.sleep(10)
                self.estado["final_auto"] = True
                self.guardar_estado()
                self.log("Equipo ya en el dominio: revisando la lista final automáticamente…", "info")
                self.ui(self.ejecutar_todo)
            threading.Thread(target=_auto_final, daemon=True).start()

    def _ventana(self):
        r = self.root
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(S(1080), sw - S(30)), min(S(800), sh - S(80))
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        r.minsize(min(S(820), w), min(S(560), h))

    def _titulo_oscuro(self):
        if not SYS.IS_WIN:
            return
        try:
            self.root.update()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            v = ctypes.c_int(1)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4) == 0:
                    break
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ctypes.c_int(0)), 4)
        except Exception:
            pass

    def _modo_inicial(self):
        usuario = os.environ.get("USERNAME", os.environ.get("USER", "")).lower()
        if usuario == self.cfg["usuario_local_nueva"].lower():
            return 0
        return 0 if not self.estado.get("modo") else int(self.estado.get("modo", 0))

    # ==================================================================
    # Helpers de interfaz
    # ==================================================================
    def f(self, size=9, bold=False):
        return (self.F, size, "bold") if bold else (self.F, size)

    def L(self, parent, texto, size=9, bold=False, color=None, bg=None, **kw):
        return tk.Label(parent, text=texto, font=self.f(size, bold), bg=bg or C["card"],
                        fg=color or C["label"], anchor="w", justify="left", **kw)

    def secc(self, parent, texto):
        tk.Label(parent, text=texto.upper(), font=self.f(8), bg=C["bg"], fg=C["label2"],
                 anchor="w").pack(fill="x", padx=S(8), pady=(S(12), S(4)))

    def card(self, parent, radius=18):
        c = Card(parent, C["bg"], radius=radius, width=420)
        c.pack(fill="x")
        return c.inner

    def entrada(self, parent, var, show=None, **kw):
        return tk.Entry(parent, textvariable=var, font=self.f(10), bg=C["fill"], fg=C["label"],
                        insertbackground=C["label"], relief="flat", bd=0, show=show or "",
                        disabledbackground=C["fill"], disabledforeground=C["label2"],
                        highlightthickness=1, highlightbackground=C["fill"], highlightcolor=C["blue"], **kw)

    def boton(self, parent, texto, cmd, color=None, fg="#FFFFFF", h=36, w=100, accion=True, bold=True):
        b = RoundedButton(parent, texto, cmd, color or C["fill"], fg, C["card"],
                          self.f(9, bold), height=h, width=w)
        if accion:
            self.btns_accion.append(b)
        return b

    def campo(self, grid, fila, texto, var, show=None):
        self.L(grid, texto, 9, color=C["label2"]).grid(row=fila, column=0, sticky="w", pady=S(3), padx=(0, S(10)))
        e = self.entrada(grid, var, show=show)
        e.grid(row=fila, column=1, sticky="ew", pady=S(3), ipady=S(5))
        return e

    def ui(self, fn, *args):
        self.q.put((fn, args))

    def log(self, texto, nivel=None):
        self.ui(self._log, texto, nivel)

    def estado_txt(self, texto, nivel=None):
        self.ui(lambda: self.lbl_estado.config(text=texto, fg=COL.get(nivel, C["label2"])))

    def preguntar(self, titulo, texto):
        """Pregunta sí/no desde un hilo (bloquea ese hilo, no la interfaz)."""
        ev, res = threading.Event(), []

        def f():
            res.append(messagebox.askyesno(titulo, texto))
            ev.set()
        self.ui(f)
        ev.wait()
        return res[0]

    def en_hilo(self, clave, fn, *args):
        if clave in self.tareas:
            return
        self.tareas.add(clave)

        def run():
            try:
                fn(*args)
            except Exception as e:
                self.log(f"Error inesperado ({clave}): {e}", "err")
            finally:
                self.ui(self.tareas.discard, clave)
        threading.Thread(target=run, daemon=True).start()

    # ==================================================================
    # Construcción
    # ==================================================================
    def _construir(self):
        r = self.root
        cab = tk.Frame(r, bg=C["bg"])
        cab.pack(fill="x", padx=S(20), pady=(S(14), S(4)))
        tk.Label(cab, text="Mainbit Asistente", font=self.f(22, True), bg=C["bg"], fg=C["label"]).pack(side="left")
        tk.Label(cab, text="v" + U.VERSION_COMPLETA, font=self.f(8, True), bg=C["fill"], fg=C["blue"],
                 padx=S(8), pady=S(3)).pack(side="left", padx=S(10))
        b_aj = RoundedButton(cab, "Ajustes", self.abrir_ajustes, C["fill"], C["blue"], C["bg"],
                             self.f(9, True), height=34, width=76)
        b_aj.pack(side="right", padx=(S(8), 0))
        self.seg = Segmentado(cab, ["PC NUEVA", "PC VIEJA", "RELACIÓN"], self.cambiar_modo, C["bg"],
                              self.f(9, True), width=330, height=34)
        self.seg.pack(side="right")

        # banner "siguiente paso"
        cb = Card(r, C["bg"], radius=14, pad=12, width=900)
        cb.pack(fill="x", padx=S(20), pady=(S(6), S(2)))
        self.lbl_banner = tk.Label(cb.inner, text="", font=self.f(10, True), bg=C["card"], fg=C["blue"],
                                   anchor="w", justify="left", wraplength=S(980))
        self.lbl_banner.pack(fill="x")

        # cuerpo
        self.cuerpo = tk.Frame(r, bg=C["bg"])
        self.cuerpo.pack(fill="both", expand=True, padx=S(12), pady=(S(4), 0))
        self.cuerpo.columnconfigure(0, weight=1)
        self.cuerpo.rowconfigure(0, weight=1)
        self.fr_trabajo = tk.Frame(self.cuerpo, bg=C["bg"])
        self.fr_trabajo.grid(row=0, column=0, sticky="nsew")
        self.fr_trabajo.columnconfigure((0, 1), weight=1, uniform="col")
        self.fr_trabajo.rowconfigure(0, weight=1)
        self.sf_izq = ScrollFrame(self.fr_trabajo, C["bg"])
        self.sf_izq.grid(row=0, column=0, sticky="nsew", padx=(0, S(6)))
        self.sf_der = ScrollFrame(self.fr_trabajo, C["bg"])
        self.sf_der.grid(row=0, column=1, sticky="nsew", padx=(S(6), 0))

        self.pag_nueva = tk.Frame(self.sf_izq.inner, bg=C["bg"])
        self.pag_vieja = tk.Frame(self.sf_izq.inner, bg=C["bg"])
        self._pag_nueva()
        self._pag_vieja()
        self._actividad(self.sf_der.inner)
        self.pag_rel = tk.Frame(self.cuerpo, bg=C["bg"])
        self.pag_rel.grid(row=0, column=0, sticky="nsew")
        self.pag_rel.grid_remove()
        self._pag_relacion(self.pag_rel)

        pie = tk.Frame(r, bg=C["bg"])
        pie.pack(fill="x", padx=S(20), pady=(S(4), S(8)))
        tk.Label(pie, text="Desarrollado para Mainbit por Gabriel Hernández Arroyo", font=self.f(8),
                 bg=C["bg"], fg=C["label3"]).pack(side="left")
        esc = tk.Scale(pie, from_=60, to=100, orient="horizontal", showvalue=0, length=S(110),
                       command=lambda v: r.attributes("-alpha", int(float(v)) / 100),
                       bg=C["bg"], troughcolor=C["fill"], highlightthickness=0, bd=0, sliderrelief="flat",
                       activebackground=C["blue"], width=S(9))
        esc.set(95)
        esc.pack(side="right")
        tk.Label(pie, text="Transparencia", font=self.f(8), bg=C["bg"], fg=C["label2"]).pack(side="right", padx=S(6))

    # ------------------------------------------------------------ PC NUEVA
    def _pag_nueva(self):
        p = self.pag_nueva
        self.subseg = Segmentado(p, ["1 · Recibir", "2 · Dominio", "3 · Final"], self.cambiar_sub, C["bg"],
                                 self.f(9, True), width=400, height=34)
        self.subseg.pack(fill="x", pady=(S(4), 0))
        self.sub_frames = [tk.Frame(p, bg=C["bg"]) for _ in range(3)]
        self._nueva1(self.sub_frames[0])
        self._nueva2(self.sub_frames[1])
        self._nueva3(self.sub_frames[2])

    def _equipo_card(self, p, serie_var, mon_var, titulo):
        self.secc(p, titulo)
        a = self.card(p)
        g = tk.Frame(a, bg=C["card"])
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.campo(g, 0, "Serie CPU", serie_var)
        self.campo(g, 1, "Serie monitor", mon_var)
        lm = self.L(g, "", 9, color=C["label2"], wraplength=S(260))
        self.L(g, "Equipo", 9, color=C["label2"]).grid(row=2, column=0, sticky="w", pady=S(3))
        lm.grid(row=2, column=1, sticky="w", pady=S(3))
        la = self.L(g, "", 9)
        self.L(g, "Administrador", 9, color=C["label2"]).grid(row=3, column=0, sticky="w", pady=S(3))
        la.grid(row=3, column=1, sticky="w", pady=S(3))
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x", pady=(S(6), 0))
        self.boton(f2, "Releer equipo", lambda: self.en_hilo("info", self._cargar_info), h=30, w=110,
                   accion=False, bold=False).pack(side="left")
        self.boton(f2, "Diagnóstico de monitores", lambda: self.en_hilo("diagmon", self.diagnostico_monitores), h=30,
                   w=170, accion=False, bold=False).pack(side="left", padx=(8, 0))
        return lm, la

    def _nueva1(self, p):
        self.lm_n, self.la_n = self._equipo_card(p, self.v_serie, self.v_monitor, "Este equipo (PC nueva)")
        self.secc(p, "Recibir datos de la PC vieja")
        a = self.card(p)
        self.L(a, "Conecta el cable de red entre las dos PCs. IP de este equipo:", 9, color=C["label2"],
               wraplength=S(400)).pack(fill="x")
        self.fr_ips = tk.Frame(a, bg=C["card"])
        self.fr_ips.pack(fill="x", pady=(S(4), S(6)))
        self.L(a, "Carpeta de destino", 9, color=C["label2"]).pack(fill="x")
        fila = tk.Frame(a, bg=C["card"])
        fila.pack(fill="x", pady=(S(2), S(8)))
        self.lbl_dest = self.L(fila, self.destino, 9, wraplength=S(260))
        self.lbl_dest.pack(side="left", fill="x", expand=True)
        self.boton(fila, "Cambiar", self.cambiar_destino, h=30, w=80, bold=False).pack(side="right")
        self.btn_receptor = self.boton(a, "Activar receptor", self.iniciar_receptor, C["blue"], h=44)
        self.btn_receptor.pack(fill="x")
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x", pady=(S(8), 0))
        self.btn_ipfija_n = self.boton(f2, "Cable directo (IP fija)", lambda: self.toggle_ip_fija(IP_DIRECTA_NUEVA),
                                       h=30, w=170, accion=False, bold=False)
        self.btn_ipfija_n.pack(side="left")
        self.L(a, "Úsalo solo si «Auto-buscar» no encuentra la PC (por ejemplo si la PC vieja tiene IP fija). "
                  "Hay que activarlo en las DOS PCs; se restaura solo al terminar.",
               8, color=C["label2"], wraplength=S(400)).pack(fill="x", pady=(S(4), 0))
        self.secc(p, "PC vieja detectada")
        a = self.card(p)
        self.lbl_vieja = self.L(a, "Aún no se conecta la PC vieja.", 9, color=C["label2"], wraplength=S(400))
        self.lbl_vieja.pack(fill="x")

    def _nueva2(self, p):
        self.secc(p, "Nombre del equipo")
        a = self.card(p)
        g = tk.Frame(a, bg=C["card"])
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.campo(g, 0, "Base", self.v_base)
        self.lbl_host = self.L(a, "", 18, True, color=C["blue"])
        self.lbl_host.pack(fill="x", pady=(S(6), 0))
        self.lbl_host_ok = self.L(a, "", 8, color=C["label2"], wraplength=S(400))
        self.lbl_host_ok.pack(fill="x")
        Switch(a, "Separar con guion medio", self.v_guion, C["card"], self.f(9),
               detalle="124MD243H47 (como tu Tabla 2)  ↔  124-M-D2-43H47 (como el manual)",
               wrap=330).pack(fill="x", pady=(S(8), 0))

        self.secc(p, "Red corporativa")
        a = self.card(p)
        g = tk.Frame(a, bg=C["card"])
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.campo(g, 0, "WiFi (SSID)", self.v_ssid)
        self.campo(g, 1, "Clave WiFi", self.v_pass_wifi, show="•")
        f1 = tk.Frame(a, bg=C["card"])
        f1.pack(fill="x", pady=(S(6), S(4)))
        f1.columnconfigure((0, 1), weight=1, uniform="r")
        self.boton(f1, "Conectar WiFi", self.conectar_wifi, h=34).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.boton(f1, "Verificar red", self.verificar_red, h=34).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.L(a, "Con el WiFi puedes dejar el cable conectado a la PC vieja y unir al dominio al mismo tiempo.",
               8, color=C["label2"], wraplength=S(400)).pack(fill="x", pady=(0, S(6)))
        self.f_red = FilaEstado(a, "Red corporativa (DHCP)", C["card"], self.f(9), wrap=300)
        self.f_dns = FilaEstado(a, "DNS resuelve el dominio", C["card"], self.f(9), wrap=300)
        self.f_dc = FilaEstado(a, "Controlador de dominio responde", C["card"], self.f(9), wrap=300)
        self.f_hora = FilaEstado(a, "Hora sincronizada", C["card"], self.f(9), "Sincronizar", self.sincronizar_hora,
                                 wrap=250)
        for fr in (self.f_red, self.f_dns, self.f_dc, self.f_hora):
            fr.pack(fill="x", pady=S(3))

        self.secc(p, "Unir al dominio")
        a = self.card(p)
        self.lbl_dominio = self.L(a, "Dominio: " + (self.cfg["dominio"] or "(VACÍO: abre Ajustes)"), 9, True)
        self.lbl_dominio.pack(fill="x", pady=(0, S(4)))
        g = tk.Frame(a, bg=C["card"])
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.campo(g, 0, "Usuario", self.v_user_dom)
        self.campo(g, 1, "Contraseña", self.v_pass_dom, show="•")
        Switch(a, "Recordar usuario y claves en esta USB", self.v_recordar, C["card"], self.f(9),
               detalle="Se guardan en credenciales.ini (texto plano). Bórralo al terminar el proyecto.",
               wrap=330).pack(fill="x", pady=(S(8), S(6)))
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x")
        f2.columnconfigure((0, 1), weight=1, uniform="d")
        self.boton(f2, "Solo renombrar", self.solo_renombrar, h=38).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_unir = self.boton(f2, "Unir al dominio", self.unir_dominio, C["blue"], h=38)
        self.btn_unir.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.boton(a, "Abrir Propiedades del sistema (sysdm.cpl)", SYS.abrir_sysdm, h=30, accion=False,
                   bold=False).pack(fill="x", pady=(S(8), 0))

    def _nueva3(self, p):
        self.secc(p, "Lista final (con el usuario ya en el dominio)")
        a = self.card(p)
        self.f_fin = {}
        filas = [("dominio", "Equipo en el dominio", "Revisar", self.chk_dominio),
                 ("ivanti", "Agente IVANTI instalado", "Revisar", lambda: self.en_hilo("ivanti", self.chk_ivanti)),
                 ("politicas", "Políticas aplicadas", "Aplicar", lambda: self.en_hilo("gpo", self.chk_politicas)),
                 ("fuentes", "Tipografías (72 Noto Sans + 3 Patria)", "Instalar", self.accion_fuentes),
                 ("impresora", "Impresora de red", "Conectar", lambda: self.en_hilo("impresora", self.chk_impresora)),
                 ("impvieja", "Impresoras de la PC vieja (clonar)", "Clonar", lambda: self.en_hilo("clonar", self.chk_clonar_impresoras)),
                 ("perfil", "Perfil del usuario de la PC vieja", "Crear", lambda: self.en_hilo("perfil", self.chk_perfil_dominio)),
                 ("programas", "Programas de la carpeta «Instaladores»", "Instalar", lambda: self.en_hilo("instalar", self.instalar_programas)),
                 ("apps", "Apps de la PC vieja que faltan (winget)", "Instalar", lambda: self.en_hilo("apps", self.instalar_faltantes)),
                 ("datos", "Datos restaurados al perfil (opcional)", "Restaurar", self.abrir_restaurar)]
        for clave, texto, boton, cmd in filas:
            fr = FilaEstado(a, texto, C["card"], self.f(9), boton, cmd, wrap=250)
            fr.pack(fill="x", pady=S(4))
            self.f_fin[clave] = fr
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x", pady=(S(8), 0))
        f2.columnconfigure((0, 1), weight=1, uniform="f")
        self.boton(f2, "Ejecutar todo", self.ejecutar_todo, C["blue"], h=40).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.boton(f2, "Generar evidencia", lambda: self.en_hilo("evidencia", self.generar_evidencia),
                   C["green"], h=40).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.boton(a, "Manual para el usuario (dónde quedó todo)", lambda: self.en_hilo("manual", self.generar_manual),
                   C["blue"], h=40).pack(fill="x", pady=(S(8), 0))
        self.boton(a, "Terminar servicio (reporte final + nuevo equipo)", self.terminar_servicio, C["red"],
                   h=40).pack(fill="x", pady=(S(8), 0))

        self.secc(p, "Registro en la relación de equipos")
        a = self.card(p)
        g = tk.Frame(a, bg=C["card"])
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.campo(g, 0, "Nombre(s)", self.r_nombre)
        self.campo(g, 1, "Apellido(s)", self.r_apellido)
        self.campo(g, 2, "Serie monitor antiguo", self.r_mon_a)
        self.campo(g, 3, "Serie CPU antiguo", self.r_cpu_a)
        self.campo(g, 4, "Tipo", self.r_tipo)
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x", pady=(S(8), 0))
        f2.columnconfigure((0, 1), weight=1, uniform="g")
        self.boton(f2, "Elegir de pendientes", self.elegir_pendiente, h=36, bold=False).grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        self.boton(f2, "Guardar en la relación", self.guardar_relacion, C["green"], h=36).grid(
            row=0, column=1, sticky="ew", padx=(4, 0))
        self.lbl_faltan = self.L(a, "", 8, color=C["label2"], wraplength=S(400))
        self.lbl_faltan.pack(fill="x", pady=(S(6), 0))

    # ------------------------------------------------------------ PC VIEJA
    def _pag_vieja(self):
        p = self.pag_vieja
        self.lm_v, self.la_v = self._equipo_card(p, self.v_serie_v, self.v_monitor_v, "Este equipo (PC vieja)")
        self.secc(p, "Qué respaldar")
        a = self.card(p)
        self.fr_unidades = tk.Frame(a, bg=C["card"])
        self.fr_unidades.pack(fill="x")
        Switch(a, "Carpeta C:\\Users (todos los perfiles)", self.v_users, C["card"], self.f(9),
               detalle="Como administrador se leen también los perfiles de otros usuarios.",
               wrap=330).pack(fill="x", pady=S(4))
        Switch(a, "Omitir cachés y temporales", self.v_cache, C["card"], self.f(9),
               detalle="Quita AppData\\Local, cachés y .tmp (rápido). Conserva marcadores de Chrome/Edge, "
                       "firmas de Outlook y todo lo demás.", wrap=330).pack(fill="x", pady=S(4))
        self.lbl_avisos = self.L(a, "", 8, color=C["yellow"], wraplength=S(400))
        self.lbl_avisos.pack(fill="x", pady=(S(4), 0))
        self.L(a, "Extras (opcional)", 9, color=C["label2"]).pack(fill="x", pady=(S(8), S(2)))
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x")
        f2.columnconfigure((0, 1, 2), weight=1, uniform="x")
        for i, (t, cmd, col) in enumerate((("+ Carpeta", self.add_carpeta, C["blue"]),
                                            ("+ Archivos", self.add_archivos, C["blue"]),
                                            ("Quitar", self.quitar_extra, C["red"]))):
            self.boton(f2, t, cmd, C["fill"], col, h=32).grid(row=0, column=i, sticky="ew", padx=2)
        marco = tk.Frame(a, bg=C["fill"])
        marco.pack(fill="x", pady=(S(6), 0))
        self.lb_extra = tk.Listbox(marco, height=3, bg=C["fill"], fg=C["label"], font=self.f(9),
                                   selectbackground=C["blue"], relief="flat", highlightthickness=0, bd=0,
                                   activestyle="none", selectmode="extended")
        self.lb_extra.pack(fill="x", padx=S(8), pady=S(6))

        self.secc(p, "Enviar a la PC nueva")
        a = self.card(p)
        self.L(a, "IP de la PC nueva (la muestra el receptor)", 9, color=C["label2"]).pack(fill="x", pady=(0, S(3)))
        fila = tk.Frame(a, bg=C["card"])
        fila.pack(fill="x")
        self.entry_ip = self.entrada(fila, self.v_ip)
        self.entry_ip.pack(side="left", fill="x", expand=True, ipady=S(6), padx=(0, S(8)))
        self.btn_buscar = self.boton(fila, "Auto-buscar", self.buscar, C["gray"], h=34, w=100)
        self.btn_buscar.pack(side="right")
        self.btn_ipfija_v = self.boton(a, "Cable directo (IP fija)", lambda: self.toggle_ip_fija(IP_DIRECTA_VIEJA),
                                       h=30, accion=False, bold=False)
        self.btn_ipfija_v.pack(fill="x", pady=(S(8), 0))
        f2 = tk.Frame(a, bg=C["card"])
        f2.pack(fill="x", pady=(S(8), 0))
        f2.columnconfigure(0, weight=1, uniform="e")
        f2.columnconfigure(1, weight=2, uniform="e")
        self.boton(f2, "Analizar", lambda: self.accion_emisor(True), C["gray"], h=42).grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        self.boton(f2, "Enviar", lambda: self.accion_emisor(False), C["green"], h=42).grid(
            row=0, column=1, sticky="ew", padx=(4, 0))
        self.secc(p, "Cierre de la PC vieja")
        a = self.card(p)
        self.lbl_cierre = self.L(a, "Tras el envío se genera la ficha de cierre y un aviso «NO_BORRAR_HASTA» en el "
                                    "escritorio. Esta herramienta no borra nada.", 9, color=C["label2"], wraplength=S(400))
        self.lbl_cierre.pack(fill="x", pady=(0, S(8)))
        self.boton(a, "Verificar y liberar (después de 2 días)", lambda: self.en_hilo("liberar", self.verificar_liberar),
                   C["fill"], C["blue"], h=36, accion=False).pack(fill="x")

    # ------------------------------------------------------------ Actividad
    def _actividad(self, p):
        self.secc(p, "Actividad")
        a = self.card(p)
        fila = tk.Frame(a, bg=C["card"])
        fila.pack(fill="x")
        self.lbl_pct = self.L(fila, "0%", 28, True, width=5)
        self.lbl_pct.pack(side="left")
        self.lbl_estado = self.L(fila, "Listo.", 10, color=C["label2"], wraplength=S(250))
        self.lbl_estado.pack(side="left", fill="x", expand=True)
        self.progress = Capsula(a, C["card"])
        self.progress.pack(fill="x", pady=(S(6), S(8)))
        st = tk.Frame(a, bg=C["card"])
        st.pack(fill="x")
        st.columnconfigure((0, 1), weight=1, uniform="s")
        self.vals = {}
        for i, (k, cap) in enumerate((("arch", "ARCHIVOS"), ("datos", "DATOS"), ("vel", "VELOCIDAD"),
                                      ("rest", "TIEMPO RESTANTE"))):
            r, c = (i // 2) * 2, i % 2
            self.L(st, cap, 7, color=C["label2"]).grid(row=r, column=c, sticky="w")
            lb = self.L(st, "—", 10, True)
            lb.grid(row=r + 1, column=c, sticky="w", pady=(0, S(4)))
            self.vals[k] = lb
        self.lbl_actual = self.L(a, "", 8, color=C["label2"], wraplength=S(400))
        self.lbl_actual.pack(fill="x", pady=(S(2), S(6)))
        self.txt = tk.Text(a, height=14, bg=C["fill"], fg=C["label"], font=self.f(8), relief="flat",
                           highlightthickness=0, bd=0, wrap="word", state="disabled", padx=S(8), pady=S(6))
        for k, v in COL.items():
            self.txt.tag_config(str(k), foreground=v)
        self.txt.pack(fill="x")
        self.btn_cancel = RoundedButton(a, "Cancelar", self.cancelar, C["fill"], C["red"], C["card"],
                                        self.f(10, True), height=38)
        self.btn_cancel.pack(fill="x", pady=(S(10), 0))
        self.btn_cancel.set_enabled(False)

    # ------------------------------------------------------------ RELACIÓN
    def _pag_relacion(self, p):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Mb.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["label"],
                     rowheight=S(28), borderwidth=0, font=self.f(9))
        st.configure("Mb.Treeview.Heading", background=C["fill"], foreground=C["label"], relief="flat",
                     font=self.f(9, True), padding=S(6))
        st.map("Mb.Treeview", background=[("selected", C["blue"])], foreground=[("selected", "#FFFFFF")])
        st.configure("Mb.Vertical.TScrollbar", background=C["gray"], troughcolor=C["card"],
                     bordercolor=C["card"], arrowcolor=C["label2"], relief="flat")
        st.configure("Mb.Horizontal.TScrollbar", background=C["gray"], troughcolor=C["card"],
                     bordercolor=C["card"], arrowcolor=C["label2"], relief="flat")
        top = tk.Frame(p, bg=C["bg"])
        top.pack(fill="x", pady=(S(8), S(6)), padx=S(8))
        self.lbl_rel_res = tk.Label(top, text="", font=self.f(10, True), bg=C["bg"], fg=C["label"], anchor="w")
        self.lbl_rel_res.pack(side="left")
        for texto, cmd, col in (("Abrir carpeta", self.abrir_carpeta_relacion, C["blue"]),
                                ("Importar tabla…", self.importar_tabla, C["blue"]),
                                ("Pegar usuarios pendientes", self.pegar_pendientes, C["blue"]),
                                ("Editar fila", self.editar_fila, C["blue"])):
            b = RoundedButton(top, texto, cmd, C["fill"], col, C["bg"], self.f(9, True), height=34,
                              width=170 if len(texto) > 14 else 120)
            b.pack(side="right", padx=(S(6), 0))
        marco = tk.Frame(p, bg=C["card"])
        marco.pack(fill="both", expand=True, padx=S(8), pady=(0, S(6)))
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(marco, columns=COLUMNAS, show="headings", style="Mb.Treeview", selectmode="browse")
        anchos = [150, 150, 110, 120, 125, 110, 80, 120, 120, 90, 300]
        for c, w in zip(COLUMNAS, anchos):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=S(w), minwidth=S(60), stretch=False)
        vs = ttk.Scrollbar(marco, orient="vertical", command=self.tree.yview, style="Mb.Vertical.TScrollbar")
        hs = ttk.Scrollbar(marco, orient="horizontal", command=self.tree.xview, style="Mb.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("Hecho", foreground=C["green"])
        self.tree.tag_configure("Incompleto", foreground=C["yellow"])
        self.tree.tag_configure("Pendiente", foreground=C["label2"])
        self.tree.bind("<Double-1>", lambda e: self.editar_fila())
        self.lbl_rel_ruta = tk.Label(p, text="", font=self.f(8), bg=C["bg"], fg=C["label2"], anchor="w")
        self.lbl_rel_ruta.pack(fill="x", padx=S(10), pady=(0, S(4)))

    # ==================================================================
    # Modos y páginas
    # ==================================================================
    def cambiar_modo(self, i):
        self.modo = i
        self.estado["modo"] = i
        self.pag_nueva.pack_forget()
        self.pag_vieja.pack_forget()
        if i == 2:
            self.fr_trabajo.grid_remove()
            self.pag_rel.grid()
            self.refrescar_relacion()
        else:
            self.pag_rel.grid_remove()
            self.fr_trabajo.grid()
            (self.pag_nueva if i == 0 else self.pag_vieja).pack(fill="x")
            if i == 0:
                self.cambiar_sub(getattr(self, "sub", self._sub_sugerida()))
            else:
                self.refrescar_unidades()
        self.sf_izq.arriba()
        self.actualizar_banner()

    def _sub_sugerida(self):
        if self.estado.get("unido") or self.info.get("unido"):
            return 2
        if self.estado.get("recibido"):
            return 1
        return 0

    def cambiar_sub(self, i):
        self.sub = i
        self.subseg.seleccionar(i)
        for fr in self.sub_frames:
            fr.pack_forget()
        self.sub_frames[i].pack(fill="x")
        self.sf_izq.arriba()
        if i == 1:
            self.actualizar_hostname()
        if i == 2:
            self.rellenar_registro()
            self.actualizar_faltantes()

    def set_ocupado(self, v):
        self.ocupado = v
        self.seg.activo = not v
        self.seg._dibujar()
        self.subseg.activo = not v
        self.subseg._dibujar()
        for b in self.btns_accion:
            b.set_enabled(not v)
        self.btn_cancel.set_enabled(v)
        SYS.evitar_suspension(v)

    # ==================================================================
    # Cola UI
    # ==================================================================
    def tick(self):
        try:
            for _ in range(300):
                try:
                    fn, args = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception as e:
                    print("ui:", e)
            self.refrescar()
        finally:
            self.root.after(150, self.tick)

    def _log(self, texto, nivel):
        self.txt.config(state="normal")
        self.txt.insert(tk.END, f"{datetime.now():%H:%M:%S}  {texto}\n", str(nivel))
        if int(self.txt.index("end-1c").split(".")[0]) > 700:
            self.txt.delete("1.0", "200.0")
        self.txt.see(tk.END)
        self.txt.config(state="disabled")

    def refrescar(self):
        if not self.job:
            return
        p = self.job.prog
        now = time.monotonic()
        if p.fase == "analizando":
            self.lbl_pct.config(text="…")
            self.progress.set(0)
            self.vals["arch"].config(text=f"{p.files_total:,}")
            self.vals["datos"].config(text=fmt_bytes(p.bytes_total))
            self.vals["vel"].config(text="—")
            self.vals["rest"].config(text="—")
            self.lbl_actual.config(text="Analizando carpetas…")
        elif p.fase in ("transfiriendo", "fin"):
            if p.completo:
                pct = 100
            elif p.bytes_total > 0:
                pct = min(99, int(min(p.bytes_done, p.bytes_total) * 100 / p.bytes_total))
            else:
                pct = 0
            rest = None
            if p.fase == "transfiriendo":
                dt = now - self._t_last
                if dt >= 0.5:
                    inst = (p.bytes_done - self._b_last) / dt
                    self._vel = inst if self._vel == 0 else 0.7 * self._vel + 0.3 * inst
                    self._t_last, self._b_last = now, p.bytes_done
                if self._vel > 1:
                    rest = (p.bytes_total - p.bytes_done) / self._vel
                self.vals["vel"].config(text=f"{fmt_bytes(self._vel)}/s")
                self.lbl_actual.config(text=acortar(p.actual, 70))
            else:
                self.vals["vel"].config(text="—")
                self.lbl_actual.config(text="")
            self.lbl_pct.config(text=f"{pct}%")
            self.progress.set(pct)
            extra = f"  (+{p.files_ya:,} ya estaban)" if p.files_ya else ""
            self.vals["arch"].config(text=f"{p.files_done:,} / {p.files_total:,}{extra}")
            self.vals["datos"].config(text=f"{fmt_bytes(p.bytes_done)} / {fmt_bytes(p.bytes_total)}")
            self.vals["rest"].config(text=fmt_tiempo(rest) if p.fase == "transfiriendo" else "—")
        elif p.fase == "esperando":
            self.lbl_pct.config(text="⏳")
            self.vals["vel"].config(text="—")

    def nuevo_job(self):
        self.job = M.Job(self.log)
        self._vel, self._t_last, self._b_last = 0.0, time.monotonic(), 0
        self.txt.config(state="normal")
        self.txt.delete("1.0", tk.END)
        self.txt.config(state="disabled")
        self.lbl_pct.config(text="0%")
        self.progress.set(0)
        self.lbl_actual.config(text="")
        for k in self.vals:
            self.vals[k].config(text="—")
        self.set_ocupado(True)
        self.sf_der.arriba()

    def cancelar(self):
        if self.job and self.ocupado:
            self.log("Cancelando…", "warn")
            self.job.abortar()

    def cerrar(self):
        if self.ocupado:
            if not messagebox.askyesno("Cerrar", "Hay una operación en curso. ¿Cancelarla y salir?"):
                return
            self.job.abortar()
        if SYS.ip_fija_pendiente():
            SYS.ip_fija_restaurar()
        if self.fw_abierto:
            SYS.cerrar_firewall()
        SYS.evitar_suspension(False)
        U.escribir_json(self.est_path, self.estado)
        self.root.destroy()

    def guardar_estado(self):
        try:
            U.escribir_json(self.est_path, self.estado)
        except OSError:
            pass

    # ==================================================================
    # Información del equipo
    # ==================================================================
    def _cargar_info(self):
        info = SYS.info_pc()
        adap = SYS.adaptadores()
        self.ui(self.aplicar_info, info, adap)

    def aplicar_info(self, info, adap):
        self.info, self.adaptadores = info, adap
        serie, mon = info.get("serie", ""), SYS.series_monitores(info)
        for v, val in ((self.v_serie, serie), (self.v_serie_v, serie)):
            v.set(val)
        for v, val in ((self.v_monitor, mon), (self.v_monitor_v, mon)):
            v.set(val)
        modelo = " ".join(x for x in (info.get("fabricante", ""), info.get("modelo", "")) if x) or "—"
        extra = f" · {info.get('perfil', '')}" if info.get("perfil") else ""
        for lm in (self.lm_n, self.lm_v):
            lm.config(text=modelo + extra + (f"\nSerie obtenida de: {info.get('serie_fuente', '')}"
                                             if info.get("serie") else "\nSerie NO detectada: escríbela"))
        adm = "Sí" if info.get("admin") else "NO — abre el programa como administrador"
        for la in (self.la_n, self.la_v):
            la.config(text=adm, fg=C["green"] if info.get("admin") else C["red"])
        if not self.v_base.get():
            self.v_base.set(self._base_default())
        self.mostrar_ips()
        if not self.r_tipo.get() or self.r_tipo.get() == "D2 Y P03":
            self.r_tipo.set("L2" if info.get("perfil") == "L2" else self.cfg.get("tipo_defecto", "D2 Y P03"))
        if info.get("unido") and not self.estado.get("unido"):
            self.estado["unido"] = True
        self.guardar_estado()
        self.actualizar_hostname()
        self.refrescar_unidades()
        self.actualizar_banner()

    def mostrar_ips(self):
        for w in self.fr_ips.winfo_children():
            w.destroy()
        filas = SYS.describir_ips(self.adaptadores)
        if not filas:
            self.L(self.fr_ips, "No detectada (conecta el cable)", 12, True, color=C["red"]).pack(fill="x")
        for etq, ip in filas:
            fila = tk.Frame(self.fr_ips, bg=C["card"])
            fila.pack(fill="x")
            cable = etq == "Cable"
            self.L(fila, ip, 20 if cable else 11, True, color=C["blue"] if cable else C["label2"]).pack(side="left")
            self.L(fila, f"  {etq}" + (" ← usa esta en la PC vieja" if cable else ""), 8,
                   color=C["label2"]).pack(side="left", pady=(S(8), 0))

    def _base_default(self):
        perfil = self.cfg.get("perfil") or self.info.get("perfil") or "D2"
        if self.v_guion.get():
            return f"{self.cfg['unidad']}-{self.cfg['empresa']}-{perfil}-"
        return f"{self.cfg['unidad']}{self.cfg['empresa']}{perfil}"

    def _regenerar_base(self):
        self.v_base.set(self._base_default())

    def hostname_actual(self):
        return self.v_base.get().strip() + ultimos5(self.v_serie.get())

    def actualizar_hostname(self):
        if not hasattr(self, "lbl_host"):
            return
        h = self.hostname_actual()
        ok, msg = SYS.hostname_valido(h)
        self.lbl_host.config(text=h or "—", fg=C["blue"] if ok else C["red"])
        actual = SYS.nombre_host()
        nota = msg if not ok else (f"Nombre actual: {actual}" + ("  ✓ ya coincide" if actual.lower() == h.lower() else ""))
        if not self.v_serie.get().strip():
            nota = "Falta la serie del CPU: escríbela en la pestaña 1."
        self.lbl_host_ok.config(text=nota, fg=C["red"] if not ok else C["label2"])

    # ==================================================================
    # Banner «siguiente paso»
    # ==================================================================
    def actualizar_banner(self):
        if not hasattr(self, "lbl_banner"):
            return
        adm = self.info.get("admin", True)
        m = getattr(self, "modo", 0)
        if m == 2:
            h, i, pn = self.relacion.resumen()
            t = f"Relación: {h} hechos · {i} incompletos · {pn} pendientes. Doble clic en una fila para editarla."
        elif not adm and self.info:
            t = "⚠  Cierra y abre el programa como administrador (clic derecho → Ejecutar como administrador)."
        elif m == 1:
            if self.estado.get("enviado"):
                t = "✓ Envío completado. Retira el cable y NO borres nada de esta PC: se conserva 2 días."
            elif self.v_ip.get():
                t = "✓ PC nueva encontrada. Pulsa «Enviar a la PC nueva» y espera: no apagues nada hasta que termine."
            else:
                t = ("Buscando la PC nueva sola… Revisa que el cable esté conectado y que en la PC nueva "
                     "esté activo el receptor.")
        else:
            eq = self.clasificar_equipo()
            if eq["tipo"] == "terminada":
                t = "✓ Este equipo ya está terminado. Si es otra PC, usa «Terminar este equipo · siguiente PC»."
            elif eq["tipo"] == "ya_config":
                t = "⚠ Esta PC ya está en un dominio y no recibió datos con esta app. Confirma que es la correcta."
            elif self.estado.get("unido") or self.info.get("unido"):
                t = "Paso 3 · Equipo en el dominio. Inicia sesión con el usuario y completa la lista final y el registro."
            elif self.estado.get("recibido"):
                t = "Paso 2 · Datos recibidos ✓. Conecta el WiFi, verifica la red y pulsa «Unir al dominio»."
            else:
                t = ("Paso 1 · Conecta el cable entre las dos PCs, pulsa «Activar receptor» "
                     "y envía desde la PC vieja.")
        self.lbl_banner.config(text=t)

    # ==================================================================
    # Cable directo (IP fija) y red
    # ==================================================================
    def toggle_ip_fija(self, ip):
        if self.ip_fija_activa or SYS.ip_fija_pendiente():
            ok, msg = SYS.ip_fija_restaurar()
            self.ip_fija_activa = not ok
            self.log(msg, "ok" if ok else "err")
        else:
            a = SYS.adaptador_cable(self.adaptadores or SYS.adaptadores())
            if not a:
                messagebox.showwarning("Cable directo", "No se encontró un adaptador de red por cable.")
                return
            ok, msg = SYS.ip_fija_aplicar(a["alias"], ip)
            self.ip_fija_activa = ok
            self.log(f"IP fija {ip} en «{a['alias']}»" if ok else f"No se pudo fijar la IP: {msg}", "ok" if ok else "err")
            if ok and self.modo == 1:
                self.v_ip.set(IP_DIRECTA_NUEVA)
        t = "Restaurar red (DHCP)" if self.ip_fija_activa else "Cable directo (IP fija)"
        self.btn_ipfija_n.set_text(t)
        self.btn_ipfija_v.set_text(t)
        self.en_hilo("info", self._cargar_info)

    def _auto_post_recepcion(self):
        """Tras recibir los datos: WiFi y verificación de red solos, para dejar todo listo para unir al dominio."""
        try:
            ssid, clave = self.v_ssid.get().strip(), self.v_pass_wifi.get()
            if ssid and clave:
                s2, c2 = SYS.wifi_conectado(ssid)
                if not (c2 and s2.strip().lower() == ssid.lower()):
                    self.log(f"Conectando solo al WiFi {ssid}…", "info")
                    ok, msg = SYS.wifi_conectar(ssid, clave, log=lambda t: self.log(t, "warn"))
                    self.log(msg, "ok" if ok else "err")
                    time.sleep(4)
            self._verificar_red()
        except Exception as e:
            self.log(f"Paso automático de red: {e}", "warn")

    def _auto_buscar_receptor(self):
        """PC vieja (abierta desde la descarga por el cable): busca sola a la PC nueva hasta encontrarla."""
        t0 = time.time()
        while time.time() - t0 < 900 and not self.v_ip.get():
            try:
                r = M.buscar_receptor()
            except Exception:
                r = None
            if r:
                self.ui(self.v_ip.set, r[0])
                self.estado["receptor_ok"] = r[1] or r[0]
                self.estado_txt(f"PC nueva encontrada: {r[1] or r[0]}", "ok")
                self.log(f"PC nueva encontrada: {r[1]} en {r[0]}", "ok")
                self.ui(self.actualizar_banner)
                return
            self.estado_txt("Buscando la PC nueva… (cable conectado y receptor activo)", "warn")
            time.sleep(5)

    def conectar_wifi(self):
        ssid, clave = self.v_ssid.get().strip(), self.v_pass_wifi.get()
        if not ssid or not clave:
            messagebox.showwarning("WiFi", "Escribe el SSID y la clave del WiFi.")
            return
        self.estado_txt(f"Conectando a {ssid}…", "warn")

        def run():
            ok, msg = SYS.wifi_conectar(ssid, clave, log=lambda t: self.log(t, "warn"))
            if not ok:   # el estado REAL manda: a veces conecta justo después del tiempo de espera
                s2, c2 = SYS.wifi_conectado(ssid)
                if c2 and s2.strip().lower() == ssid.lower():
                    ok, msg = True, f"Conectado a {ssid}"
            self.log(msg, "ok" if ok else "err")
            self.estado_txt(msg, "ok" if ok else "err")
            self.ui(lambda: self.en_hilo("info", self._cargar_info))
            if ok:
                time.sleep(4)
                self._verificar_red()
        self.en_hilo("wifi", run)

    def verificar_red(self):
        self.en_hilo("red", self._verificar_red)

    def _verificar_red(self):
        self.estado_txt("Verificando la red corporativa…", "warn")
        adap = SYS.adaptadores()
        res, dc = SYS.verificar_red(self.cfg["dominio"], adap)
        self.dc_ip = dc
        filas = [self.f_red, self.f_dns, self.f_dc, self.f_hora]
        for fr in filas:
            self.ui(fr.set, None, "")
        for fr, (nombre, nivel, detalle) in zip(filas, res):
            self.ui(fr.set, nivel, detalle)
        self.ui(self.mostrar_ips)
        self.adaptadores = adap
        errores = [r for r in res if r[1] == "err"]
        self.estado_txt("Red lista para unir al dominio" if not errores else "La red necesita atención",
                        "ok" if not errores else "err")
        self.res["red"] = res

    def sincronizar_hora(self):
        if not self.dc_ip:
            messagebox.showinfo("Hora", "Primero pulsa «Verificar red».")
            return

        def run():
            self.log("Sincronizando la hora con el dominio…", "info")
            for ln in SYS.sincronizar_hora(self.dc_ip):
                self.log(ln, None)
            self._verificar_red()
        self.en_hilo("hora", run)

    # ==================================================================
    # RECEPTOR (PC nueva)
    # ==================================================================
    def cambiar_destino(self):
        d = filedialog.askdirectory(title="Carpeta de destino de la migración")
        if d:
            self.destino = d if os.path.basename(d).lower() == "backup_migracion" else os.path.join(d, "BACKUP_MIGRACION")
            self.cfg["destino_backup"] = self.destino
            U.guardar_config(self.cfg)
            self.lbl_dest.config(text=self.destino)

    def iniciar_receptor(self):
        if not self.info.get("admin", True):
            if not messagebox.askyesno("Sin permisos de administrador",
                                       "Sin administrador no se pueden abrir los puertos del Firewall solos. "
                                       "¿Continuar de todos modos?"):
                return
        self.nuevo_job()
        self.en_hilo("info", self._cargar_info)
        threading.Thread(target=self._hilo_receptor, args=(self.job, self.destino), daemon=True).start()

    def _hilo_receptor(self, job, destino):
        stop = threading.Event()
        threading.Thread(target=M.servidor_discovery, args=(stop,), daemon=True).start()
        p = job.prog
        try:
            if SYS.abrir_firewall(M.PORT_TRANSFER, M.PORT_DISCOVER, "80,%d" % ARR.PORT_ARRANQUE):
                self.fw_abierto = True
                job.log("Firewall: puertos abiertos automáticamente.", "ok")
            ips = ", ".join(ip for _, ip in SYS.describir_ips()) or "sin IP"
            try:
                self.srv_arranque, pto = ARR.iniciar_auto(U.dir_app())
                ip1 = (SYS.describir_ips() or [("", "")])[0][1]
                nom = SYS.nombre_host()
                self.arranque_info = {"url": ARR.url_base(ip1, pto), "cmd": ARR.comando_vieja(ip1, pto),
                                      "corto": ARR.url_base(nom, pto).replace("http://", ""), "ip": ip1}
                if hasattr(self, "rev"):
                    self.rev += 1
                job.log(f"PC vieja sin la app: abre el navegador y escribe {ARR.url_base(ip1, pto)}", "info")
            except OSError:
                pass
            job.log(f"Receptor activo · IP {ips} · puerto {M.PORT_TRANSFER}", "info")
            job.log(f"Destino: {destino}", None)
            self.estado_txt("Esperando al emisor…", "info")
            res = M.recibir(job, destino, limite=int(self.cfg["limite_gb"]) * 1024 ** 3,
                            al_conectar=lambda h, ip, inf: self.ui(self.on_pc_vieja, h, ip, inf))
            p.fase, p.completo = "fin", True
            malos = res["malos"]
            self.estado["recibido"] = True
            self.estado["transfer"] = {"ok": res["ok"], "ya": res["ya"], "malos": len(malos), "bytes": res["bytes"],
                                       "seg": res["seg"], "host_viejo": res["host"], "total": res["total"],
                                       "fecha": datetime.now().isoformat(timespec="seconds")}
            self.res["transfer"] = self.estado["transfer"]
            self.guardar_estado()
            self.estado_txt("¡Migración completada!" if not malos else "Completada con advertencias",
                            "ok" if not malos else "warn")
            job.log(f"Listo: {res['ok'] + res['ya']:,} de {res['total']:,} archivos verificados.", "ok")
            texto = (f"{res['ok'] + res['ya']:,} archivos recibidos y verificados ({fmt_bytes(res['bytes'])} en esta "
                     f"sesión, {fmt_tiempo(res['seg'])}).\n\nCarpeta:\n{destino}\n\nReporte: _REPORTE_MIGRACION.txt")
            if malos:
                self.ui(messagebox.showwarning, "Migración con advertencias",
                        texto + f"\n\n{len(malos)} archivos fallaron la verificación y se descartaron.")
            else:
                self.ui(messagebox.showinfo, "Migración finalizada", texto + "\n\nSiguiente: pestaña «2 · Dominio».")
            self.ui(self.cambiar_sub, 1)
            threading.Thread(target=self._auto_post_recepcion, daemon=True).start()
        except M.Cancelado:
            p.fase = "error"
            self.estado_txt("Receptor detenido.", "warn")
            job.log("Receptor detenido por el usuario.", "warn")
        except Exception as e:
            p.fase = "error"
            if job.cancel.is_set():
                self.estado_txt("Receptor detenido.", "warn")
            else:
                self.estado_txt("Error en el receptor", "err")
                job.log(f"Error: {e}", "err")
                self.ui(messagebox.showerror, "Error en el receptor", f"{e}")
        finally:
            stop.set()
            job.abortar()
            self.ui(self.set_ocupado, False)
            self.ui(self.actualizar_banner)

    def on_pc_vieja(self, host, ip, info):
        self.estado["info_vieja"] = info
        self.estado["host_viejo"] = host
        adop = U.adoptar_config(self.cfg, info.get("config"))
        if adop:
            U.guardar_config(self.cfg)
            self.log("Ajustes tomados de la PC vieja: " + ", ".join(adop), "ok")
        mons = ", ".join(m.get("serie", "") for m in info.get("monitores", []) if m.get("serie"))
        self.lbl_vieja.config(text=f"{host}  ({ip})\nSerie CPU: {info.get('serie', '—')}\nMonitor: {mons or '—'}"
                                   f"\nUsuario en sesión: {info.get('usuario', '—')}", fg=C["label"])
        self.r_cpu_a.set(info.get("serie", ""))
        self.r_mon_a.set(mons)
        if info.get("programas"):
            self.lbl_vieja.config(text=self.lbl_vieja.cget("text") + f"\nProgramas instalados recibidos: {len(info['programas'])}")
        self.guardar_estado()

    # ==================================================================
    # Dominio (PC nueva)
    # ==================================================================
    def _credenciales_ok(self):
        if not self.v_user_dom.get().strip() or not self.v_pass_dom.get():
            messagebox.showwarning("Dominio", "Escribe el usuario y la contraseña de dominio.")
            return False
        return True

    def _recordar(self):
        if self.v_recordar.get():
            guardar_credenciales({"usuario": self.v_user_dom.get().strip(), "clave": self.v_pass_dom.get(),
                                  "wifi_ssid": self.v_ssid.get().strip(), "wifi_clave": self.v_pass_wifi.get()})
        else:
            borrar_credenciales()
        self.cfg["usuario_dominio"] = self.v_user_dom.get().strip()
        self.cfg["wifi_ssid"] = self.v_ssid.get().strip()
        self.cfg["guion"] = self.v_guion.get()
        self.cfg["base_hostname"] = self.v_base.get().strip()
        U.guardar_config(self.cfg)

    def solo_renombrar(self):
        h = self.hostname_actual()
        ok, msg = SYS.hostname_valido(h)
        if not ok:
            messagebox.showerror("Nombre no válido", msg)
            return
        if not messagebox.askyesno("Renombrar", f"¿Cambiar el nombre del equipo a {h}?\nSe aplica al reiniciar."):
            return
        self._recordar()

        def run():
            ok, msg = SYS.renombrar_equipo(h)
            self.log(msg, "ok" if ok else "err")
            self.ui((messagebox.showinfo if ok else messagebox.showerror), "Renombrar", msg)
        self.en_hilo("renombrar", run)

    def unir_dominio(self):
        h = self.hostname_actual()
        ok, msg = SYS.hostname_valido(h)
        if not ok:
            messagebox.showerror("Nombre no válido", msg)
            return
        if not self.cfg["dominio"].strip():
            messagebox.showerror("Falta el dominio", "El dominio está vacío. Abre «Ajustes» y escribe "
                                 "dominio.semarnat.gob.mx (o copia config_mainbit.json junto al programa).")
            return
        if not self.v_serie.get().strip():
            messagebox.showerror("Falta la serie", "Escribe la serie del CPU para formar el nombre del equipo.")
            return
        if not self.info.get("admin", True):
            messagebox.showerror("Administrador", "Abre el programa como administrador para unir al dominio.")
            return
        if not self._credenciales_ok():
            return
        errores = [r for r in self.res.get("red", []) if r[1] == "err"]
        if not self.res.get("red"):
            messagebox.showinfo("Red", "Primero pulsa «Verificar red» para comprobar la conexión.")
            return
        if errores and not messagebox.askyesno(
                "La red tiene problemas",
                "La verificación de red encontró errores:\n- " + "\n- ".join(r[0] for r in errores) +
                "\n\nUnir al dominio probablemente fallará. ¿Intentarlo de todos modos?"):
            return
        if not messagebox.askyesno("Unir al dominio",
                                   f"Nombre: {h}\nDominio: {self.cfg['dominio']}\n\nSe unirá al dominio y luego "
                                   "pedirá reiniciar. ¿Continuar?"):
            return
        self._recordar()
        self.nuevo_job()

        def run():
            self.estado_txt("Uniendo al dominio…", "warn")
            self.log(f"Renombrando a {h} y uniendo a {self.cfg['dominio']}…", "info")
            ok, msg = SYS.unir_dominio(self.cfg["dominio"], self.v_user_dom.get().strip(),
                                       self.v_pass_dom.get(), h)
            self.log(msg.splitlines()[0], "ok" if ok else "err")
            if ok:
                self.estado.update({"unido": True, "hostname": h})
                self.guardar_estado()
                self.estado_txt("Unido al dominio. Falta reiniciar.", "ok")
                if self.preguntar("Equipo unido", f"{h} se unió al dominio.\n\n¿Reiniciar ahora? "
                                                  "Después inicia sesión con «Otro usuario» y el usuario final."):
                    SYS.reiniciar(10)
                    self.log("Reiniciando en 10 segundos…", "warn")
            else:
                self.estado_txt("No se pudo unir al dominio", "err")
                self.ui(messagebox.showerror, "No se pudo unir al dominio", msg)
            self.ui(self.set_ocupado, False)
            self.ui(self.actualizar_banner)
        threading.Thread(target=run, daemon=True).start()

    # ==================================================================
    # Lista final (PC nueva, ya en el dominio)
    # ==================================================================
    def chk_dominio(self):
        unido, dom = SYS.estado_dominio()
        host, esperado = SYS.nombre_host(), self.hostname_actual()
        if unido:
            ok = host.lower() == esperado.lower() or not self.v_serie.get().strip()
            self.f_fin["dominio"].set("ok" if ok else "warn", f"{host} en {dom}" + ("" if ok else f" (se esperaba {esperado})"))
            self.estado["unido"] = True
        else:
            self.f_fin["dominio"].set("err", "El equipo NO está en un dominio todavía.")
        self.res["dominio"] = (unido, dom, host)
        self.guardar_estado()
        self.actualizar_banner()

    def chk_ivanti(self):
        self.ui(self.f_fin["ivanti"].set, "run", "Buscando el agente…")
        r = SYS.ivanti_estado(self.cfg)
        self.res["ivanti"] = r
        esperado = int(self.cfg["ivanti_items"])
        corriendo = [s for s in r["servicios"] if s["estado"].lower() in ("running", "en ejecución", "en ejecucion")]
        if r["existe"]:
            det = f"{r['ruta']} · {r['items']} elementos en EPM Agent (esperado {esperado})"
            if corriendo:
                det += f" · servicio activo: {corriendo[0]['visible'] or corriendo[0]['nombre']}"
            nivel = "ok" if r["items"] >= esperado - 2 or corriendo else "warn"
            self.ui(self.f_fin["ivanti"].set, nivel, det)
            return nivel == "ok"
        self.ui(self.f_fin["ivanti"].set, "warn", "Aún no aparece (se instala solo en segundo plano). "
                                                    "Se sigue buscando automáticamente.")
        self.iniciar_vigilancia_ivanti()
        return False

    def iniciar_vigilancia_ivanti(self):
        if self.vigilando_ivanti:
            return
        self.vigilando_ivanti = True

        def run():
            fin = time.time() + 20 * 60
            while time.time() < fin and self.vigilando_ivanti:
                time.sleep(20)
                r = SYS.ivanti_estado(self.cfg)
                if r["existe"]:
                    self.res["ivanti"] = r
                    self.ui(self.f_fin["ivanti"].set, "ok", f"Detectado: {r['ruta']} · {r['items']} elementos")
                    self.log("IVANTI detectado.", "ok")
                    break
            self.vigilando_ivanti = False
        threading.Thread(target=run, daemon=True).start()

    def chk_politicas(self):
        fr = self.f_fin["politicas"]
        dominio = self.estado.get("unido") or (self.info or {}).get("unido")
        ok, nombres = False, []
        for intento in range(1, 4):
            self.ui(fr.set, "run", f"Aplicando políticas (gpupdate)… intento {intento}/3, puede tardar 1-2 min")
            ok, nombres = SYS.actualizar_politicas()
            if ok and (nombres or not dominio):
                break
            time.sleep(15)
        self.res["gpo"] = nombres
        if nombres:
            self.ui(fr.set, "ok", f"{len(nombres)} políticas: " + ", ".join(nombres[:4]) + ("…" if len(nombres) > 4 else ""))
        elif ok and dominio:
            self.ui(fr.set, "ok", "Políticas actualizadas (gpupdate terminó sin errores)")
        elif ok:
            self.ui(fr.set, "warn", "gpupdate terminó, pero el equipo aún no está en el dominio")
        else:
            self.ui(fr.set, "warn", "No se pudo actualizar: revisa que haya red hacia el controlador de dominio")

    def _resolver_dir_fuentes(self):
        d = self.cfg["fuentes_dir"]
        if d and os.path.isdir(d):
            return d
        d = SYS.buscar_dir_fuentes([U.dir_app()])
        if not d:
            d = filedialog.askdirectory(title="Selecciona la carpeta «Tipografías» (Noto Sans y Patria)")
        if d:
            self.cfg["fuentes_dir"] = d
            U.guardar_config(self.cfg)
        return d

    def accion_fuentes(self):
        d = self._resolver_dir_fuentes()
        if d:
            self.en_hilo("fuentes", self.chk_fuentes, d)

    def chk_fuentes(self, carpeta):
        archivos = SYS.listar_fuentes(carpeta)
        noto, patria = SYS.contar_fuentes(archivos)
        en, ep = int(self.cfg["fuentes_noto"]), int(self.cfg["fuentes_patria"])
        self.ui(self.f_fin["fuentes"].set, "run", f"Instalando {len(archivos)} tipografías para todos los usuarios…")

        def prog(i, n, nombre):
            if i % 10 == 0:
                self.ui(self.f_fin["fuentes"].set, "run", f"Instalando {i}/{n}…")
        ok, ya, fallos = SYS.instalar_fuentes(archivos, prog)
        presentes = sum(1 for f in archivos if SYS.fuente_instalada(f))
        self.res["fuentes"] = {"noto": noto, "patria": patria, "instaladas": ok, "ya": ya,
                               "fallos": len(fallos), "presentes": presentes, "total": len(archivos)}
        cuentas_ok = noto == en and patria == ep
        det = f"Noto Sans {noto}/{en} · Patria {patria}/{ep} · instaladas {presentes}/{len(archivos)}"
        if fallos:
            det += f" · {len(fallos)} con error"
            for f, m in fallos[:5]:
                self.log(f"Tipografía con error: {f} ({m})", "err")
        nivel = "ok" if (cuentas_ok and presentes == len(archivos) and not fallos) else "warn"
        self.ui(self.f_fin["fuentes"].set, nivel, det)

    def chk_impresora(self):
        if not self.cfg["usar_impresora"]:
            self.ui(self.f_fin["impresora"].set, None, "Desactivada en Ajustes (el cliente no la pidió).")
            return
        self.ui(self.f_fin["impresora"].set, "run", "Conectando…")
        ruta = self.cfg["impresora"]
        if SYS.impresora_existe(ruta):
            self.ui(self.f_fin["impresora"].set, "ok", "Ya está conectada.")
            self.res["impresora"] = (True, "Ya conectada")
            return
        ok, msg = SYS.conectar_impresora(ruta)
        self.res["impresora"] = (ok, msg)
        self.ui(self.f_fin["impresora"].set, "ok" if ok else "warn", msg)

    def ejecutar_todo(self):
        """Lista final automática: lo que falte se hace solo (perfil, políticas, tipografías, impresoras, apps)."""
        def run():
            self.chk_dominio()
            self.chk_perfil_dominio()
            self.chk_politicas()
            self.chk_ivanti()
            carpeta = self._dir_fuentes_silencioso()
            if carpeta:
                self.chk_fuentes(carpeta)
            elif self.cfg.get("fuentes_url") and hasattr(self, "fuentes_desde_internet"):
                self.fuentes_desde_internet()
            else:
                self.ui(self.f_fin["fuentes"].set, "warn", "Pon la dirección de descarga en Ajustes o una carpeta «Tipografias» junto al programa")
            self.chk_impresora()
            if (self.estado.get("info_vieja") or {}).get("impresoras"):
                self.chk_clonar_impresoras()
            if SYS.listar_instaladores(self.dir_instaladores()):
                self.instalar_programas()
            if self.cfg.get("auto_winget", True) and (self.estado.get("info_vieja") or {}).get("programas"):
                self.instalar_faltantes()
            self.log("Lista final revisada.", "ok")
            self.estado_txt("Lista final revisada", "ok")
            self.ui(self.actualizar_faltantes)
        self.en_hilo("todo", run)

    def _dir_fuentes_silencioso(self):
        d = self.cfg.get("fuentes_dir")
        if d and os.path.isdir(d):
            return d
        d = SYS.buscar_dir_fuentes([U.dir_app()])
        if d:
            self.cfg["fuentes_dir"] = d
            U.guardar_config(self.cfg)
        return d

    def chk_perfil_dominio(self):
        fr = self.f_fin["perfil"]
        usuario = (self.estado.get("info_vieja") or {}).get("usuario") or ""
        if not usuario:
            self.ui(fr.set, None, "Aún no se recibió el usuario de la PC vieja")
            return
        if not (self.estado.get("unido") or (self.info or {}).get("unido")):
            self.ui(fr.set, "warn", "Primero une el equipo al dominio")
            return
        self.ui(fr.set, "run", f"Creando el perfil de {usuario}…")
        ok, msg = SYS.crear_perfil_dominio(usuario, self.cfg.get("dominio", ""))
        self.log(f"Perfil de {usuario}: {msg}", "ok" if ok else "warn")
        self.ui(fr.set, "ok" if ok else "warn", msg)

    def instalar_faltantes(self):
        """Instala con winget las apps de la PC vieja que no están en esta (necesita internet y winget)."""
        fr = self.f_fin["apps"]
        viejos = (self.estado.get("info_vieja") or {}).get("programas") or []
        faltan = [p["nombre"] for p in SV.comparar(viejos, SV.programas_instalados())[0]]
        if not viejos:
            self.ui(fr.set, None, "Aún no se recibió la lista de la PC vieja")
            return
        if not faltan:
            self.ui(fr.set, "ok", "No falta ninguna app")
            return
        if not SYS.winget_disponible():
            self.ui(fr.set, "warn", f"Faltan {len(faltan)} apps; winget no está disponible: ponlas en la carpeta Instaladores")
            return
        hechas, sin = [], []
        for i, nom in enumerate(faltan, 1):
            self.ui(fr.set, "run", f"Instalando {i}/{len(faltan)}: {nom}…")
            ok, det = SYS.winget_instalar(nom)
            self.log(f"{nom}: {'instalada' if ok else det}", "ok" if ok else "warn")
            (hechas if ok else sin).append(nom)
        self.res["apps_winget"] = {"ok": hechas, "sin": sin}
        det = f"{len(hechas)} instaladas" + (f" · {len(sin)} a mano (no están en winget): " + ", ".join(sin[:5]) + ("…" if len(sin) > 5 else "") if sin else "")
        self.ui(fr.set, "ok" if not sin else "warn", det)

    # ---------------------------------------------------------------- USB: sincronizar al volver a insertarla
    def _iniciar_vigilancia_usb(self):
        import mb_local as L
        self.usb = {"local": bool(L.origen()) and L.es_local(), "presente": False, "pendiente": True, "ultima": "", "msg": ""}
        if not self.usb["local"]:
            return
        self.usb_lock = threading.Lock()

        def vigilar():
            while getattr(self, "vivo", True):
                try:
                    ruta = L.buscar_usb()
                    self.usb["presente"] = bool(ruta)
                    if self._huella() != self.usb.get("huella"):
                        self.usb["pendiente"] = True
                    if ruta and self.usb["pendiente"] and not self.ocupado:
                        self._sincronizar_usb(ruta)
                except Exception as e:
                    self.usb["msg"] = f"Error al sincronizar: {e}"
                if hasattr(self, "rev"):
                    self.rev += 1
                time.sleep(3)
        threading.Thread(target=vigilar, daemon=True).start()

    def _sincronizar_usb(self, ruta=None):
        import mb_local as L
        ruta = ruta or L.buscar_usb()
        if not ruta:
            return None
        with self.usb_lock:
            self.estado_txt("Guardando en la USB…", "warn")
            res = L.sincronizar(L.raiz_datos(), L.raiz_datos_usb(ruta), host=SYS.nombre_host(), app_local=U.dir_app(), app_usb=ruta)
            self._vaciar_usb(ruta)
            self.relacion.cargar()
            self.usb.update(huella=self._huella(), pendiente=False, ultima=datetime.now().strftime("%H:%M:%S"),
                            msg=f"Guardado: {res['relacion_usb']} equipos, {res['archivos']} archivos")
            self.log("USB sincronizada · " + self.usb["msg"] + ". Ya puedes sacarla.", "ok")
            self.usb["aviso"] = {"n": (self.usb.get("aviso") or {}).get("n", 0) + 1,
                                 "texto": "Listo: ya puedes sacar la USB y llevarla a la siguiente PC"}
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
            self.estado_txt("USB al día: ya puedes sacarla", "ok")
            self.ui(self.actualizar_banner)
            return res

    @staticmethod
    def _vaciar_usb(ruta):
        """Pide a Windows que escriba todo en la USB antes de avisar que se puede sacar."""
        if os.name != "nt":
            return
        try:
            k = ctypes.windll.kernel32
            h = k.CreateFileW("\\\\.\\" + os.path.splitdrive(os.path.abspath(ruta))[0], 0xC0000000, 3, None, 3, 0, None)
            if h not in (-1, 0xFFFFFFFF, None):
                k.FlushFileBuffers(h)
                k.CloseHandle(h)
        except Exception:
            pass

    def sincronizar_usb(self):
        if not getattr(self, "usb", {}).get("local"):
            self.log("Esta copia no viene de una USB: no hay nada que sincronizar.", "info")
            return
        if not self._sincronizar_usb():
            self.ui(messagebox.showinfo, "Sincronizar", "Inserta la USB «%s» y se guardará sola." % self.cfg.get("usb_etiqueta", "USB Blue"))

    def _huella(self):
        """Cambia cuando hay algo nuevo que guardar en la USB (relación o archivos generados)."""
        h = []
        try:
            h.append(os.path.getmtime(self.relacion.csv) if os.path.exists(self.relacion.csv) else 0)
            for d in ("Reportes", "Evidencias", "Cierres", "Servicios", "Diagnosticos"):
                p = os.path.join(L0.raiz_datos(), d)
                h.append(sum(len(f) for _, _, f in os.walk(p)) if os.path.isdir(p) else 0)
        except OSError:
            pass
        return tuple(h)

    def clasificar_equipo(self):
        """¿Qué es esta PC? Nueva sin tocar, en configuración, ya terminada, o la PC vieja (origen)."""
        try:
            i = self.info or {}
            host = (i.get("host") or "").upper()
            serie = (i.get("serie") or "").strip()
            if os.path.isfile(os.path.join(U.dir_app(), "modo_vieja.flag")) or getattr(self, "modo", 0) == 1:
                return {"tipo": "vieja", "texto": "PC vieja (origen de los datos)"}
            fila = None
            k = self.relacion.indice_por_serie(serie)
            if k >= 0:
                fila = self.relacion.filas[k]
            est = self.estado
            if fila and fila.get("Estado") == "Hecho" or est.get("registrado"):
                return {"tipo": "terminada", "texto": "Equipo ya terminado y registrado en la relación"}
            if est.get("unido") or i.get("unido"):
                if est.get("recibido"):
                    return {"tipo": "proceso", "texto": "En configuración: ya está en el dominio, falta la lista final y el registro"}
                return {"tipo": "ya_config", "texto": "Ya está en un dominio y no recibió datos con esta app: ¿es la PC correcta?"}
            if est.get("recibido"):
                return {"tipo": "proceso", "texto": "En configuración: datos recibidos, falta conectar WiFi y unir al dominio"}
            if est.get("hostname") or (host and not host.startswith(("DESKTOP-", "LAPTOP-", "WIN-")) and
                                       host.startswith(str(self.cfg.get("base_hostname", "@@@")).upper()[:4])):
                return {"tipo": "proceso", "texto": "En configuración: ya tiene el nombre del proyecto"}
            return {"tipo": "nueva", "texto": "PC nueva sin configurar"}
        except Exception:
            return {"tipo": "nueva", "texto": "PC nueva sin configurar"}

    def dir_instaladores(self):
        """Carpeta «Instaladores» junto al .exe: el técnico solo arrastra aquí los .msi/.exe."""
        d = os.path.join(U.dir_app(), "Instaladores")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    def abrir_instaladores(self):
        self.abrir_ruta(self.dir_instaladores())

    def instalar_programas(self):
        d = self.dir_instaladores()
        archivos = SYS.listar_instaladores(d)
        fr = self.f_fin["programas"]
        if not archivos:
            self.ui(fr.set, "warn", "La carpeta «Instaladores» está vacía. Arrastra ahí los instaladores.")
            self.log(f"Sin instaladores en {d}", "warn")
            return
        hechos = self.estado.setdefault("instalados", {})
        ok_n, fallos, saltados = 0, [], 0
        for i, ruta in enumerate(archivos, 1):
            nombre = os.path.basename(ruta)
            firma = f"{nombre}|{os.path.getsize(ruta)}"
            if firma in hechos:
                saltados += 1
                continue
            self.ui(fr.set, "run", f"Instalando {i}/{len(archivos)}: {nombre}…")
            self.estado_txt(f"Instalando {nombre}…", "warn")
            ok, det = SYS.instalar_programa(ruta, log=lambda t: self.log(t, None))
            self.log(f"{nombre}: {det}", "ok" if ok else "err")
            if ok:
                ok_n += 1
                hechos[firma] = datetime.now().isoformat(timespec="seconds")
                self.guardar_estado()
            else:
                fallos.append(nombre)
        txt = f"{ok_n} instalados" + (f" · {saltados} ya estaban" if saltados else "")
        if fallos:
            self.ui(fr.set, "warn", txt + " · revisar a mano: " + ", ".join(fallos))
        else:
            self.ui(fr.set, "ok", txt)
        self.res["instaladores"] = {"ok": ok_n, "ya": saltados, "fallos": fallos}

    def abrir_restaurar(self):
        raiz = os.path.join(self.destino, "Users")
        if not os.path.isdir(raiz):
            messagebox.showinfo("Restaurar", f"No hay respaldo en:\n{raiz}")
            return
        origenes = sorted(d for d in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, d)))
        destinos = SYS.perfiles_locales()
        if not origenes or not destinos:
            messagebox.showinfo("Restaurar", "No hay perfiles de origen o destino. El usuario debe haber "
                                             "iniciado sesión al menos una vez en esta PC.")
            return
        win = tk.Toplevel(self.root)
        win.title("Restaurar datos al perfil")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        self.L(win, "Perfil de la PC vieja (origen)", 9, color=C["label2"]).pack(padx=14, pady=(12, 2), anchor="w")
        lo = tk.Listbox(win, height=6, exportselection=False, bg=C["fill"], fg=C["label"], font=self.f(10),
                        selectbackground=C["blue"], relief="flat", highlightthickness=0, activestyle="none")
        for o in origenes:
            lo.insert(tk.END, o)
        lo.pack(padx=14, fill="x")
        self.L(win, "Perfil de esta PC (destino)", 9, color=C["label2"]).pack(padx=14, pady=(10, 2), anchor="w")
        ld = tk.Listbox(win, height=5, exportselection=False, bg=C["fill"], fg=C["label"], font=self.f(10),
                        selectbackground=C["blue"], relief="flat", highlightthickness=0, activestyle="none")
        for d in destinos:
            ld.insert(tk.END, d)
        ld.pack(padx=14, fill="x")
        self.L(win, "Copia Escritorio, Documentos, Descargas, Imágenes, Videos, Música y Favoritos sin pisar\n"
                    "archivos más nuevos. El respaldo NO se borra.", 8, color=C["label2"]).pack(padx=14, pady=8, anchor="w")

        def aplicar():
            if not lo.curselection() or not ld.curselection():
                messagebox.showwarning("Restaurar", "Elige un perfil de origen y uno de destino.")
                return
            o, d = origenes[lo.curselection()[0]], destinos[ld.curselection()[0]]
            win.destroy()
            self.restaurar(o, d)
        fila = tk.Frame(win, bg=C["card"])
        fila.pack(fill="x", padx=14, pady=(0, 12))
        RoundedButton(fila, "Restaurar", aplicar, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 110).pack(side="right")
        RoundedButton(fila, "Cancelar", win.destroy, C["fill"], C["red"], C["card"], self.f(9, True), 36, 100).pack(side="right", padx=8)

    def restaurar(self, origen, destino_perfil):
        self.nuevo_job()
        job = self.job

        def run():
            base_o = os.path.join(self.destino, "Users", origen)
            base_d = os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "Users", destino_perfil)
            job.prog.fase = "transfiriendo"
            todo_ok = True
            for carpeta in SYS.CARPETAS_PERFIL:
                src = os.path.join(base_o, carpeta)
                if not os.path.isdir(src):
                    continue
                job.log(f"Restaurando {carpeta}…", "info")
                self.estado_txt(f"Restaurando {carpeta}…", "warn")
                ok, rc = SYS.robocopy(src, os.path.join(base_d, carpeta), log=lambda t: job.log(t, None),
                                      cancelar=job.cancel.is_set)
                if not ok:
                    todo_ok = False
                    job.log(f"{carpeta}: robocopy terminó con código {rc}", "err")
            job.prog.fase, job.prog.completo = "fin", True
            self.estado_txt("Datos restaurados" if todo_ok else "Restaurado con advertencias", "ok" if todo_ok else "warn")
            if todo_ok:
                self.estado["restaurado"] = {"origen": origen, "destino": destino_perfil}
                self.guardar_estado()
            self.ui(self.f_fin["datos"].set, "ok" if todo_ok else "warn",
                    f"{origen} → {destino_perfil}" + ("" if todo_ok else " (con advertencias, ver actividad)"))
            if os.path.isdir(os.path.join(self.destino, "D")):
                job.log(f"La unidad D: de la PC vieja quedó en {os.path.join(self.destino, 'D')}", "info")
            self.ui(self.set_ocupado, False)
        threading.Thread(target=run, daemon=True).start()

    # ==================================================================
    # Registro en la relación
    # ==================================================================
    def rellenar_registro(self):
        iv = self.estado.get("info_vieja") or {}
        if iv and not self.r_cpu_a.get():
            self.r_cpu_a.set(iv.get("serie", ""))
        if iv and not self.r_mon_a.get():
            self.r_mon_a.set(", ".join(m.get("serie", "") for m in iv.get("monitores", []) if m.get("serie")))

    def datos_registro(self):
        return {"Usuario Nombre": self.r_nombre.get(), "Apellido": self.r_apellido.get(),
                "Serie cpu Nuevo": self.v_serie.get(), "Serie monitor Nuevo": self.v_monitor.get(),
                "Serie monitor antiguo": self.r_mon_a.get(), "Serie cpu antiguo": self.r_cpu_a.get(),
                "Tipo": self.r_tipo.get(), "Hostname": self.hostname_actual()}

    def actualizar_faltantes(self):
        f = faltantes(self.datos_registro())
        self.lbl_faltan.config(text=("Falta: " + ", ".join(f)) if f else "Registro completo ✓",
                               fg=C["yellow"] if f else C["green"])

    def elegir_pendiente(self):
        pend = self.relacion.pendientes()
        if not pend:
            messagebox.showinfo("Pendientes", "No hay usuarios pendientes. Cárgalos en la pestaña RELACIÓN "
                                              "(«Pegar usuarios pendientes») o escribe el nombre a mano.")
            return
        win = tk.Toplevel(self.root)
        win.title("Usuarios pendientes")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        lb = tk.Listbox(win, height=14, width=46, exportselection=False, bg=C["fill"], fg=C["label"],
                        font=self.f(10), selectbackground=C["blue"], relief="flat", highlightthickness=0,
                        activestyle="none")
        for _, f in pend:
            lb.insert(tk.END, f"{f['Usuario Nombre']} {f['Apellido']}")
        lb.pack(padx=14, pady=12, fill="both", expand=True)

        def elegir():
            if lb.curselection():
                i, f = pend[lb.curselection()[0]]
                self.idx_pendiente = i
                self.r_nombre.set(f["Usuario Nombre"])
                self.r_apellido.set(f["Apellido"])
                self.actualizar_faltantes()
            win.destroy()
        RoundedButton(win, "Elegir", elegir, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 110).pack(pady=(0, 12))

    def guardar_relacion(self):
        d = self.datos_registro()
        f = faltantes(d)
        if f and not messagebox.askyesno("Faltan datos", "Faltan: " + ", ".join(f) +
                                         "\n\n¿Guardar de todos modos? Quedará marcado como «Incompleto»."):
            return
        i = self.idx_pendiente
        if i is not None and not (0 <= i < len(self.relacion.filas) and not self.relacion.filas[i]["Serie cpu Nuevo"].strip()):
            i = None
        idx, fila = self.relacion.guardar_equipo(d, i)
        self.idx_pendiente = None
        self.estado["registrado"] = True
        self.guardar_estado()
        self.actualizar_faltantes()
        msg = f"{fila['Hostname']} guardado ({fila['Estado']}).\n\n{self.relacion.xlsx}"
        if self.relacion.error_xlsx:
            msg += f"\n\n(No se pudo actualizar el XLSX: {self.relacion.error_xlsx}. El CSV sí se guardó.)"
        messagebox.showinfo("Relación de equipos", msg)
        self.actualizar_banner()

    # ==================================================================
    # Evidencia (ServiceNow)
    # ==================================================================
    def generar_evidencia(self):
        info = self.info or {}
        host = SYS.nombre_host()
        unido, dom = SYS.estado_dominio()
        iv = self.estado.get("info_vieja") or {}
        tr = self.estado.get("transfer") or {}
        sec = []
        sec.append(("Equipo nuevo", [
            ("Hostname", host, "ok" if host.lower() == self.hostname_actual().lower() else "warn"),
            ("Serie CPU", self.v_serie.get() or "—", None),
            ("Serie monitor", self.v_monitor.get() or "—", None),
            ("Modelo", f"{info.get('fabricante', '')} {info.get('modelo', '')}".strip() or "—", None),
            ("Sistema operativo", info.get("os", "—"), None),
            ("Dominio", dom if unido else "NO unido", "ok" if unido else "err"),
            ("Usuario en sesión", info.get("usuario", "—"), None)]))
        sec.append(("Red", [(f"{etq}", ip, None) for etq, ip in SYS.describir_ips()] or [("IP", "—", None)]))
        iv_r = self.res.get("ivanti")
        sec.append(("Seguridad", [
            ("IVANTI", (f"{iv_r['ruta']} · {iv_r['items']} elementos" if iv_r and iv_r["existe"] else "no detectado"),
             "ok" if iv_r and iv_r["existe"] else "warn"),
            ("Políticas de dominio aplicadas", ", ".join(self.res.get("gpo", [])) or "sin datos",
             "ok" if self.res.get("gpo") else "warn")]))
        fu = self.res.get("fuentes")
        imp = self.res.get("impresora")
        sec.append(("Aplicativos", [
            ("Tipografías", (f"Noto Sans {fu['noto']} · Patria {fu['patria']} · presentes {fu['presentes']}/{fu['total']}"
                             if fu else "sin datos"), "ok" if fu and fu["presentes"] == fu["total"] else "warn"),
            ("Impresora", (imp[1] if imp else "sin datos"), "ok" if imp and imp[0] else "warn")]))
        sec.append(("Migración desde la PC vieja", [
            ("PC vieja", f"{iv.get('host', self.estado.get('host_viejo', '—'))}", None),
            ("Serie CPU antiguo", iv.get("serie", self.r_cpu_a.get() or "—"), None),
            ("Serie monitor antiguo", self.r_mon_a.get() or "—", None),
            ("Archivos verificados", f"{tr.get('ok', 0) + tr.get('ya', 0):,} de {tr.get('total', 0):,}" if tr else "sin datos",
             "ok" if tr and not tr.get("malos") else "warn"),
            ("Datos transferidos", fmt_bytes(tr.get("bytes", 0)) if tr else "—", None),
            ("Archivos descartados", str(tr.get("malos", 0)) if tr else "—", "ok" if tr and not tr.get("malos") else "warn")]))
        carpeta = os.path.join(self.relacion.dir, "Evidencias")
        txt, htm = escribir_evidencia(carpeta, host, f"Evidencia técnica — {host}", sec)
        self.log(f"Evidencia guardada: {txt}", "ok")
        self.ui(messagebox.showinfo, "Evidencia", f"Se generó la evidencia para ServiceNow:\n{txt}\n{htm}")
        self.ui(self.abrir_ruta, carpeta)

    # ==================================================================
    # Diagnóstico de monitores / cierre / reporte final
    # ==================================================================
    def diagnostico_monitores(self):
        texto = SYS.monitores_diagnostico()
        carpeta = os.path.join(L0.raiz_datos(), "Diagnosticos")
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, f"monitores_{SYS.nombre_host()}_{datetime.now():%Y%m%d_%H%M}.txt")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        for ln in texto.splitlines():
            if ln.strip().startswith(("Fabricante", "Serie")):
                self.log(ln.strip(), None)
        self.log(f"Diagnóstico guardado: {ruta}", "ok")
        self.ui(messagebox.showinfo, "Diagnóstico de monitores",
                f"Se guardó el diagnóstico en:\n{ruta}\n\nCompáralo con la serie de la ETIQUETA de cada "
                "monitor. Si no coinciden, mándame el archivo junto con las series de las etiquetas.")

    def verificar_liberar(self):
        carpeta = os.path.join(L0.raiz_datos(), "Cierres")
        ok, msg = SV.verificar_liberar(carpeta, SYS.nombre_host())
        self.log(msg, "ok" if ok else "warn")
        self.ui(self.lbl_cierre.config, {"text": msg, "fg": C["green"] if ok else C["yellow"]})
        self.ui((messagebox.showinfo if ok else messagebox.showwarning), "Cierre de la PC vieja", msg)

    def datos_manual(self):
        iv = self.estado.get("info_vieja") or {}
        unido, dom = SYS.estado_dominio()
        imp, fu = self.res.get("impresora"), self.res.get("fuentes")
        nuevos = SV.programas_instalados()
        viejos = iv.get("programas") or []
        faltan = [p["nombre"] for p in (SV.comparar(viejos, nuevos)[0] if viejos else [])]
        ficha = SV.ultima_ficha(os.path.join(L0.raiz_datos(), "Cierres"), iv.get("host", ""))
        borrado = SV.fecha_min_borrado().strftime("%d/%m/%Y")
        return MAN.datos_servicio(
            SYS.nombre_host(), self.destino, carpetas_perfil=SYS.CARPETAS_PERFIL,
            dominio_ok=unido, restaurado=self.estado.get("restaurado"),
            impresora=(imp[1] if imp and imp[0] else ""),
            fuentes=(f"{fu['presentes']} de {fu['total']} instaladas" if fu else ""),
            faltan=faltan, tiene_lista=bool(viejos), host_viejo=iv.get("host", self.estado.get("host_viejo", "")),
            fecha_borrado=borrado, soporte=self.cfg.get("soporte_contacto", ""))

    def generar_manual(self):
        carpeta = os.path.join(self.relacion.dir, "Reportes")
        htm, txt, copia = MAN.escribir_manual(carpeta, self.datos_manual())
        self.log(f"Manual del usuario: {htm}", "ok")
        extra = f"\nCopia en el escritorio: {copia}" if copia else ""
        self.ui(messagebox.showinfo, "Manual para el usuario", f"Se generó el manual:\n{htm}{extra}")
        self.ui(self.abrir_ruta, os.path.dirname(copia) if copia else carpeta)

    def terminar_servicio(self):
        if not messagebox.askyesno("Terminar servicio",
                                   "Se generará el reporte de ESTE equipo y el programa se reiniciará limpio "
                                   "para la siguiente PC del mismo servicio (la relación y los ajustes se conservan).\n\n¿Continuar?"):
            return
        self.en_hilo("terminar", self._terminar_servicio)

    def _terminar_servicio(self):
        self.estado_txt("Generando el reporte final…", "warn")
        info = self.info or {}
        host = SYS.nombre_host()
        iv = self.estado.get("info_vieja") or {}
        tr = self.estado.get("transfer") or {}
        nuevos = SV.programas_instalados()
        viejos = iv.get("programas") or []
        faltan, ruido, ya = SV.comparar(viejos, nuevos) if viejos else ([], 0, 0)
        unido, dom = SYS.estado_dominio()
        fu, imp, iva = self.res.get("fuentes"), self.res.get("impresora"), self.res.get("ivanti")
        sec = [("Equipo", [
            ("Hostname", host, None), ("Serie CPU", self.v_serie.get() or "—", None),
            ("Serie monitor", self.v_monitor.get() or "—", None),
            ("Modelo", f"{info.get('fabricante', '')} {info.get('modelo', '')}".strip() or "—", None),
            ("Dominio", dom if unido else "NO unido", "ok" if unido else "err"),
            ("Fecha del servicio", datetime.now().strftime("%d/%m/%Y %H:%M"), None)]),
            ("Servicio realizado", [
                ("Migración de datos", (f"{tr.get('ok', 0) + tr.get('ya', 0):,} de {tr.get('total', 0):,} archivos "
                                         f"({fmt_bytes(tr.get('bytes', 0))})") if tr else "no realizada en esta sesión",
                 "ok" if tr and not tr.get("malos") else "warn"),
                ("Archivos descartados", str(tr.get("malos", 0)) if tr else "—", None),
                ("IVANTI", (f"{iva['ruta']} · {iva['items']} elementos" if iva and iva["existe"] else "sin verificar"),
                 "ok" if iva and iva["existe"] else "warn"),
                ("Políticas de dominio", ", ".join(self.res.get("gpo", [])) or "sin verificar",
                 "ok" if self.res.get("gpo") else "warn"),
                ("Tipografías", (f"{fu['presentes']}/{fu['total']} instaladas" if fu else "sin verificar"),
                 "ok" if fu and fu["presentes"] == fu["total"] else "warn"),
                ("Impresora", imp[1] if imp else "sin verificar", "ok" if imp and imp[0] else "warn"),
                ("Programas instalados desde la carpeta", (lambda r: f"{r['ok']} instalados · {r['ya']} ya estaban" +
                 (" · fallaron: " + ", ".join(r["fallos"]) if r["fallos"] else "") if r else "sin ejecutar")(self.res.get("instaladores")),
                 "ok" if self.res.get("instaladores") and not self.res["instaladores"]["fallos"] else "warn")]),
            ("PC vieja", [("Hostname", iv.get("host", self.estado.get("host_viejo", "—")), None),
                          ("Serie CPU", iv.get("serie", "—"), None),
                          ("Serie monitor (la etiqueta manda)", self.r_mon_a.get() or "—", None)]),
            ("Programas instalados en la PC vieja que NO están en la nueva",
             [(p["nombre"], p.get("version", ""), "warn") for p in faltan]
             or [("Ninguno" if viejos else "Sin lista de la PC vieja", "", "ok" if viejos else None)]),
            ("Resumen de programas", [("En la PC vieja", str(len(viejos)), None), ("En la PC nueva", str(len(nuevos)), None),
                                      ("Ya presentes", str(ya), None), ("Componentes del sistema ignorados", str(ruido), None)]),
            ("Programas instalados en la PC nueva", [(p["nombre"], p.get("version", ""), None) for p in nuevos])]
        carpeta = os.path.join(self.relacion.dir, "Reportes")
        txt, htm = escribir_evidencia(carpeta, host, f"Reporte de servicio — {host}", sec)
        self.log(f"Reporte guardado: {txt}", "ok")
        try:
            mh, _, _ = MAN.escribir_manual(carpeta, self.datos_manual())
            self.log(f"Manual del usuario: {mh}", "ok")
        except Exception as e:
            self.log(f"No se pudo generar el manual del usuario: {e}", "warn")
        # reinicio limpio: se borra el avance de ESTE equipo y se relanza el programa (la relación se conserva)
        if getattr(self, "usb", {}).get("local"):
            try:
                os.remove(os.path.join(U.dir_app(), "credenciales.ini"))   # no dejar claves en texto plano en la PC
            except OSError:
                pass
        for k in ("recibido", "unido", "enviado", "info_vieja", "host_viejo", "transfer", "hostname", "registrado", "restaurado", "final_auto", "instalados", "receptor_ok"):
            self.estado.pop(k, None)
        self.guardar_estado()
        self.ui(messagebox.showinfo, "Servicio terminado", f"Reporte generado:\n{txt}\n\nEl programa se reiniciará.")
        self.ui(self.abrir_ruta, carpeta)
        self.ui(self.reiniciar_app, False)      # siguiente PC del mismo servicio: sin asistente

    def terminar_grupo(self):
        """Cierra el SERVICIO completo (todas las PCs del grupo): reporte general y asistente de uno nuevo."""
        if getattr(self, "usb", {}).get("local"):
            import mb_local as L
            if not L.buscar_usb():
                messagebox.showinfo("Terminar servicio", "Inserta la USB «%s» para cerrar el servicio: ahí se guarda todo "
                                    "y se junta lo de las demás PCs." % self.cfg.get("usb_etiqueta", "USB Blue"))
                return
            self._sincronizar_usb()
        h, i, pn = self.relacion.resumen()
        if not messagebox.askyesno("Terminar servicio",
                                   f"Se cerrará el servicio completo: {len(self.relacion.filas)} equipos "
                                   f"({h} hechos, {i} incompletos, {pn} pendientes).\n\nSe genera el reporte general y el "
                                   "programa abre el asistente de un servicio nuevo.\n\n¿Continuar?"):
            return
        self.en_hilo("terminar_grupo", self._terminar_grupo)

    def _terminar_grupo(self):
        h, i, pn = self.relacion.resumen()
        filas = self.relacion.filas
        sec = [("Servicio", [("Cliente / proyecto", self.cfg.get("empresa") or "—", None),
                             ("Dominio", self.cfg.get("dominio") or "—", None),
                             ("Fecha del cierre", datetime.now().strftime("%d/%m/%Y %H:%M"), None),
                             ("Equipos en la relación", str(len(filas)), None),
                             ("Terminados", str(h), "ok"), ("Incompletos", str(i), "warn" if i else None),
                             ("Pendientes", str(pn), "warn" if pn else None)]),
               ("Equipos", [(f.get("Hostname") or f.get("Serie cpu Nuevo") or "sin equipo",
                             f"{(f.get('Usuario Nombre', '') + ' ' + f.get('Apellido', '')).strip() or '—'} · {f.get('Estado', '')}",
                             "ok" if f.get("Estado") == "Hecho" else "warn") for f in filas])]
        carpeta = os.path.join(self.relacion.dir, "Reportes")
        txt, htm = escribir_evidencia(carpeta, "SERVICIO_COMPLETO", "Reporte de todo el servicio", sec)
        self.log(f"Reporte del servicio: {txt}", "ok")
        try:
            import shutil
            nombre = "".join(ch for ch in (self.cfg.get("empresa") or "Servicio") if ch.isalnum() or ch in " _-").strip() or "Servicio"
            dest = os.path.join(self.relacion.dir, "Servicios", f"{nombre}_{datetime.now():%Y-%m-%d_%H%M}")
            n = self.relacion.archivar_y_vaciar(dest)
            if getattr(self, "usb", {}).get("local"):
                import mb_local as L
                ruta_usb = L.buscar_usb()
                if ruta_usb:
                    from mb_relacion import Relacion as _R
                    _ru = L.raiz_datos_usb(ruta_usb)
                    _R(_ru).archivar_y_vaciar(os.path.join(_ru, "Servicios", os.path.basename(dest)))
            for f in os.listdir(carpeta):                       # copia de los reportes de este servicio (los originales se quedan)
                p = os.path.join(carpeta, f)
                if os.path.isfile(p):
                    shutil.copy2(p, dest)
            self.log(f"Servicio archivado en {dest} ({n} equipos). La lista queda vacía para el siguiente.", "ok")
            if hasattr(self, "rev"):
                self.rev += 1
        except Exception as e:
            self.log(f"No se pudo archivar el servicio: {e}", "err")
        for k in list(self.estado):
            if k != "modo":
                self.estado.pop(k, None)
        self.guardar_estado()
        self.ui(messagebox.showinfo, "Servicio terminado", f"Reporte general:\n{txt}\n\nRecuerda: cambia las contraseñas "
                "usadas y borra credenciales.ini. El programa abrirá el asistente de un servicio nuevo.")
        self.ui(self.abrir_ruta, carpeta)
        self.ui(self.reiniciar_app, True)

    def reiniciar_app(self, nuevo=False):
        import subprocess
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable]
            else:
                cmd = [sys.executable, os.path.abspath(__file__)]
            if nuevo:
                cmd.append("--nuevo")
            if self.ocupado:
                return
            if SYS.ip_fija_pendiente():
                SYS.ip_fija_restaurar()
            if self.fw_abierto:
                SYS.cerrar_firewall()
            subprocess.Popen(cmd, close_fds=True)
            self.root.destroy()
        except Exception as e:
            messagebox.showwarning("Reinicio", f"No se pudo reiniciar solo ({e}). Cierra y abre el programa.")

    def abrir_ruta(self, ruta):
        try:
            if SYS.IS_WIN:
                os.startfile(ruta)
        except OSError:
            pass

    # ==================================================================
    # EMISOR (PC vieja)
    # ==================================================================
    def refrescar_unidades(self):
        if not hasattr(self, "fr_unidades"):
            return
        for w in self.fr_unidades.winfo_children():
            w.destroy()
        previas = {k: v.get() for k, v in self.vars_unidad.items()}
        self.vars_unidad = {}
        sistema = (os.environ.get("SystemDrive", "C:") or "C:").upper()
        app_drive = os.path.splitdrive(U.dir_app())[0].upper()
        avisos = []
        unds = M.unidades() or ([{"letra": "D:", "tipo": "fijo", "etiqueta": "DATOS", "total": 500 * 1024 ** 3,
                                  "libre": 300 * 1024 ** 3}] if SYS.DEMO else [])
        hay_d = False
        for u in unds:
            L = u["letra"].upper()
            if L == sistema or u["tipo"] not in ("fijo", "usb"):
                continue
            usado = u["total"] - u["libre"]
            esta_app = L == app_drive
            sospechosa = any(x in u["etiqueta"].lower() for x in ("recovery", "restore", "pbr", "oem", "lenovo_"))
            por_defecto = (L == "D:" and u["tipo"] == "fijo" and not esta_app and not sospechosa)
            if L == "D:":
                hay_d = True
            var = tk.BooleanVar(value=previas.get(L, por_defecto))
            self.vars_unidad[L] = var
            det = f"{u['tipo'].upper()} · {fmt_bytes(usado)} usados de {fmt_bytes(u['total'])}"
            if esta_app:
                det += " · ES LA USB DEL PROGRAMA: no se respalda"
                var.set(False)
            if sospechosa:
                det += " · parece partición de recuperación"
            Switch(self.fr_unidades, f"Unidad {L} «{u['etiqueta'] or 'sin nombre'}»", var, C["card"], self.f(9),
                   detalle=det, wrap=330).pack(fill="x", pady=S(4))
        if not hay_d and not SYS.DEMO:
            avisos.append("⚠ Este equipo NO tiene unidad D: (revisa si debería tenerla).")
        self.lbl_avisos.config(text="\n".join(avisos))

    def add_carpeta(self):
        d = filedialog.askdirectory(title="Selecciona una carpeta de origen")
        if d and d not in self.rutas_extra:
            self.rutas_extra.append(d)
            self.lb_extra.insert(tk.END, "📁 " + d)

    def add_archivos(self):
        for f in filedialog.askopenfilenames(title="Selecciona archivos"):
            if f not in self.rutas_extra:
                self.rutas_extra.append(f)
                self.lb_extra.insert(tk.END, "📄 " + f)

    def quitar_extra(self):
        for i in reversed(self.lb_extra.curselection()):
            self.lb_extra.delete(i)
            del self.rutas_extra[i]

    def rutas_a_enviar(self):
        rutas = []
        if self.v_users.get():
            rutas.append(os.path.join((os.environ.get("SystemDrive", "C:") or "C:") + "\\", "Users"))
        for L, v in self.vars_unidad.items():
            if v.get():
                rutas.append(L + "\\")
        return rutas + list(self.rutas_extra)

    def info_envio(self):
        info = dict(self.info or {})
        info["serie"] = self.v_serie_v.get().strip()
        mons = [m for m in (x.strip() for x in self.v_monitor_v.get().split(",")) if m]
        info["monitores"] = [{"serie": m} for m in mons]
        info["programas"] = SV.programas_instalados()[:600]
        info["impresoras"] = SYS.listar_impresoras()
        info["config"] = U.config_compartida(self.cfg)
        return {k: info.get(k) for k in ("host", "usuario", "serie", "fabricante", "modelo", "monitores", "os",
                                         "programas", "impresoras", "config")}

    def buscar(self):
        self.estado_txt("Buscando receptor en la red local…", "warn")
        self.btn_buscar.set_enabled(False)

        def run():
            r = M.buscar_receptor()
            if r:
                self.ui(self.v_ip.set, r[0])
                aviso = "" if r[2] == "3" else "  ⚠ versión anterior: abre Mainbit Asistente en la PC nueva"
                self.estado_txt(f"Receptor encontrado: {r[1] or r[0]} ({r[0]}){aviso}", "ok" if not aviso else "warn")
                self.log(f"Receptor encontrado: {r[1]} en {r[0]}{aviso}", "ok" if not aviso else "warn")
            else:
                self.estado_txt("Sin respuesta del receptor", "err")
                self.ui(messagebox.showwarning, "Auto-buscar",
                        "No se encontró el receptor.\n\nRevisa: cable conectado, receptor ACTIVO en la PC nueva, "
                        "y que ambas tengan IP (a veces tarda ~30 s tras conectar el cable).\n"
                        "Si la PC vieja tiene IP fija, usa «Cable directo (IP fija)» en las dos, "
                        "o escribe la IP a mano.")
            self.ui(lambda: self.btn_buscar.set_enabled(not self.ocupado))
        self.en_hilo("buscar", run)

    def _avisos_previos(self):
        res = []
        if not self.info.get("admin", True):
            res.append(("err", "No eres administrador: no se leerán los perfiles de otros usuarios."))
        if SYS.proceso_activo("OUTLOOK.EXE"):
            res.append(("warn", "Outlook está abierto: los archivos .pst en uso NO se pueden copiar. Ciérralo."))
        ses = SYS.sesiones_activas()
        if len(ses) > 1:
            res.append(("warn", f"Hay {len(ses)} sesiones abiertas (otros usuarios conectados pueden bloquear archivos)."))
        return res

    def accion_emisor(self, solo_analizar):
        rutas = self.rutas_a_enviar()
        if not rutas:
            messagebox.showwarning("Aviso", "Activa al menos una carpeta o unidad para respaldar.")
            return
        ip = self.v_ip.get().strip()
        if not solo_analizar:
            try:
                socket.inet_pton(socket.AF_INET, ip)
            except OSError:
                messagebox.showerror("IP no válida", "Escribe la IP de la PC nueva (ej. 169.254.12.34) "
                                                     "o usa «Auto-buscar».")
                return
            if not self.v_serie_v.get().strip() and not messagebox.askyesno(
                    "Falta la serie", "No se detectó la serie de esta PC vieja (se usa en la relación de equipos).\n"
                                      "¿Continuar sin ella?"):
                return
            mons = self.v_monitor_v.get().strip()
            if not messagebox.askyesno(
                    "Confirma las series de la PC vieja",
                    f"Serie CPU: {self.v_serie_v.get().strip() or '—'}\nSerie monitor: {mons or '— (vacía)'}\n\n"
                    "¿Coinciden con las ETIQUETAS físicas? Si no, pulsa «No», corrígelas en los campos y "
                    "vuelve a enviar (la serie leída por Windows a veces no es la de la etiqueta)."):
                return
        self.estado["modo"] = 1
        self.nuevo_job()
        threading.Thread(target=self._hilo_emisor, args=(self.job, ip, rutas, solo_analizar, self.v_cache.get(),
                                                         self.info_envio()), daemon=True).start()

    def _hilo_emisor(self, job, ip, rutas, solo_analizar, excluir_cache, info):
        p = job.prog
        limite = int(self.cfg["limite_gb"]) * 1024 ** 3
        try:
            for nivel, t in self._avisos_previos():
                job.log("⚠ " + t, nivel)
            stage = None
            try:
                imps = (info or {}).get("impresoras") or []
                if imps and not solo_analizar:
                    self.estado_txt("Preparando drivers de impresoras…", "warn")
                    stage = os.path.join(tempfile.gettempdir(), "_DRIVERS_IMPRESORAS")
                    shutil.rmtree(stage, ignore_errors=True)
                    if SYS.empaquetar_drivers(stage, imps, log=lambda t: job.log(t, None)):
                        rutas = list(rutas) + [stage]
            except Exception as e:
                job.log(f"No se pudieron preparar los drivers: {e}", "warn")
            p.fase = "analizando"
            self.estado_txt("Analizando archivos…", "warn")
            job.log("Analizando: " + ", ".join(rutas), None)
            scan = M.escanear(job, rutas, limite, excluir_cache)
            n, b = len(scan["archivos"]), scan["bytes"]
            om, ex = scan["omitidos"], scan["excluidos"]
            est = b / VELOCIDAD_ESTIMADA
            job.log(f"Análisis: {n:,} archivos · {len(scan['carpetas']):,} carpetas · {fmt_bytes(b)} "
                    f"(límite {self.cfg['limite_gb']} GB)", "ok")
            for nombre, tam, cnt in scan["desglose"]:
                job.log(f"   {nombre}: {cnt:,} archivos · {fmt_bytes(tam)}", None)
            job.log(f"Tiempo estimado ≈ {fmt_tiempo(est)} (varía con el disco y la cantidad de archivos pequeños)", "info")
            if sum(ex.values()):
                job.log("Excluidos automáticamente: " + ", ".join(f"{k} {v:,}" for k, v in ex.items() if v), None)
            for motivo, ruta in om[:40]:
                if motivo not in ("enlace simbólico", "unión/enlace de carpeta"):
                    job.log(f"Omitido ({motivo}): {acortar(M.limpio(ruta), 60)}", "warn")
            if len(om) > 40:
                job.log(f"…y {len(om) - 40:,} omitidos más (irán en el reporte).", "warn")
            if n == 0:
                raise ValueError("No hay archivos legibles para enviar.")
            if solo_analizar:
                p.fase, p.completo = "fin", True
                self.estado_txt(f"Listo para enviar: {n:,} archivos · {fmt_bytes(b)} · ≈ {fmt_tiempo(est)}", "ok")
                return
            res = M.enviar_con_reintentos(job, ip, scan, info)
            p.fase, p.completo = "fin", True
            problemas = len(res["fallidos"]) + len(res["ilegibles"]) + res["recibidos_mal"]
            self.estado["enviado"] = True
            self.guardar_estado()
            try:
                ficha, _ = SV.escribir_cierre(os.path.join(L0.raiz_datos(), "Cierres"), info, res)
                marcador = SV.escribir_marcador(ficha)
                job.log(f"Ficha de cierre guardada. NO borrar esta PC antes de {ficha['no_borrar_antes_de'][:10]}.", "info")
                if marcador:
                    job.log(f"Aviso en el escritorio: {marcador}", None)
            except Exception as e:
                job.log(f"No se pudo guardar la ficha de cierre: {e}", "warn")
            self.estado_txt("¡Envío completado!" if not problemas else "Completado con advertencias",
                            "ok" if not problemas else "warn")
            job.log(f"Enviados {res['enviados']:,} (+{res['ya_estaban']:,} que ya estaban) · "
                    f"verificados por el receptor {res['recibidos_ok']:,}.", "ok")
            texto = (f"{res['enviados'] + res['ya_estaban']:,} de {res['total_archivos']:,} archivos ya están "
                     f"en la PC nueva.\nVerificados por el receptor: {res['recibidos_ok']:,}.\n"
                     f"Omitidos en el análisis: {len(res['omitidos']):,}.\n\n"
                     "Ya puedes retirar el cable. NO borres nada de esta PC: se conserva 2 días.")
            if problemas:
                self.ui(messagebox.showwarning, "Envío con advertencias",
                        texto + f"\n\nCon problemas: {problemas:,} (detalle en _REPORTE_MIGRACION.txt de la PC nueva).")
            else:
                self.ui(messagebox.showinfo, "Envío finalizado", texto)
        except M.Cancelado:
            p.fase = "error"
            self.estado_txt("Operación cancelada.", "warn")
            job.log("Cancelado por el usuario.", "warn")
        except M.LimiteExcedido as e:
            p.fase = "error"
            self.estado_txt("Denegado: supera el límite", "err")
            desg = "\n".join(f"   {n}: {fmt_bytes(t)}" for n, t, c in e.desglose)
            job.log(f"DENEGADO: la selección supera {self.cfg['limite_gb']} GB (va en {fmt_bytes(e.total)}).", "err")
            self.ui(messagebox.showwarning, "Selección demasiado grande",
                    f"La selección supera el límite de {self.cfg['limite_gb']} GB (ya lleva {fmt_bytes(e.total)}).\n\n"
                    f"Desglose parcial:\n{desg}\n\nQuita carpetas o divide la migración en partes.")
        except M.Rechazado as e:
            p.fase = "error"
            self.estado_txt("El receptor rechazó la migración", "err")
            job.log(f"Rechazado: {str(e).splitlines()[0]}", "err")
            self.ui(messagebox.showwarning, "Migración rechazada", str(e))
        except Exception as e:
            fase = p.fase
            p.fase = "error"
            if job.cancel.is_set():
                self.estado_txt("Operación cancelada.", "warn")
            elif fase == "conectando":
                self.estado_txt("No se pudo conectar", "err")
                job.log(f"No se pudo conectar con {ip}: {e}", "err")
                self.ui(messagebox.showerror, "No se pudo conectar",
                        f"No hay respuesta de {ip}.\n\nRevisa que el receptor esté ACTIVO en la PC nueva, "
                        "el cable y la IP. Si no, prueba «Cable directo (IP fija)» en las dos PCs.")
            else:
                self.estado_txt("Error en la transferencia", "err")
                job.log(f"Error: {e}", "err")
                self.ui(messagebox.showerror, "Error en la transferencia",
                        f"{e}\n\nPuedes volver a pulsar «Enviar»: se retoma lo que falta.")
        finally:
            job.abortar()
            self.ui(self.set_ocupado, False)
            self.ui(self.actualizar_banner)

    # ==================================================================
    # RELACIÓN (pestaña)
    # ==================================================================
    def refrescar_relacion(self):
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.relacion.filas):
            self.tree.insert("", tk.END, iid=str(i), values=[f[c] for c in COLUMNAS], tags=(f["Estado"],))
        h, i, pn = self.relacion.resumen()
        self.lbl_rel_res.config(text=f"{len(self.relacion.filas)} equipos · {h} hechos · {i} incompletos · {pn} pendientes")
        self.lbl_rel_ruta.config(text=f"Archivo: {self.relacion.xlsx}  (se actualiza solo al guardar)")
        self.actualizar_banner()

    def importar_tabla(self):
        """Suma a la relación actual una tabla de versiones previas (CSV/XLSX). No pisa datos existentes."""
        ruta = filedialog.askopenfilename(title="Tabla o base de una versión anterior",
                                          filetypes=[("Tablas", "*.csv *.xlsx *.xlsm *.txt"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            rep = self.relacion.importar(ruta)
        except Exception as e:
            messagebox.showerror("Importar tabla", str(e))
            return
        txt = texto_informe_importacion(rep)
        self.log(txt.replace("\n", " | "), "ok")
        try:
            self.refrescar_relacion()
        except Exception:
            pass
        messagebox.showinfo("Importación terminada", txt)

    def abrir_carpeta_relacion(self):
        self.abrir_ruta(self.relacion.dir)

    def pegar_pendientes(self):
        win = tk.Toplevel(self.root)
        win.title("Usuarios pendientes")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        self.L(win, "Pega la lista: un usuario por línea (Nombre Apellido, o Nombre<TAB>Apellido).", 9,
               color=C["label2"]).pack(padx=14, pady=(12, 4), anchor="w")
        t = tk.Text(win, width=60, height=16, bg=C["fill"], fg=C["label"], font=self.f(10), relief="flat",
                    insertbackground=C["label"], highlightthickness=0, padx=8, pady=6)
        t.pack(padx=14, pady=4)

        def agregar():
            n = self.relacion.agregar_pendientes(t.get("1.0", tk.END).splitlines())
            win.destroy()
            self.refrescar_relacion()
            messagebox.showinfo("Pendientes", f"{n} usuarios agregados.")
        RoundedButton(win, "Agregar", agregar, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 110).pack(pady=(4, 12))

    def editar_fila(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Editar", "Selecciona una fila.")
            return
        i = int(sel[0])
        fila = self.relacion.filas[i]
        win = tk.Toplevel(self.root)
        win.title("Editar equipo")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        vars_ = {}
        g = tk.Frame(win, bg=C["card"])
        g.pack(padx=14, pady=12)
        for r, c in enumerate(COLUMNAS):
            if c in ("Estado",):
                continue
            self.L(g, c, 9, color=C["label2"]).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            v = tk.StringVar(value=fila[c])
            vars_[c] = v
            self.entrada(g, v, width=40).grid(row=r, column=1, pady=3, ipady=4)

        def guardar():
            for c, v in vars_.items():
                fila[c] = v.get().strip()
            self.relacion.recalcular(fila, fila.get("Notas") if fila.get("Notas") and not fila["Notas"].startswith("Falta") else None)
            self.relacion.guardar()
            win.destroy()
            self.refrescar_relacion()
        fr = tk.Frame(win, bg=C["card"])
        fr.pack(fill="x", padx=14, pady=(0, 12))
        RoundedButton(fr, "Guardar", guardar, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 110).pack(side="right")
        RoundedButton(fr, "Cancelar", win.destroy, C["fill"], C["red"], C["card"], self.f(9, True), 36, 100).pack(side="right", padx=8)

    # ==================================================================
    # Ajustes
    # ==================================================================
    def chk_clonar_impresoras(self):
        imps = (self.estado.get("info_vieja") or {}).get("impresoras") or []
        fr = self.f_fin["impvieja"]
        if not imps:
            self.ui(fr.set, None, "La PC vieja no envió impresoras (conéctala primero).")
            return
        self.ui(fr.set, "run", "Instalando los drivers recibidos…")
        okd, mald = SYS.instalar_drivers(os.path.join(self.destino, "_DRIVERS_IMPRESORAS"), log=lambda t: self.log(t, None))
        if okd or mald:
            self.log(f"Drivers: {len(okd)} instalados" + (f" · fallaron: {', '.join(mald)}" if mald else ""), "ok" if not mald else "warn")
        self.ui(fr.set, "run", f"Clonando {len(imps)} impresoras…")
        hechas, faltan, pred = [], [], None
        for imp in imps:
            if SYS.impresora_existe(imp["nombre"]):
                hechas.append(imp["nombre"])
            else:
                ok, msg = SYS.clonar_impresora(imp)
                (hechas if ok else faltan).append(imp["nombre"] if ok else f"{imp['nombre']} ({msg})")
                self.log(f"Impresora {imp['nombre']}: {msg}", "ok" if ok else "warn")
            if imp.get("predeterminada") and imp["nombre"] in hechas:
                pred = imp["nombre"]
        if pred:
            SYS.predeterminar_impresora(pred)
        self.res["impresoras_clonadas"] = {"ok": hechas, "faltan": faltan, "predeterminada": pred}
        det = f"{len(hechas)}/{len(imps)} listas" + (f" · faltan: {'; '.join(faltan)[:160]}" if faltan else "")
        self.ui(fr.set, "ok" if not faltan else "warn", det)

    def wizard_nuevo(self):
        """Asistente de inicio de un servicio nuevo: todo preconfigurado con lo último usado."""
        win = tk.Toplevel(self.root)
        win.title("Nuevo servicio")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        self.L(win, "Nuevo servicio", 13, True).pack(anchor="w", padx=14, pady=(12, 0))
        self.L(win, "Todo viene con lo último que usaste. Cambia solo lo que sea distinto.", 9,
               color=C["label2"]).pack(anchor="w", padx=14, pady=(0, 6))
        g = tk.Frame(win, bg=C["card"])
        g.pack(padx=14, pady=6)
        pin_ok = SEC.disponible()
        vs = {}
        campos = [("empresa", "Cliente / proyecto", self.cfg["empresa"], None),
                  ("tipo_defecto", "Tipo de equipo", self.cfg["tipo_defecto"], None),
                  ("dominio", "Dominio", self.cfg["dominio"], None),
                  ("usuario_dominio", "Usuario de dominio", self.v_user_dom.get() or self.cfg["usuario_dominio"], None),
                  ("impresora", "Impresora de red (vacío = ninguna)", self.cfg["impresora"] if self.cfg["usar_impresora"] else "", None),
                  ("wifi_ssid", "WiFi (SSID)", self.v_ssid.get(), None),
                  ("wifi_clave", "Clave WiFi", "", "•"),
                  ("pin", "PIN para recordar la clave (opcional)", "", "•")]
        ruta_sec = os.path.join(U.dir_datos(), "secretos.json")
        pin_prev = {}
        for r, (k, t, v0, show) in enumerate(campos):
            if k == "pin" and not pin_ok:
                continue
            self.L(g, t, 9, color=C["label2"]).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            v = tk.StringVar(value=v0)
            vs[k] = v
            self.entrada(g, v, width=34, show=show).grid(row=r, column=1, pady=3, ipady=4)
        if pin_ok and os.path.exists(ruta_sec):
            self.L(win, "Hay una clave WiFi guardada: escribe tu PIN y deja la clave en blanco para usarla.", 8,
                   color=C["label2"]).pack(anchor="w", padx=14)

        def empezar():
            clave, pin = vs["wifi_clave"].get(), vs["pin"].get() if "pin" in vs else ""
            if not clave and pin and pin_ok:
                d = SEC.leer(ruta_sec, pin)
                if d is None:
                    messagebox.showwarning("PIN", "PIN incorrecto o no hay clave guardada.")
                    return
                clave = d.get("wifi_clave", "")
            for k in ("empresa", "tipo_defecto", "dominio", "usuario_dominio"):
                self.cfg[k] = vs[k].get().strip()
            imp = vs["impresora"].get().strip()
            self.cfg["usar_impresora"] = bool(imp)
            if imp:
                self.cfg["impresora"] = imp
            self.cfg["wifi_ssid"] = vs["wifi_ssid"].get().strip()
            self.v_ssid.set(self.cfg["wifi_ssid"])
            self.v_user_dom.set(self.cfg["usuario_dominio"])
            self.v_pass_wifi.set(clave)
            if clave and pin and pin_ok:
                SEC.guardar(ruta_sec, pin, {"wifi_clave": clave})
            U.guardar_config(self.cfg)
            self._regenerar_base()
            self.lbl_dominio.config(text="Dominio: " + (self.cfg["dominio"] or "(VACÍO: abre Ajustes)"))
            win.destroy()
            if self.cfg["wifi_ssid"] and clave:
                self.conectar_wifi()
        fr = tk.Frame(win, bg=C["card"])
        fr.pack(fill="x", padx=14, pady=(6, 12))
        RoundedButton(fr, "Importar tabla…", self.importar_tabla, C["fill"], C["blue"], C["card"], self.f(9, True), 36, 130).pack(side="left")
        RoundedButton(fr, "Empezar", empezar, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 120).pack(side="right")
        RoundedButton(fr, "Más tarde", win.destroy, C["fill"], C["label"], C["card"], self.f(9, True), 36, 110).pack(side="right", padx=8)

    def abrir_ajustes(self):
        win = tk.Toplevel(self.root)
        win.title("Ajustes")
        win.configure(bg=C["card"])
        win.transient(self.root)
        win.grab_set()
        campos = [("unidad", "Unidad administrativa"), ("empresa", "Empresa (M = Mainbit)"),
                  ("perfil", "Perfil (D2 / L2; vacío = detectar)"), ("dominio", "Dominio"),
                  ("impresora", "Impresora de red (\\\\ip\\nombre)"), ("limite_gb", "Límite de migración (GB)"),
                  ("destino_backup", "Carpeta de respaldo (PC nueva)"), ("fuentes_dir", "Carpeta de tipografías"),
                  ("tipo_defecto", "Tipo por defecto en la relación"),
                  ("soporte_contacto", "Contacto de soporte (aparece en el manual del usuario)")]
        g = tk.Frame(win, bg=C["card"])
        g.pack(padx=14, pady=12)
        vs = {}
        for r, (k, t) in enumerate(campos):
            self.L(g, t, 9, color=C["label2"]).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            v = tk.StringVar(value=str(self.cfg.get(k, "")))
            vs[k] = v
            self.entrada(g, v, width=38).grid(row=r, column=1, pady=3, ipady=4)
        vimp = tk.BooleanVar(value=self.cfg["usar_impresora"])
        Switch(win, "Instalar la impresora (si el cliente la pide)", vimp, C["card"], self.f(9), wrap=320).pack(
            fill="x", padx=14, pady=4)

        def guardar():
            for k, v in vs.items():
                val = v.get().strip()
                self.cfg[k] = int(val) if k == "limite_gb" and val.isdigit() else (val if k != "limite_gb" else self.cfg[k])
            self.cfg["usar_impresora"] = vimp.get()
            U.guardar_config(self.cfg)
            self.destino = self.cfg["destino_backup"]
            self.lbl_dest.config(text=self.destino)
            self._regenerar_base()
            self.lbl_dominio.config(text="Dominio: " + (self.cfg["dominio"] or "(VACÍO: abre Ajustes)"))
            win.destroy()
        fr = tk.Frame(win, bg=C["card"])
        fr.pack(fill="x", padx=14, pady=(4, 12))
        RoundedButton(fr, "Guardar", guardar, C["blue"], "#FFFFFF", C["card"], self.f(9, True), 36, 110).pack(side="right")
        RoundedButton(fr, "Cancelar", win.destroy, C["fill"], C["red"], C["card"], self.f(9, True), 36, 100).pack(side="right", padx=8)


def _copiar_a_local(L):
    """Abierto desde la USB: se copia a la PC y se relanza desde ahí (así la USB queda libre para otra PC)."""
    import subprocess
    aviso = threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(
        0, "Copiando Mainbit a esta PC (unos segundos). Cuando se abra, ya puedes sacar la USB.", "Mainbit", 0x40), daemon=True)
    aviso.start()
    exe = L.instalar_local(U.cargar_config())
    try:
        h = ctypes.windll.user32.FindWindowW(None, "Mainbit")
        if h:
            ctypes.windll.user32.PostMessageW(h, 0x10, 0, 0)
    except Exception:
        pass
    if not exe:
        return False
    subprocess.Popen([exe] + [a for a in sys.argv[1:] if a != "--sin-copia"], close_fds=True, cwd=os.path.dirname(exe))
    return True


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        import mb_local as L
        if "--sin-copia" not in sys.argv and L.debe_copiarse(U.cargar_config()):
            if _copiar_a_local(L):
                return
    except Exception:
        pass
    if "--clasico" not in sys.argv:
        try:
            import mb_ui
            if mb_ui.disponible():
                mb_ui.main()
                return
        except Exception as e:      # sin WebView2 u otro problema: se abre la interfaz clásica
            try:
                with open(os.path.join(U.dir_datos(), "arranque_error.txt"), "w", encoding="utf-8") as f:
                    f.write(repr(e))
            except OSError:
                pass
    raiz = tk.Tk()
    app = App(raiz)
    if "--nuevo" in sys.argv:
        raiz.after(700, app.wizard_nuevo)
    raiz.mainloop()


if __name__ == "__main__":
    main()
