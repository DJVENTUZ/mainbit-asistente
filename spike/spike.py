import os, sys, time, threading, json, ctypes
VAR=sys.argv[1] if len(sys.argv)>1 else "a"
if VAR in ("a","c"): os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] = "00FFFFFF"
import webview
RES = {}
class Api:
    def ping(self, x):
        RES["ping"] = x
        return "pong " + str(x)
HTML = """<html><body style='margin:0;background:transparent'>
<div style='margin:40px;height:300px;border-radius:30px;background:rgba(255,255,255,.18);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.5);color:#fff;font:700 28px Segoe UI;padding:30px'>Cristal prueba<div id=o></div></div>
<script>
window.addEventListener('pywebviewready',async()=>{document.getElementById('o').textContent=await pywebview.api.ping(7)});
</script></body></html>"""
def acrylic(hwnd):
    class ACCENT(ctypes.Structure):
        _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int), ("Gradient", ctypes.c_uint), ("Anim", ctypes.c_int)]
    class WCA(ctypes.Structure):
        _fields_ = [("Attr", ctypes.c_int), ("Data", ctypes.c_void_p), ("Size", ctypes.c_size_t)]
    a = ACCENT(4, 2, 0x33FFFFFF, 0)   # acrylic blur-behind
    d = WCA(19, ctypes.cast(ctypes.pointer(a), ctypes.c_void_p), ctypes.sizeof(a))
    return ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(d))
def trabajo(win):
    time.sleep(5)
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, "spike")
        RES["hwnd"] = hwnd
        RES["acrylic"] = acrylic(hwnd) if hwnd else None
    except Exception as e:
        RES["acrylic_err"] = repr(e)
    time.sleep(2)
    try:
        from PIL import ImageGrab
        import ctypes.wintypes as wt
        r = wt.RECT(); ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        im = ImageGrab.grab(); im.save("spike_%s.png" % VAR); RES["rect"] = [r.left, r.top, r.right, r.bottom]
        RES["margen"] = im.getpixel((r.left + 20, r.top + 20)); RES["tarjeta"] = im.getpixel((r.left + 200, r.top + 150)); RES["escritorio"] = im.getpixel((2, 2))
    except Exception as e:
        RES["shot_err"] = repr(e)
    try:
        RES["texto"] = win.evaluate_js("document.getElementById('o').textContent")
    except Exception as e:
        RES["js_err"] = repr(e)
    print("RESULT", VAR, json.dumps(RES, default=str)); sys.stdout.flush()
    os._exit(0)
w = webview.create_window("spike", html=HTML, js_api=Api(), width=900, height=500, frameless=True, transparent=(VAR in ("a","b")), easy_drag=True)
webview.start(trabajo, w)
