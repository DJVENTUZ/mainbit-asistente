# -*- coding: utf-8 -*-
"""Núcleo sin interfaz de Mainbit Asistente.

Reutiliza TODA la lógica probada de la clase App (mainbit_asistente.py) pero sin Tk:
los widgets son objetos "fantasma" que solo registran su texto/estado, y la interfaz web
(mb_ui.py + web/index.html) lee ese estado y dispara las acciones."""
import itertools
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime

import mb_motor as M
import mb_sistema as SYS
import mb_util as U
import mb_servicio as SV
import mb_secretos as SEC
import mainbit_asistente as A
from mb_relacion import COLUMNAS, Relacion
from mb_widgets import C, COL

_NIVEL_DE_COLOR = {C["green"]: "ok", C["red"]: "err", C["yellow"]: "warn", C["blue"]: "info"}
VARS_SECRETAS = ("v_pass_dom", "v_pass_wifi")


class Var:
    """Sustituto de tk.StringVar / tk.BooleanVar."""

    def __init__(self, core, valor=""):
        self._c, self._v, self._cb, self.name = core, valor, [], ""

    def get(self):
        return self._v

    def set(self, v):
        if isinstance(self._v, bool):
            v = (str(v).lower() in ("1", "true", "si", "sí", "on")) if not isinstance(v, bool) else v
        elif v is None:
            v = ""
        cambio = v != self._v
        self._v = v
        if cambio:
            self._c.rev += 1
            for cb in list(self._cb):
                try:
                    cb()
                except Exception:
                    pass

    def trace_add(self, modo, cb):
        self._cb.append(cb)


class Wd:
    """Widget fantasma: guarda lo último que la lógica le pidió mostrar."""

    def __init__(self, core, nombre):
        self.__dict__["_c"], self.__dict__["_n"], self.__dict__["d"] = core, nombre, {}

    def config(self, *args, **kw):
        for a in args:
            if isinstance(a, dict):
                kw.update(a)
        if "text" in kw:
            self.d["text"] = str(kw["text"])
        if "fg" in kw:
            self.d["nivel"] = _NIVEL_DE_COLOR.get(kw["fg"])
        self._c.rev += 1

    configure = config

    def set(self, nivel=None, texto=None):
        if texto is None and isinstance(nivel, (int, float)):       # barra de progreso
            self.d["valor"] = nivel
        else:
            self.d["nivel"], self.d["text"] = nivel, "" if texto is None else str(texto)
        self._c.rev += 1

    def get(self, *a):
        return self.d.get("text", "")

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


class Filas(dict):
    def __init__(self, core, prefijo):
        super().__init__()
        self._c, self._p = core, prefijo

    def __missing__(self, k):
        self[k] = Wd(self._c, f"{self._p}.{k}")
        return self[k]


class RaizFalsa:
    def __init__(self, core):
        self.core = core

    def after(self, ms, fn=None, *args):
        if fn:
            threading.Timer(ms / 1000.0, lambda: self.core.q.put((fn, args))).start()

    def destroy(self):
        self.core.pedir_cierre()

    def __getattr__(self, n):
        return lambda *a, **k: None


class CajaMensajes:
    """Sustituye tkinter.messagebox: las preguntas se muestran en la interfaz web."""

    def __init__(self, core):
        self.c = core

    def showinfo(self, t, m, **k):
        self.c.modal("info", t, m, ["Entendido"])

    def showwarning(self, t, m, **k):
        self.c.modal("warn", t, m, ["Entendido"])

    def showerror(self, t, m, **k):
        self.c.modal("err", t, m, ["Entendido"])

    def askyesno(self, t, m, **k):
        return self.c.modal("pregunta", t, m, ["No", "Sí"]) == "Sí"

    def askokcancel(self, t, m, **k):
        return self.c.modal("pregunta", t, m, ["Cancelar", "Aceptar"]) == "Aceptar"


