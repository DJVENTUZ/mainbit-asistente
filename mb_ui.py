# -*- coding: utf-8 -*-
"""Interfaz Mainbit Toolkit: ventana web (WebView2) + puente hacia el núcleo (mb_core.Core).

El «cristal» es transparencia REAL de Windows (se ve lo que hay detrás de la ventana)."""
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

    def vidrio(self):
        LISTO_VIDRIO.wait(6)
        return {k: v for k, v in ESTADO_VIDRIO.items()}

    def cristal(self, alpha):
        ESTADO_VIDRIO["alpha"] = max(1, min(250, int(alpha)))
        return True

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
# Cristal REAL: la ventana es transparente de verdad (Windows pone detrás tu escritorio y tus ventanas)
# ----------------------------------------------------------------------
GC, GR = 96, 58
ESTADO_VIDRIO = {"ok": False, "alpha": 8, "modo": "ninguno", "fondo": ""}
LISTO_VIDRIO = threading.Event()


class _ACCENT(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int), ("Gradient", ctypes.c_uint), ("Anim", ctypes.c_int)]


class _WCA(ctypes.Structure):
    _fields_ = [("Attr", ctypes.c_int), ("Data", ctypes.c_void_p), ("Size", ctypes.c_size_t)]


class _MARG(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def aplicar_vidrio(hwnd, alpha):
    """Fondo transparente real. Windows 11 (22H2+): material acrílico del sistema; Windows 10/11 anterior: acrílico
    por composición. Devuelve True si el sistema lo aceptó (el color que se ve detrás lo pone Windows)."""
    try:
        v = ctypes.c_int(3)                                   # DWMSBT_TRANSIENTWINDOW = acrílico
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(v), 4) == 0:
            ESTADO_VIDRIO["modo"] = "win11"
            return True
    except Exception:
        pass
    try:
        a = _ACCENT(4, 2, (int(alpha) << 24) | (64 << 16) | (32 << 8) | 20, 0)
        d = _WCA(19, ctypes.cast(ctypes.pointer(a), ctypes.c_void_p), ctypes.sizeof(a))
        if ctypes.windll.user32.SetWindowCompositionAttribute(wt.HWND(hwnd), ctypes.byref(d)):
            ESTADO_VIDRIO["modo"] = "acrilico"
            return True
    except Exception:
        pass
    return False


def _webview_transparente(ventana):
    """pywebview pinta el WebView2 con color sólido; aquí se cambia a transparente para ver el vidrio de Windows."""
    try:
        import System.Drawing as D
        from System import Action
        form = ventana.native

        def buscar(c, prof=0):
            if hasattr(c, "DefaultBackgroundColor"):
                return c
            if prof > 6:
                return None
            try:
                hijos = list(c.Controls)
            except Exception:
                hijos = []
            for h in hijos:
                r = buscar(h, prof + 1)
                if r is not None:
                    return r
            return None

        def poner():
            wv = buscar(form)
            if wv is None:
                for nom in ("web_view", "browser"):
                    o = getattr(getattr(form, "browser", form), nom, None)
                    if o is not None and hasattr(o, "DefaultBackgroundColor"):
                        wv = o
                        break
            if wv is None:
                ESTADO_VIDRIO["fondo"] = "sin control"
                return
            wv.DefaultBackgroundColor = D.Color.Transparent
            try:
                form.BackColor = D.Color.Black
            except Exception:
                pass
            ESTADO_VIDRIO["fondo"] = "transparente"

        form.Invoke(Action(poner))
    except Exception as e:
        ESTADO_VIDRIO["fondo"] = "error: " + repr(e)[:120]


def ajustar_ventana(hwnd, ancho_dis=1380, alto_dis=880):
    """Tamaño inicial: el del diseño, pero nunca más grande que el área útil de la pantalla (sin tapar la barra de tareas)."""
    try:
        u32 = ctypes.windll.user32
        rc = wt.RECT()
        u32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)            # SPI_GETWORKAREA
        W, H = rc.right - rc.left, rc.bottom - rc.top
        try:
            esc = u32.GetDpiForWindow(wt.HWND(hwnd)) / 96.0
        except Exception:
            esc = 1.0
        w = int(min(ancho_dis * esc, W * 0.94))
        h = int(min(alto_dis * esc, H * 0.94))
        x = rc.left + (W - w) // 2
        y = rc.top + (H - h) // 2
        u32.SetWindowPos(wt.HWND(hwnd), 0, x, y, w, h, 0x0004 | 0x0010)    # NOZORDER | NOACTIVATE
    except Exception:
        pass


