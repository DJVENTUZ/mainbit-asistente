# -*- coding: utf-8 -*-
"""Interfaz Mainbit Toolkit: ventana web (WebView2) + puente hacia el núcleo (mb_core.Core).

El «cristal» se dibuja con el fondo de escritorio REAL de cada PC (se lee su imagen y se alinea
con la ventana), así funciona igual en cualquier Windows sin depender de la transparencia del sistema."""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

import mb_util as U

TITULO = "Mainbit Toolkit"


def dir_web():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "web")


# ----------------------------------------------------------------------
# Fondo de escritorio
# ----------------------------------------------------------------------
def ruta_fondo():
    if os.name != "nt":
        return ""
    buf = ctypes.create_unicode_buffer(520)
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buf, 0)      # SPI_GETDESKWALLPAPER
    except Exception:
        return ""
    r = buf.value
    if r and os.path.isfile(r):
        return r
    alt = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Themes", "TranscodedWallpaper")
    return alt if os.path.isfile(alt) else ""


def fondo_data_url(max_mb=14):
    r = ruta_fondo()
    if not r or os.path.getsize(r) > max_mb * 1024 * 1024:
        return ""
    ext = os.path.splitext(r)[1].lower()
    mime = {"png": "image/png", ".png": "image/png", ".bmp": "image/bmp"}.get(ext, "image/jpeg")
    with open(r, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


# ----------------------------------------------------------------------
# Puente que ve JavaScript
# ----------------------------------------------------------------------
class Api:
    def __init__(self, core):
        self._core = core
        self._ventana = None

    # estado
    def poll(self, rev, log_seq):
        c = self._core
        if rev == c.rev and not c.modal_activo:
            return None
        return c.estado_publico(int(log_seq))

    def set_var(self, nombre, valor):
        v = getattr(self._core, nombre, None)
        from mb_core import Var
        if isinstance(v, Var):
            v.set(valor)
        elif nombre.startswith("u_") and nombre[2:] in self._core.vars_unidad:
            self._core.vars_unidad[nombre[2:]].set(valor)
        return True

    def act(self, nombre, args=None):
        c = self._core
        if nombre not in c.ACCIONES:
            return False
        args = args or []
        fn = getattr(c, nombre)
        if nombre in c.LENTAS:
            threading.Thread(target=lambda: self._seguro(fn, args), daemon=True).start()
            return True
        return self._seguro(fn, args)

    def _seguro(self, fn, args):
        try:
            return fn(*args)
        except Exception as e:
            self._core.log(f"Error: {e}", "err")
            return None

    def datos(self, nombre, args=None):
        if nombre not in DATOS:
            return None
        try:
            return getattr(self._core, nombre)(*(args or []))
        except Exception as e:
            return {"error": str(e)}

    def responder(self, mid, valor):
        self._core.responder(mid, valor)
        return True

    def fondo(self):
        return fondo_data_url()

    def arranque(self):
        return "nuevo" if "--nuevo" in sys.argv else ""

    def copiar(self, texto):
        try:
            import subprocess
            subprocess.run(["clip"], input=texto.encode("utf-16le"), check=False, creationflags=0x08000000)
            return True
        except Exception:
            return False

    def cerrar_ventana(self):
        threading.Thread(target=self._core.cerrar, daemon=True).start()


DATOS = {"ajustes_datos", "ajustes_guardar", "wizard_datos", "wizard_aplicar", "restaurar_opciones",
         "pendientes_lista", "pegar_pendientes_texto", "editar_fila_datos", "editar_fila_guardar",
         "datos_programas", "datos_reportes", "datos_liberar", "datos_monitores", "informe_soporte",
         "pref_guardar", "elegir_pendiente_idx", "restaurar", "buscar_actualizacion"}


class Puente:
    """Lo que el núcleo necesita de la ventana (diálogos de archivo, cerrar)."""
    auto = None

    def __init__(self):
        self.ventana = None

    def archivos(self, tipo, titulo, tipos=None):
        import webview
        if not self.ventana:
            return []
        try:
            FD = getattr(webview, "FileDialog", None)
            modo = {"carpeta": getattr(FD, "FOLDER", None) if FD else getattr(webview, "FOLDER_DIALOG", 20),
                    "archivo": getattr(FD, "OPEN", None) if FD else getattr(webview, "OPEN_DIALOG", 10),
                    "archivos": getattr(FD, "OPEN", None) if FD else getattr(webview, "OPEN_DIALOG", 10)}[tipo]
            ft = ("Tablas (*.csv;*.xlsx;*.xlsm;*.txt)", "Todos (*.*)") if tipo == "archivo" else ()
            r = self.ventana.create_file_dialog(modo, allow_multiple=(tipo == "archivos"), file_types=ft)
            return list(r) if r else []
        except Exception:
            return []

    def cerrar(self):
        try:
            if self.ventana:
                self.ventana.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Posición de la ventana → JS (para alinear el fondo)
# ----------------------------------------------------------------------
def _hilo_posicion(ventana, titulo):
    if os.name != "nt":
        return
    u32 = ctypes.windll.user32
    ultimo = None
    while True:
        time.sleep(0.05)
        try:
            hwnd = u32.FindWindowW(None, titulo)
            if not hwnd:
                continue
            pt = wt.POINT(0, 0)
            u32.ClientToScreen(hwnd, ctypes.byref(pt))
            mon = u32.MonitorFromWindow(hwnd, 2)

            class MI(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("flags", wt.DWORD)]
            mi = MI()
            mi.cb = ctypes.sizeof(MI)
            u32.GetMonitorInfoW(mon, ctypes.byref(mi))
            m = mi.rcMonitor
            pos = (pt.x - m.left, pt.y - m.top, m.right - m.left, m.bottom - m.top)
            if pos != ultimo:
                ultimo = pos
                ventana.evaluate_js(f"window.mbPos&&mbPos({pos[0]},{pos[1]},{pos[2]},{pos[3]})")
        except Exception:
            time.sleep(0.5)


def _titulo_oscuro(titulo):
    """Barra de título oscura (y Mica en Windows 11) para que combine con el cristal."""
    if os.name != "nt":
        return
    for _ in range(60):
        hwnd = ctypes.windll.user32.FindWindowW(None, titulo)
        if hwnd:
            try:
                v = ctypes.c_int(1)
                for attr in (20, 19):
                    if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4) == 0:
                        break
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(2)), 4)
            except Exception:
                pass
            return
        time.sleep(0.1)


