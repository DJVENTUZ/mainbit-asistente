import os, sys, time, threading, json, ctypes, platform
import ctypes.wintypes as wt
VAR = sys.argv[1]
if VAR in ("a", "b", "c"):
    os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] = "00FFFFFF"
import webview
RES = {"var": VAR, "win": platform.version()}
HTML = "<html><body style='margin:0;background:transparent'><div style='margin:40px;height:300px;border-radius:30px;background:rgba(255,255,255,.18);color:#fff;font:700 28px Segoe UI;padding:30px'>Cristal</div></body></html>"
class ACCENT(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int), ("Gradient", ctypes.c_uint), ("Anim", ctypes.c_int)]
class WCA(ctypes.Structure):
    _fields_ = [("Attr", ctypes.c_int), ("Data", ctypes.c_void_p), ("Size", ctypes.c_size_t)]
class MARG(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
def accent(hwnd, state, grad):
    a = ACCENT(state, 2, grad, 0)
    d = WCA(19, ctypes.cast(ctypes.pointer(a), ctypes.c_void_p), ctypes.sizeof(a))
    return ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(d))
def trabajo(win):
    time.sleep(5)
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, "spike")
    RES["hwnd"] = bool(hwnd)
    try:
        v = ctypes.c_int(0); ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(v)); RES["dwm"] = v.value
        if VAR in ("b", "c", "d"):
            RES["extend"] = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARG(-1, -1, -1, -1)))
            if VAR == "b":
                RES["backdrop"] = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(3)), 4)
            if VAR == "c":
                RES["accent"] = accent(hwnd, 4, 0x33000000)
            if VAR == "d":
                RES["accent"] = accent(hwnd, 3, 0x00000000)    # blur behind
    except Exception as e:
        RES["err"] = repr(e)
    time.sleep(2)
    from PIL import ImageGrab
    pt = wt.POINT(0, 0); u32.ClientToScreen(hwnd, ctypes.byref(pt))
    for nombre, kw in (("normal", {}), ("capas", {"include_layered_windows": True})):
        im = ImageGrab.grab(**kw)
        RES["margen_" + nombre] = im.getpixel((pt.x + 20, pt.y + 20)); RES["tarjeta_" + nombre] = im.getpixel((pt.x + 200, pt.y + 150))
    RES["escritorio"] = ImageGrab.grab().getpixel((2, 2))
    u32.SetWindowDisplayAffinity(hwnd, 0x11)
    time.sleep(1)
    RES["excluida_margen"] = ImageGrab.grab().getpixel((pt.x + 20, pt.y + 20))
    print("RESULT", json.dumps(RES)); sys.stdout.flush(); os._exit(0)
w = webview.create_window("spike", html=HTML, width=900, height=500, frameless=(VAR == "a"), transparent=(VAR == "a"))
webview.start(trabajo, w)
