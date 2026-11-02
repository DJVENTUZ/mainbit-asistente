# -*- coding: utf-8 -*-
"""Ventana con WebView2 hablando DIRECTO con COM por ctypes.

Motivo: equipos con una directiva de Control de aplicaciones (WDAC/AppLocker) bloquean los componentes sin firma de
pythonnet/cffi, y pywebview no puede abrir. Aquí solo se usan: Python estándar (firmado), la WebView2Loader.dll de
Microsoft (firmada) y el WebView2 Runtime de Windows. Es el mismo motor de la interfaz nueva.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading

HRESULT = ctypes.c_long
WM_SIZE, WM_CLOSE, WM_DESTROY, WM_SETFOCUS, WM_ERASEBKGND, WM_GETMINMAXINFO, WM_APP = 0x0005, 0x0010, 0x0002, 0x0007, 0x0014, 0x0024, 0x8000
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)
u32, k32, o32 = None, None, None
if os.name == "nt":
    u32, k32, o32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.ole32
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    u32.CreateWindowExW.restype = wt.HWND
    u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    u32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    k32.GetModuleHandleW.restype = wt.HMODULE
    u32.LoadCursorW.restype = ctypes.c_void_p
    k32.LoadLibraryW.restype = ctypes.c_void_p


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class COLOR(ctypes.Structure):
    _fields_ = [("A", ctypes.c_ubyte), ("R", ctypes.c_ubyte), ("G", ctypes.c_ubyte), ("B", ctypes.c_ubyte)]


def _ptr_at(direccion):
    return ctypes.c_void_p.from_address(direccion).value


def _met(obj, idx, restype, *argtypes):
    """Método número `idx` de la tabla virtual de un objeto COM (obj = dirección entera)."""
    vtbl = _ptr_at(obj)
    fn = _ptr_at(vtbl + idx * ctypes.sizeof(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)


class _Handler:
    """Objeto COM mínimo (IUnknown + Invoke) que llama a una función de Python."""

    def __init__(self, fn):
        QI = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        INV = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

        def qi(this, riid, out):
            out[0] = this
            return 0

        def inv(this, a, b):
            try:
                fn(a, b)
            except Exception:
                pass
            return 0
        self._f = (QI(qi), REF(lambda t: 1), REF(lambda t: 1), INV(inv))
        self._vt = (ctypes.c_void_p * 4)(*[ctypes.cast(f, ctypes.c_void_p) for f in self._f])
        self._obj = (ctypes.c_void_p * 1)(ctypes.addressof(self._vt))
        self.ptr = ctypes.addressof(self._obj)


SHIM = r"""(function(){
 var ids=0,pend={};
 window.__mbres=function(i,v){var f=pend[i];delete pend[i];if(f)f(v)};
 window.pywebview={api:new Proxy({},{get:function(t,n){
   if(n==='then'||typeof n!=='string')return undefined;
   return function(){var a=Array.prototype.slice.call(arguments),i=++ids;
     return new Promise(function(ok){pend[i]=ok;window.chrome.webview.postMessage(JSON.stringify({id:i,m:n,a:a}))})}}})};
 window.dispatchEvent(new Event('pywebviewready'));
})();"""


def buscar_loader():
    """WebView2Loader.dll (x64) junto al programa."""
    cand = []
    for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(sys.executable)),
                 os.path.dirname(os.path.abspath(__file__))):
        if not base:
            continue
        cand += [os.path.join(base, "WebView2Loader.dll"), os.path.join(base, "_internal", "WebView2Loader.dll")]
        for raiz, _, archivos in os.walk(base):
            if "WebView2Loader.dll" in archivos and ("x64" in raiz.lower() or "win-x64" in raiz.lower()):
                cand.append(os.path.join(raiz, "WebView2Loader.dll"))
    for c in cand:
        if os.path.isfile(c):
            return c
    return None


class Ventana:
    def __init__(self, titulo, url, api, ancho=1380, alto=880, datos_dir="", al_cerrar=None):
        if os.name != "nt":
            raise OSError("solo Windows")
        self.titulo, self.url, self.api = titulo, url, api
        self.ancho, self.alto, self.datos = ancho, alto, datos_dir
        self.al_cerrar = al_cerrar
        self.hwnd = None
        self.controller = self.core = self.controller2 = None
        self.cola = queue.Queue()
        self.error = None
        self.listo = threading.Event()
        self._h = []                                  # mantiene vivos los manejadores COM
        self.loader = buscar_loader()
        if not self.loader:
            raise OSError("no se encontró WebView2Loader.dll")
        self._ld = ctypes.WinDLL(self.loader)
        self._ld.CreateCoreWebView2EnvironmentWithOptions.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        self._ld.CreateCoreWebView2EnvironmentWithOptions.restype = HRESULT
        self._zoom = 1.0

    # ------------------------------------------------------------ hilo de la interfaz
    def _ui(self, fn):
        self.cola.put(fn)
        if self.hwnd:
            u32.PostMessageW(self.hwnd, WM_APP + 1, 0, 0)

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_APP + 1:
                while True:
                    try:
                        self.cola.get_nowait()()
                    except queue.Empty:
                        break
                    except Exception:
                        pass
                return 0
            if msg == WM_SIZE:
                self._ajustar()
                return 0
            if msg == WM_SETFOCUS and self.controller:
                _met(self.controller, 12, HRESULT, ctypes.c_int)(self.controller, 0)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_GETMINMAXINFO:
                try:
                    esc = u32.GetDpiForWindow(hwnd) / 96.0
                except Exception:
                    esc = 1.0
                mm = (ctypes.c_long * 10).from_address(lp)
                mm[6], mm[7] = int(900 * esc), int(560 * esc)
                return 0
            if msg == WM_CLOSE:
                try:
                    if self.al_cerrar:
                        self.al_cerrar()
                except Exception:
                    pass
                u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _ajustar(self):
        if not self.controller:
            return
        rc = wt.RECT()
        u32.GetClientRect(self.hwnd, ctypes.byref(rc))
        _met(self.controller, 6, HRESULT, wt.RECT)(self.controller, rc)

    # ------------------------------------------------------------ arranque
    def iniciar(self, al_iniciar=None):
        """Crea la ventana y corre el bucle de mensajes. Devuelve True si se abrió la página, False si no se pudo."""
        o32.CoInitializeEx(None, 2)
        self._wp = WNDPROC(self._wndproc)
        clase = "MainbitWV2"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wp
        wc.hInstance = k32.GetModuleHandleW(None)
        wc.hCursor = u32.LoadCursorW(None, ctypes.c_void_p(32512))
        wc.hbrBackground = None
        wc.lpszClassName = clase
        if not u32.RegisterClassW(ctypes.byref(wc)) and ctypes.GetLastError() != 1410:
            raise OSError("RegisterClass falló")
        try:
            esc = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        except Exception:
            esc = 1.0
        self.hwnd = u32.CreateWindowExW(0, clase, self.titulo, 0x00CF0000 | 0x10000000, -2147483648, -2147483648,
                                        int(self.ancho * esc), int(self.alto * esc), None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError("CreateWindow falló")
        self._al_iniciar = al_iniciar
        os.makedirs(self.datos, exist_ok=True) if self.datos else None
        h_env = _Handler(self._env_listo)
        self._h.append(h_env)
        r = self._ld.CreateCoreWebView2EnvironmentWithOptions(None, self.datos or None, None, h_env.ptr)
        if r != 0:
            u32.DestroyWindow(self.hwnd)
            raise OSError("WebView2 Runtime no disponible (0x%08X)" % (r & 0xFFFFFFFF))
        u32.ShowWindow(self.hwnd, 5)
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        return self.error is None

    def _env_listo(self, hr, env):
        if (hr & 0xFFFFFFFF) != 0 or not env:
            self.error = "entorno WebView2: 0x%08X" % (hr & 0xFFFFFFFF)
            u32.PostQuitMessage(0)
            return
        h = _Handler(self._ctrl_listo)
        self._h.append(h)
        _met(env, 3, HRESULT, wt.HWND, ctypes.c_void_p)(env, self.hwnd, h.ptr)

    def _ctrl_listo(self, hr, ctrl):
        if (hr & 0xFFFFFFFF) != 0 or not ctrl:
            self.error = "controlador WebView2: 0x%08X" % (hr & 0xFFFFFFFF)
            u32.PostQuitMessage(0)
            return
        self.controller = ctrl
        _met(ctrl, 1, ctypes.c_ulong)(ctrl)                           # AddRef: se conserva
        core = ctypes.c_void_p()
        _met(ctrl, 25, HRESULT, ctypes.POINTER(ctypes.c_void_p))(ctrl, ctypes.byref(core))
        self.core = core.value
        # fondo transparente (Controller2.put_DefaultBackgroundColor)
        try:
            iid = (ctypes.c_ubyte * 16)(*bytes.fromhex("3e9079c9cad4284292eb47ee3fa96eab"))
            c2 = ctypes.c_void_p()
            if _met(ctrl, 0, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(ctrl, ctypes.addressof(iid), ctypes.byref(c2)) == 0:
                self.controller2 = c2.value
                _met(c2.value, 27, HRESULT, COLOR)(c2.value, COLOR(0, 0, 0, 0))
        except Exception:
            pass
        self._ajustar()
        _met(ctrl, 4, HRESULT, ctypes.c_int)(ctrl, 1)                 # IsVisible
        # puente JS -> Python
        h_js = _Handler(lambda a, b: None)
        self._h.append(h_js)
        _met(self.core, 27, HRESULT, wt.LPCWSTR, ctypes.c_void_p)(self.core, SHIM, h_js.ptr)
        h_msg = _Handler(self._mensaje)
        self._h.append(h_msg)
        tok = ctypes.c_longlong()
        _met(self.core, 34, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong))(self.core, h_msg.ptr, ctypes.byref(tok))
        _met(self.core, 5, HRESULT, wt.LPCWSTR)(self.core, self.url)
        self.listo.set()
        if self._al_iniciar:
            threading.Thread(target=self._al_iniciar, daemon=True).start()
        u32.SetFocus(self.hwnd)

    # ------------------------------------------------------------ JS <-> Python
    def _mensaje(self, sender, args):
        s = ctypes.c_void_p()
        if _met(args, 5, HRESULT, ctypes.POINTER(ctypes.c_void_p))(args, ctypes.byref(s)) != 0 or not s.value:
            return
        texto = ctypes.wstring_at(s.value)
        o32.CoTaskMemFree(ctypes.c_void_p(s.value))
        d = json.loads(texto)
        threading.Thread(target=self._llamar, args=(d.get("id"), d.get("m"), d.get("a") or []), daemon=True).start()

    def _llamar(self, i, nombre, args):
        try:
            if not nombre or nombre.startswith("_"):
                raise AttributeError(nombre)
            r = getattr(self.api, nombre)(*args)
        except Exception:
            r = None
        try:
            self.ejecutar("window.__mbres(%s,%s)" % (int(i), json.dumps(r, default=str)))
        except Exception:
            pass

    def ejecutar(self, js, esperar=False, timeout=15):
        """Ejecuta JavaScript en la página (desde cualquier hilo). Con esperar=True devuelve el resultado."""
        res = {}
        ev = threading.Event()

        def hacer():
            def fin(hr, texto):
                try:
                    res["v"] = ctypes.wstring_at(texto) if texto else "null"
                finally:
                    ev.set()
            h = _Handler(fin)
            self._h.append(h)
            if len(self._h) > 400:
                del self._h[60:200]
            if not self.core or _met(self.core, 29, HRESULT, wt.LPCWSTR, ctypes.c_void_p)(self.core, js, h.ptr) != 0:
                ev.set()
        self._ui(hacer)
        if not esperar:
            return None
        ev.wait(timeout)
        try:
            return json.loads(res.get("v", "null"))
        except Exception:
            return None

    def evaluate_js(self, js):
        return self.ejecutar(js, esperar=True)

    def set_zoom(self, z):
        def hacer():
            if self.controller:
                _met(self.controller, 8, HRESULT, ctypes.c_double)(self.controller, float(z))
        self._ui(hacer)

    def transparente(self):
        return self.controller2 is not None

    def destroy(self):
        if self.hwnd:
            u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
