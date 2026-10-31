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

    def cristal(self, alpha, blur=0, solido=False):
        if FORZAR:
            return True
        ESTADO_VIDRIO["alpha"] = max(1, min(250, int(alpha)))
        ESTADO_VIDRIO["blur"] = float(blur)
        ESTADO_VIDRIO["solido"] = bool(solido)
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
         "pref_guardar", "datos_instaladores", "importar_config_v3", "add_instaladores", "quitar_instalador", "elegir_pendiente_idx", "restaurar", "buscar_actualizacion"}


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
FORZAR = os.environ.get("MB_FORZAR", "")           # solo pruebas: "acrilico" fija el estado y la interfaz no lo cambia      # solo para pruebas del cristal
ESTADO_VIDRIO = {"ok": False, "alpha": 8, "modo": "ninguno", "fondo": "", "blur": 0.0, "solido": False}
if FORZAR == "acrilico":
    ESTADO_VIDRIO.update({"alpha": 150, "blur": 30.0})
LISTO_VIDRIO = threading.Event()


class _ACCENT(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int), ("Gradient", ctypes.c_uint), ("Anim", ctypes.c_int)]


class _WCA(ctypes.Structure):
    _fields_ = [("Attr", ctypes.c_int), ("Data", ctypes.c_void_p), ("Size", ctypes.c_size_t)]


class _MARG(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def _backdrop(hwnd, tipo):
    try:
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(tipo)), 4) == 0
    except Exception:
        return False


def _margenes(hwnd, v):
    try:
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARG(v, v, v, v)))
    except Exception:
        pass


def _accent(hwnd, estado, color):
    try:
        ac = _ACCENT(estado, 2, color, 0)
        d = _WCA(19, ctypes.cast(ctypes.pointer(ac), ctypes.c_void_p), ctypes.sizeof(ac))
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(wt.HWND(hwnd), ctypes.byref(d)))
    except Exception:
        return False


def aplicar_vidrio(hwnd, alpha, blur=0.0, solido=False):
    """Dos estados de cristal real:
    · Cristal claro (poco desenfoque): transparencia pura, se ve el escritorio tal cual.
    · Sólido / con desenfoque: acrílico de Windows (borroso); en Windows 10 se usa el acrílico por composición."""
    if solido or blur >= 6:
        _accent(hwnd, 0, 0)
        if _backdrop(hwnd, 3):                                # DWMSBT_TRANSIENTWINDOW = acrílico de Windows 11
            _margenes(hwnd, -1)
            ESTADO_VIDRIO["modo"] = "acrilico"
            return True
        tinte = (max(1, min(200, int(alpha))) << 24) | (64 << 16) | (32 << 8) | 20
        ok = _accent(hwnd, 4, tinte)                          # Windows 10: acrílico por composición
        ESTADO_VIDRIO["modo"] = "acrilico10"
        return ok
    _backdrop(hwnd, 0)
    _margenes(hwnd, 0)
    ok = _accent(hwnd, 2, 1 << 24)                            # transparencia pura
    ESTADO_VIDRIO["modo"] = "claro"
    return ok


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
    ESTADO_VIDRIO["ok"] = aplicar_vidrio(hwnd, ESTADO_VIDRIO["alpha"], ESTADO_VIDRIO["blur"], ESTADO_VIDRIO["solido"])
    LISTO_VIDRIO.set()
    ultimo, aplicado = None, None
    while True:
        time.sleep(0.7)
        try:
            cfg = (ESTADO_VIDRIO["alpha"], round(ESTADO_VIDRIO["blur"] / 4), ESTADO_VIDRIO["solido"])
            if cfg != aplicado:
                aplicado = cfg
                aplicar_vidrio(hwnd, ESTADO_VIDRIO["alpha"], ESTADO_VIDRIO["blur"], ESTADO_VIDRIO["solido"])
            g = capturar_detras(hwnd)
            if g and (ultimo is None or sum(abs(a - b) for a, b in zip(g, ultimo)) / len(g) > 0.004):
                ultimo = g
                ventana.evaluate_js("window.mbGrid&&mbGrid(" + json.dumps(g) + ")")
        except Exception:
            time.sleep(1.5)


def _prueba(w, core):
    """Autoprueba para la compilación automática: la interfaz carga, recibe datos reales y responde."""
    res = {"ok": False}
    t_ini = time.time()
    try:
        for _ in range(80):
            time.sleep(0.5)
            v = w.evaluate_js("(typeof S!=='undefined'&&S&&S.version)||''")
            if v:
                break
        res["version"] = v
        res["seg_hasta_datos"] = round(time.time() - t_ini, 1)
        res["glass"] = w.evaluate_js("document.querySelectorAll('.glass').length")
        res["host"] = w.evaluate_js("(S.info&&S.info.host)||''")
        res["modulos"] = w.evaluate_js("document.querySelectorAll('#mods .mod').length")
        w.evaluate_js("setMod(1);showModule(1)")
        time.sleep(2.5)
        res["titulo"] = w.evaluate_js("document.querySelector('#ttl h1').textContent")
        w.evaluate_js("window.__f=0;(function t(){__f++;requestAnimationFrame(t)})();0")
        time.sleep(3)
        res["fps"] = round((w.evaluate_js("window.__f") or 0) / 3, 1)
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