def _prueba(w, core):
    """Autoprueba para la compilación automática: la interfaz carga, recibe datos reales y responde."""
    res = {"ok": False}
    try:
        for _ in range(80):
            time.sleep(0.5)
            v = w.evaluate_js("(typeof S!=='undefined'&&S&&S.version)||''")
            if v:
                break
        res["version"] = v
        res["glass"] = w.evaluate_js("document.querySelectorAll('.glass').length")
        res["host"] = w.evaluate_js("(S.info&&S.info.host)||''")
        res["modulos"] = w.evaluate_js("document.querySelectorAll('#mods .mod').length")
        w.evaluate_js("setMod(1);showModule(1)")
        time.sleep(2.5)
        res["titulo"] = w.evaluate_js("document.querySelector('#ttl h1').textContent")
        res["ok"] = bool(v) and res["glass"] >= 3 and res["host"] and res["modulos"] == 10 and res["titulo"] == "Nuevo servicio"
    except Exception as e:
        res["error"] = repr(e)
    try:
        with open(os.path.join(U.dir_app(), "uiprobe.txt"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    except OSError:
        pass
    os._exit(0 if res["ok"] else 1)


def disponible():
    """¿Se puede abrir la interfaz nueva en este equipo?"""
    if os.name != "nt":
        return False
    try:
        import webview  # noqa
    except Exception:
        return False
    return os.path.isfile(os.path.join(dir_web(), "index.html"))


def main():
    import webview
    import mb_core
    puente = Puente()
    core = mb_core.Core(puente)
    api = Api(core)
    titulo = f"{TITULO}"
    w = webview.create_window(titulo, url=os.path.join(dir_web(), "index.html"), js_api=api,
                              width=1380, height=880, min_size=(1100, 700), background_color="#0b1020")
    puente.ventana = w
    api._ventana = w

    def al_iniciar():
        threading.Thread(target=_titulo_oscuro, args=(titulo,), daemon=True).start()
        threading.Thread(target=_hilo_posicion, args=(w, titulo), daemon=True).start()
        if "--prueba-ui" in sys.argv:
            threading.Thread(target=_prueba, args=(w, core), daemon=True).start()

    w.events.closing += lambda: (core._limpiar(), True)[1]
    webview.start(al_iniciar, gui="edgechromium", private_mode=False, storage_path=os.path.join(U.dir_datos(), "webview"))
    core._limpiar()
    core.vivo = False