def preparar_ventana(hwnd):
    try:
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARG(-1, -1, -1, -1)))
        v = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4) == 0:
                break
    except Exception:
        pass


def capturar_detras(hwnd):
    """Luminancia (96x58) de lo que hay DETRÁS de la ventana. La ventana se excluye de la captura solo
    durante unos milisegundos, así no afecta a quien esté compartiendo pantalla."""
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    pt = wt.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    rc = wt.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 50 or h < 50:
        return None
    if not u32.SetWindowDisplayAffinity(hwnd, 0x11):          # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+)
        return None
    try:
        time.sleep(0.03)
        hdc = u32.GetDC(0)
        mem = g32.CreateCompatibleDC(hdc)
        bmp = g32.CreateCompatibleBitmap(hdc, GC, GR)
        g32.SelectObject(mem, bmp)
        g32.SetStretchBltMode(mem, 4)                           # HALFTONE
        g32.StretchBlt(mem, 0, 0, GC, GR, hdc, pt.x, pt.y, w, h, 0x00CC0020)
        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("x", wt.LONG), ("y", wt.LONG), ("c", wt.DWORD), ("i", wt.DWORD)]
        bmi = BMI(ctypes.sizeof(BMI), GC, -GR, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(GC * GR * 4)
        g32.GetDIBits(mem, bmp, 0, GR, buf, ctypes.byref(bmi), 0)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(0, hdc)
    finally:
        u32.SetWindowDisplayAffinity(hwnd, 0)
    d = buf.raw
    lin = lambda v: (v / 255.0) / 12.92 if v <= 10.3 else (((v / 255.0) + 0.055) / 1.055) ** 2.4
    tabla = [lin(i) for i in range(256)]
    out = []
    for k in range(0, GC * GR * 4, 4):
        out.append(round(0.2126 * tabla[d[k + 2]] + 0.7152 * tabla[d[k + 1]] + 0.0722 * tabla[d[k]], 3))
    prom = sum(out) / len(out)
    if prom < 0.004 and max(out) < 0.01:                       # captura negra = no sirvió: no confiar en ella
        return None
    return out


def _hilo_cristal(ventana, titulo):
    if os.name != "nt":
        return
    u32 = ctypes.windll.user32
    hwnd = 0
    for _ in range(100):
        hwnd = u32.FindWindowW(None, titulo)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        LISTO_VIDRIO.set()
        return
    ajustar_ventana(hwnd)
    _webview_transparente(ventana)
    preparar_ventana(hwnd)
    ESTADO_VIDRIO["ok"] = aplicar_vidrio(hwnd, ESTADO_VIDRIO["alpha"])
    LISTO_VIDRIO.set()
    ultimo, aplicado = None, ESTADO_VIDRIO["alpha"]
    while True:
        time.sleep(0.7)
        try:
            if ESTADO_VIDRIO["alpha"] != aplicado:
                aplicado = ESTADO_VIDRIO["alpha"]
                if ESTADO_VIDRIO["modo"] == "acrilico":
                    aplicar_vidrio(hwnd, aplicado)
            g = capturar_detras(hwnd)
            if g and (ultimo is None or sum(abs(a - b) for a, b in zip(g, ultimo)) / len(g) > 0.004):
                ultimo = g
                ventana.evaluate_js("window.mbGrid&&mbGrid(" + json.dumps(g) + ")")
        except Exception:
            time.sleep(1.5)


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
        res["vidrio"] = ESTADO_VIDRIO["ok"]
        res["vidrio_detalle"] = dict(ESTADO_VIDRIO)
        res["grid"] = w.evaluate_js("typeof GRID!=='undefined'&&GRID.length")
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
    os.environ.setdefault("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "00000000")   # el contenido web no pinta fondo propio
    puente = Puente()
    core = mb_core.Core(puente)
    api = Api(core)
    titulo = f"{TITULO}"
    w = webview.create_window(titulo, url=os.path.join(dir_web(), "index.html"), js_api=api,
                              width=1380, height=880, min_size=(900, 560), background_color="#000000")
    puente.ventana = w
    api._ventana = w

    def al_iniciar():
        threading.Thread(target=_hilo_cristal, args=(w, titulo), daemon=True).start()
        if "--prueba-ui" in sys.argv:
            threading.Thread(target=_prueba, args=(w, core), daemon=True).start()

    w.events.closing += lambda: (core._limpiar(), True)[1]
    webview.start(al_iniciar, gui="edgechromium", private_mode=False, storage_path=os.path.join(U.dir_datos(), "webview"))
    core._limpiar()
    core.vivo = False