class Archivos:
    def __init__(self, core):
        self.c = core

    def askdirectory(self, title="", **k):
        r = self.c.puente.archivos("carpeta", title)
        return r[0] if r else ""

    def askopenfilename(self, title="", filetypes=None, **k):
        r = self.c.puente.archivos("archivo", title, filetypes)
        return r[0] if r else ""

    def askopenfilenames(self, title="", **k):
        return tuple(self.c.puente.archivos("archivos", title) or [])


class PuenteNulo:
    """Puente por defecto (pruebas): respuestas automáticas, sin ventana."""
    auto = {"pregunta": "Sí"}

    def archivos(self, tipo, titulo, tipos=None):
        return []

    def cerrar(self):
        pass


class Core(A.App):
    def __init__(self, puente=None):                    # no se llama a App.__init__ a propósito
        self.rev = 0
        self.puente = puente or PuenteNulo()
        self.root = RaizFalsa(self)
        self.F = "Segoe UI"
        self.cfg = U.cargar_config()
        self.est_path = os.path.join(U.dir_datos(), "estado.json")
        self.estado = U.leer_json(self.est_path, {}) or {}
        self.relacion = Relacion(U.dir_escribible(U.dir_app()))
        self.q = queue.Queue()
        self.job = None
        self.ocupado = False
        self.tareas = set()
        self.btns_accion = []
        self.info = {}
        self.adaptadores = []
        self.dc_ip = None
        self.res = {}
        self.rutas_extra = []
        self.vars_unidad = {}
        self.unidades_ui = []
        self.avisos_unidades = ""
        self.ips_ui = []
        self.ip_fija_activa = False
        self.fw_abierto = False
        self._vel, self._t_last, self._b_last = 0.0, time.monotonic(), 0
        self.idx_pendiente = None
        self.vigilando_ivanti = False
        self.modo, self.sub = 0, 0
        self.logs, self.log_seq = [], 0
        self.estado_barra = {"texto": "", "nivel": None}
        self.pcts = {}
        self._modales, self._mid, self.modal_activo = {}, itertools.count(1), None
        self.cierre_pedido = False
        self.vivo = True
        self.destino = self.cfg["destino_backup"]
        if not SYS.IS_WIN and not os.path.isdir(os.path.dirname(self.destino) or "."):
            self.destino = os.path.join(os.path.expanduser("~"), "BACKUP_MIGRACION")
        cred = A.leer_credenciales()

        V = lambda x="": Var(self, x)
        self.v_serie, self.v_monitor, self.v_serie_v, self.v_monitor_v = V(), V(), V(), V()
        self.v_base = V(self.cfg["base_hostname"])
        self.v_guion = V(bool(self.cfg["guion"]))
        self.v_user_dom = V(cred.get("usuario", self.cfg["usuario_dominio"]))
        self.v_pass_dom = V(cred.get("clave", ""))
        self.v_ssid = V(cred.get("wifi_ssid", self.cfg["wifi_ssid"]))
        self.v_pass_wifi = V(cred.get("wifi_clave", ""))
        self.v_recordar = V(bool(cred))
        self.v_ip = V()
        self.v_users = V(True)
        self.v_cache = V(bool(self.cfg["excluir_cache"]))
        self.r_nombre, self.r_apellido = V(), V()
        self.r_mon_a, self.r_cpu_a = V(), V()
        self.r_tipo = V(self.cfg.get("tipo_defecto", "D2 Y P03"))
        for k, v in list(vars(self).items()):
            if isinstance(v, Var):
                v.name = k
        for v in (self.v_serie, self.v_base):
            v.trace_add("write", self.actualizar_hostname)
        self.v_guion.trace_add("write", self._regenerar_base)

        self.f_fin = Filas(self, "fin")
        self.vals = Filas(self, "vals")
        # mensajes y diálogos del programa clásico → interfaz web
        A.messagebox = CajaMensajes(self)
        A.filedialog = Archivos(self)
        self.hilo = threading.Thread(target=self._bucle, daemon=True)
        self.hilo.start()
        self.en_hilo("info", self._cargar_info)

    # ---- widgets fantasma bajo demanda
    def __getattr__(self, n):
        if not (n.startswith(("lbl_", "btn_", "lm_", "la_", "f_", "sf_", "fr_", "pag_", "lb_", "ent_", "sw_", "cb_"))
                or n in ("txt", "progress", "seg", "subseg", "tree", "cuerpo")):
            raise AttributeError(n)
        w = Wd(self, n)
        object.__setattr__(self, n, w)
        return w

    # ---- bucle (sustituye al tick de Tk)
    def _bucle(self):
        while self.vivo:
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
            except Exception as e:
                print("bucle:", e)
            time.sleep(0.1)

    def estado_txt(self, texto, nivel=None):
        self.estado_barra = {"texto": texto, "nivel": nivel}
        self.rev += 1

    def _log(self, texto, nivel):
        self.log_seq += 1
        self.logs.append({"n": self.log_seq, "h": f"{datetime.now():%H:%M:%S}", "t": str(texto), "l": nivel})
        if len(self.logs) > 500:
            del self.logs[:100]
        self.rev += 1

    def nuevo_job(self):
        self.job = M.Job(self.log)
        self._vel, self._t_last, self._b_last = 0.0, time.monotonic(), 0
        self.logs.clear()
        self.log_gen = getattr(self, "log_gen", 0) + 1
        self.lbl_pct.config(text="0%")
        self.progress.set(0)
        self.lbl_actual.config(text="")
        for k in ("arch", "datos", "vel", "rest"):
            self.vals[k].config(text="—")
        self.set_ocupado(True)

    def set_ocupado(self, v):
        self.ocupado = v
        SYS.evitar_suspension(v)
        self.rev += 1

    def cancelar(self):
        super().cancelar()

    # ---- modo / sub-paso
    def cambiar_modo(self, i):
        self.modo = i
        self.estado["modo"] = i
        if i == 1:
            self.refrescar_unidades()
        self.actualizar_banner()
        self.rev += 1

    def cambiar_sub(self, i):
        self.sub = i
        if i == 1:
            self.actualizar_hostname()
        if i == 2:
            self.rellenar_registro()
            self.actualizar_faltantes()
        self.rev += 1

    # ---- unidades / IPs / carpetas extra
    def mostrar_ips(self):
        self.ips_ui = [{"etq": e, "ip": ip, "cable": e == "Cable"} for e, ip in SYS.describir_ips(self.adaptadores)]
        self.rev += 1

    def refrescar_unidades(self):
        previas = {k: v.get() for k, v in self.vars_unidad.items()}
        self.vars_unidad, self.unidades_ui, avisos = {}, [], []
        sistema = (os.environ.get("SystemDrive", "C:") or "C:").upper()
        app_drive = os.path.splitdrive(U.dir_app())[0].upper()
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
            hay_d = hay_d or L == "D:"
            var = Var(self, bool(previas.get(L, por_defecto)))
            var.name = "u_" + L
            if esta_app:
                var.set(False)
            self.vars_unidad[L] = var
            det = f"{u['tipo'].upper()} · {U.fmt_bytes(usado)} usados de {U.fmt_bytes(u['total'])}"
            if esta_app:
                det += " · ES LA USB DEL PROGRAMA: no se respalda"
            if sospechosa:
                det += " · parece partición de recuperación"
            self.unidades_ui.append({"letra": L, "titulo": f"Unidad {L} «{u['etiqueta'] or 'sin nombre'}»", "det": det})
        if not hay_d and not SYS.DEMO:
            avisos.append("Este equipo NO tiene unidad D: (revisa si debería tenerla).")
        self.avisos_unidades = "\n".join(avisos)
        self.rev += 1

    def add_carpeta(self):
        d = A.filedialog.askdirectory(title="Selecciona una carpeta de origen")
        if d and d not in self.rutas_extra:
            self.rutas_extra.append(d)
            self.rev += 1

    def add_archivos(self):
        for f in A.filedialog.askopenfilenames(title="Selecciona archivos"):
            if f not in self.rutas_extra:
                self.rutas_extra.append(f)
        self.rev += 1

    def quitar_extra(self, i=None):
        if i is not None and 0 <= int(i) < len(self.rutas_extra):
            del self.rutas_extra[int(i)]
            self.rev += 1

    # ---- diálogos (formularios en la interfaz web)
    def modal(self, tipo, titulo, texto, botones):
        auto = getattr(self.puente, "auto", None)
        if auto is not None:
            return auto.get(tipo, botones[-1])
        mid = next(self._mid)
        ev = threading.Event()
        self._modales[mid] = [ev, botones[-1]]
        self.modal_activo = {"id": mid, "tipo": tipo, "titulo": titulo, "texto": str(texto), "botones": botones}
        self.rev += 1
        ev.wait()
        self.modal_activo = None
        self.rev += 1
        return self._modales.pop(mid)[1]

    def responder(self, mid, valor):
        m = self._modales.get(int(mid))
        if m:
            m[1] = valor
            m[0].set()

    def ajustes_datos(self):
        claves = ["unidad", "empresa", "perfil", "dominio", "impresora", "limite_gb", "destino_backup",
                  "fuentes_dir", "tipo_defecto", "soporte_contacto", "usuario_dominio", "wifi_ssid",
                  "usuario_local_nueva", "fuentes_url"]
        d = {k: str(self.cfg.get(k, "")) for k in claves}
        d["usar_impresora"] = bool(self.cfg["usar_impresora"])
        return d

    def ajustes_guardar(self, d):
        for k, val in (d or {}).items():
            if k == "usar_impresora":
                self.cfg[k] = bool(val)
            elif k == "limite_gb":
                if str(val).isdigit():
                    self.cfg[k] = int(val)
            elif k in U.DEFAULTS:
                self.cfg[k] = str(val).strip()
        U.guardar_config(self.cfg)
        self.destino = self.cfg["destino_backup"]
        self._regenerar_base()
        self.rev += 1
        return True

    def wizard_datos(self):
        d = {"empresa": self.cfg["empresa"], "tipo_defecto": self.cfg["tipo_defecto"],
             "dominio": self.cfg["dominio"], "usuario_dominio": self.v_user_dom.get() or self.cfg["usuario_dominio"],
             "impresora": self.cfg["impresora"] if self.cfg["usar_impresora"] else "",
             "wifi_ssid": self.v_ssid.get(), "base_hostname": self.v_base.get(),
             "pin_guardado": os.path.exists(self._ruta_secretos()), "pin_disponible": SEC.disponible()}
        return d

    def _ruta_secretos(self):
        return os.path.join(U.dir_datos(), "secretos.json")

    def wizard_aplicar(self, d):
        clave, pin = d.get("wifi_clave", ""), d.get("pin", "")
        if not clave and pin and SEC.disponible():
            r = SEC.leer(self._ruta_secretos(), pin)
            if r is None:
                return {"ok": False, "msg": "PIN incorrecto o no hay clave guardada."}
            clave = r.get("wifi_clave", "")
        for k in ("empresa", "tipo_defecto", "dominio", "usuario_dominio"):
            if k in d:
                self.cfg[k] = str(d[k]).strip()
        imp = str(d.get("impresora", "")).strip()
        self.cfg["usar_impresora"] = bool(imp)
        if imp:
            self.cfg["impresora"] = imp
        self.cfg["wifi_ssid"] = str(d.get("wifi_ssid", "")).strip()
        self.v_ssid.set(self.cfg["wifi_ssid"])
        self.v_user_dom.set(self.cfg["usuario_dominio"])
        self.v_pass_wifi.set(clave)
        if clave and pin and SEC.disponible():
            try:
                SEC.guardar(self._ruta_secretos(), pin, {"wifi_clave": clave})
            except ValueError as e:
                return {"ok": False, "msg": str(e)}
        U.guardar_config(self.cfg)
        self._regenerar_base()
        if self.cfg["wifi_ssid"] and clave:
            self.conectar_wifi()
        self.rev += 1
        return {"ok": True}

    def wizard_nuevo(self):
        self.pedir_vista = "nuevo"
        self.rev += 1

    def restaurar_opciones(self):
        raiz = os.path.join(self.destino, "Users")
        o = sorted(d for d in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, d))) if os.path.isdir(raiz) else []
        return {"origenes": o, "destinos": SYS.perfiles_locales(), "ruta": raiz}

    def abrir_restaurar(self):
        op = self.restaurar_opciones()
        if not op["origenes"] or not op["destinos"]:
            A.messagebox.showinfo("Restaurar", "No hay respaldo recibido o el usuario aún no ha iniciado sesión "
                                               "en esta PC (necesita su perfil creado).")
            return
        self.pedir_vista = "restaurar"
        self.rev += 1

    def elegir_pendiente_idx(self, i):
        i = int(i)
        if 0 <= i < len(self.relacion.filas):
            f = self.relacion.filas[i]
            self.idx_pendiente = i
            self.r_nombre.set(f["Usuario Nombre"])
            self.r_apellido.set(f["Apellido"])
            self.actualizar_faltantes()

    def pendientes_lista(self):
        return [{"i": i, "n": f"{f['Usuario Nombre']} {f['Apellido']}"} for i, f in self.relacion.pendientes()]

    def pegar_pendientes_texto(self, texto):
        n = self.relacion.agregar_pendientes((texto or "").splitlines())
        self.rev += 1
        return n

    def editar_fila_datos(self, i):
        return {c: self.relacion.filas[int(i)][c] for c in COLUMNAS if c != "Estado"}

    def editar_fila_guardar(self, i, d):
        fila = self.relacion.filas[int(i)]
        for c in COLUMNAS:
            if c in d and c != "Estado":
                fila[c] = str(d[c]).strip()
        self.relacion.recalcular(fila, fila.get("Notas") if fila.get("Notas") and
                                 not fila["Notas"].startswith("Falta") else None)
        self.relacion.guardar()
        self.rev += 1

    def refrescar_relacion(self):
        self.rev += 1

    def limpiar_vista(self):
        self.pedir_vista = None

    def importar_tabla_ruta(self, ruta):
        if ruta == "__elegir__":
            ruta = A.filedialog.askopenfilename(title="Tabla o base de una versión anterior")
            if not ruta:
                return None
        try:
            rep = self.relacion.importar(ruta)
        except Exception as e:
            A.messagebox.showerror("Importar tabla", str(e))
            return None
        txt = A.texto_informe_importacion(rep)
        self.log(txt.replace("\n", " | "), "ok")
        A.messagebox.showinfo("Importación terminada", txt)
        self.rev += 1
        return rep

    def cerrar(self):
        if self.ocupado:
            if not A.messagebox.askyesno("Cerrar", "Hay una operación en curso. ¿Cancelarla y salir?"):
                return False
            if self.job:
                self.job.abortar()
        self._limpiar()
        self.pedir_cierre()
        return True

    def _limpiar(self):
        try:
            if SYS.ip_fija_pendiente():
                SYS.ip_fija_restaurar()
            if self.fw_abierto:
                SYS.cerrar_firewall()
            SYS.evitar_suspension(False)
            U.escribir_json(self.est_path, self.estado)
        except Exception:
            pass

    def pedir_cierre(self):
        self.cierre_pedido = True
        self.vivo = False
        try:
            self.puente.cerrar()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Módulos de consulta (todo con datos reales)
    # ------------------------------------------------------------------
    def datos_programas(self):
        iv = self.estado.get("info_vieja") or {}
        viejos = iv.get("programas") or []
        nuevos = SV.programas_instalados()
        faltan, ruido, ya = SV.comparar(viejos, nuevos) if viejos else ([], 0, 0)
        return {"hay_vieja": bool(viejos), "viejos": len(viejos), "nuevos": len(nuevos), "ya": ya,
                "faltan": [str(x) for x in faltan][:300]}

    def datos_reportes(self):
        base = U.dir_escribible(U.dir_app())
        out = []
        for carpeta in ("Cierres", "Evidencias", "Reportes", "Diagnosticos"):
            d = os.path.join(base, carpeta)
            if not os.path.isdir(d):
                continue
            for n in sorted(os.listdir(d), reverse=True)[:40]:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    out.append({"carpeta": carpeta, "nombre": n, "ruta": p,
                                "fecha": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
        h, i, pn = self.relacion.resumen()
        return {"archivos": out, "equipos": len(self.relacion.filas), "hechos": h, "incompletos": i,
                "pendientes": pn, "ruta": base}

    def datos_liberar(self):
        carpeta = os.path.join(U.dir_escribible(U.dir_app()), "Cierres")
        try:
            ficha = SV.ultima_ficha(carpeta, SYS.nombre_host())
        except Exception:
            ficha = None
        return {"host": SYS.nombre_host(), "ficha": ficha if isinstance(ficha, (dict, str, type(None))) else str(ficha),
                "enviado": bool(self.estado.get("enviado")), "recibido": bool(self.estado.get("recibido"))}

    def datos_monitores(self):
        return {"monitores": (self.info or {}).get("monitores", []), "serie_txt": self.v_monitor.get(),
                "etiqueta": self.v_monitor_v.get()}

    def informe_soporte(self):
        """Texto para pegar a soporte. NUNCA incluye contraseñas ni archivos del usuario."""
        i = self.info or {}
        l = [f"Mainbit Asistente v{U.VERSION_COMPLETA}", f"Equipo: {SYS.nombre_host()} · {i.get('fabricante','')} "
             f"{i.get('modelo','')} · serie {i.get('serie','')}", f"Windows: {i.get('os','')} · admin: {i.get('admin')}",
             f"Dominio: {i.get('dominio')} · unido: {i.get('unido')}", "", "Últimos registros:"]
        l += [f"{x['h']} [{x['l'] or '·'}] {x['t']}" for x in self.logs[-40:]]
        return "\n".join(l)

    def buscar_actualizacion(self):
        import urllib.request
        try:
            req = urllib.request.Request("https://api.github.com/repos/DJVENTUZ/mainbit-asistente/releases/tags/latest",
                                         headers={"User-Agent": "MainbitAsistente"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
        except Exception as e:
            return {"ok": False, "msg": f"Sin conexión a GitHub: {str(e)[:80]}"}
        sha = (d.get("target_commitish") or "")[:7]
        propio = U.BUILD.split(" ")[0] if U.BUILD != "local" else ""
        nueva = bool(propio) and sha and sha != propio
        return {"ok": True, "sha": sha, "publicada": d.get("published_at", ""), "propio": propio or "local",
                "nueva": nueva if propio else None}

    def actualizar_ahora(self):
        """Descarga la última versión y la aplica al cerrar (no toca datos: el ZIP no los trae)."""
        import mb_descargas as D
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "MainbitUpdate")
        os.makedirs(tmp, exist_ok=True)
        z = os.path.join(tmp, "MainbitAsistente.zip")
        self.estado_txt("Descargando la actualización…", "warn")
        ok, msg = D.descargar("https://github.com/DJVENTUZ/mainbit-asistente/releases/download/latest/MainbitAsistente.zip", z)
        if not ok:
            self.estado_txt(msg, "err")
            return {"ok": False, "msg": msg}
        if not getattr(sys, "frozen", False):
            return {"ok": True, "msg": "Descargada (en modo desarrollo no se aplica)."}
        app = U.dir_app()
        bat = os.path.join(tmp, "aplicar.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write("@echo off\r\ntimeout /t 3 /nobreak >nul\r\n"
                    f'powershell -NoProfile -Command "Expand-Archive -LiteralPath \'{z}\' -DestinationPath \'{tmp}\\x\' -Force"\r\n'
                    f'xcopy /e /y /q "{tmp}\\x\\MainbitAsistente\\*" "{app}\\" >nul\r\n'
                    f'start "" "{app}\\MainbitAsistente.exe"\r\n')
        import subprocess
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000, close_fds=True)
        self.pedir_cierre()
        return {"ok": True, "msg": "Aplicando…"}

    # ---- preferencias de la interfaz (cristal, etc.)
    def _ruta_prefs(self):
        return os.path.join(U.dir_datos(), "interfaz.json")

    def prefs(self):
        if not hasattr(self, "_prefs"):
            self._prefs = U.leer_json(self._ruta_prefs(), {}) or {}
        return self._prefs

    def pref_guardar(self, d):
        self.prefs().update(d or {})
        try:
            U.escribir_json(self._ruta_prefs(), self._prefs)
        except OSError:
            pass
        return True

    def fuentes_desde_internet(self, url=None):
        """Baja un .zip de tipografías (o un .ttf/.otf), lo extrae y las instala para todos los usuarios."""
        import mb_descargas as D
        import tempfile
        import zipfile
        url = (url or self.cfg.get("fuentes_url") or "").strip()
        if not url:
            self.f_fin["fuentes"].set("warn", "Escribe en Ajustes la dirección de descarga de las tipografías.")
            return
        if url != self.cfg.get("fuentes_url"):
            self.cfg["fuentes_url"] = url
            U.guardar_config(self.cfg)
        tmp = tempfile.mkdtemp(prefix="mbfuentes_")
        nombre = os.path.basename(url.split("?")[0]) or "fuentes.zip"
        dest = os.path.join(tmp, nombre)
        self.f_fin["fuentes"].set("run", "Descargando tipografías…")
        ok, msg = D.descargar(url, dest, progreso=lambda a, b: None)
        if not ok:
            self.f_fin["fuentes"].set("err", msg)
            return
        carpeta = os.path.join(tmp, "x")
        os.makedirs(carpeta, exist_ok=True)
        if dest.lower().endswith(".zip"):
            with zipfile.ZipFile(dest) as z:
                z.extractall(carpeta)
        else:
            os.replace(dest, os.path.join(carpeta, nombre))
        self.chk_fuentes(carpeta)

    # ------------------------------------------------------------------
    # Publicación del estado hacia la interfaz
    # ------------------------------------------------------------------
    def estado_publico(self, desde_log=0):
        v = {}
        for k, val in vars(self).items():
            if isinstance(val, Var):
                if k in VARS_SECRETAS:
                    v["tiene_" + k] = bool(val.get())
                else:
                    v[k] = val.get()
        for L, var in self.vars_unidad.items():
            v["u_" + L] = var.get()
        w = {k: dict(o.d) for k, o in vars(self).items() if isinstance(o, Wd) and o.d}
        fin = {k: dict(o.d) for k, o in self.f_fin.items() if o.d}
        vals = {k: o.d.get("text", "") for k, o in self.vals.items()}
        i = self.info or {}
        p = self.job.prog if self.job else None
        h, inc, pn = self.relacion.resumen()
        return {
            "rev": self.rev, "v": v, "w": w, "fin": fin, "vals": vals,
            "ocupado": self.ocupado, "estado": self.estado_barra, "modo": self.modo, "sub": self.sub,
            "fase": p.fase if p else None,
            "pct": self.lbl_pct.d.get("text", "0%"), "actual": self.lbl_actual.d.get("text", ""),
            "info": {k: i.get(k) for k in ("host", "usuario", "serie", "fabricante", "modelo", "os", "admin",
                                           "unido", "dominio", "perfil", "serie_fuente", "monitores")},
            "ips": self.ips_ui, "unidades": self.unidades_ui, "avisos_unidades": self.avisos_unidades,
            "extra": list(self.rutas_extra), "destino": self.destino, "ip_fija": self.ip_fija_activa,
            "cfg": {k: self.cfg.get(k) for k in ("dominio", "impresora", "usar_impresora", "unidad", "empresa",
                                                 "soporte_contacto", "wifi_ssid", "tipo_defecto")},
            "hostname": self.hostname_actual(), "estado_flags": {k: self.estado.get(k) for k in
                                                              ("recibido", "unido", "enviado", "registrado",
                                                               "host_viejo", "restaurado")},
            "vieja": self.estado.get("info_vieja") and {k: self.estado["info_vieja"].get(k) for k in
                      ("host", "usuario", "serie", "fabricante", "modelo", "monitores")} or None,
            "n_prog_vieja": len((self.estado.get("info_vieja") or {}).get("programas") or []),
            "n_imp_vieja": len((self.estado.get("info_vieja") or {}).get("impresoras") or []),
            "impresoras_vieja": (self.estado.get("info_vieja") or {}).get("impresoras") or [],
            "rel": {"filas": self.relacion.filas, "hechos": h, "incompletos": inc, "pendientes": pn,
                    "ruta": self.relacion.xlsx},
            "modal": self.modal_activo, "vista": getattr(self, "pedir_vista", None),
            "arranque": getattr(self, "arranque_info", None), "n_instaladores": len(SYS.listar_instaladores(self.dir_instaladores())),
            "version": U.VERSION_COMPLETA, "es_win": SYS.IS_WIN, "tareas": sorted(self.tareas),
            "logs": [x for x in self.logs if x["n"] > desde_log], "log_seq": self.log_seq,
            "log_gen": getattr(self, "log_gen", 0), "prefs": self.prefs(), "fuentes_url": self.cfg.get("fuentes_url", ""),
        }

    # Acciones que la interfaz puede pedir (lista blanca)
    ACCIONES = {
        "iniciar_receptor", "cancelar", "cambiar_destino", "buscar", "accion_emisor", "toggle_ip_fija",
        "conectar_wifi", "verificar_red", "sincronizar_hora", "actualizar_hostname", "solo_renombrar",
        "unir_dominio", "chk_dominio", "chk_ivanti", "chk_politicas", "accion_fuentes", "chk_impresora",
        "chk_clonar_impresoras", "fuentes_desde_internet", "ejecutar_todo", "abrir_instaladores", "instalar_programas", "abrir_restaurar", "restaurar", "guardar_relacion",
        "generar_evidencia", "diagnostico_monitores", "verificar_liberar", "generar_manual", "terminar_servicio", "terminar_grupo",
        "add_carpeta", "add_archivos", "quitar_extra", "importar_tabla_ruta", "cambiar_modo", "cambiar_sub",
        "elegir_pendiente_idx", "abrir_ruta", "reiniciar_app", "buscar_actualizacion", "actualizar_ahora",
        "rellenar_registro", "actualizar_faltantes", "refrescar_unidades", "limpiar_vista",
    }
    # las que se ejecutan en hilo propio (pueden tardar o bloquear con preguntas)
    LENTAS = {"iniciar_receptor", "accion_emisor", "unir_dominio", "solo_renombrar", "terminar_servicio",
              "guardar_relacion", "terminar_grupo", "restaurar", "importar_tabla_ruta", "cambiar_destino", "add_carpeta",
              "add_archivos", "abrir_restaurar", "diagnostico_monitores", "verificar_liberar", "actualizar_ahora",
              "conectar_wifi", "verificar_red", "ejecutar_todo", "chk_dominio", "chk_ivanti", "chk_politicas",
              "chk_clonar_impresoras", "fuentes_desde_internet", "instalar_programas", "chk_impresora", "accion_fuentes", "generar_evidencia", "generar_manual",
              "toggle_ip_fija", "buscar", "sincronizar_hora", "buscar_actualizacion"}
