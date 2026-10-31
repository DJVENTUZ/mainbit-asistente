import os, sys, time, threading, json, ctypes
os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] = "00FFFFFF"
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
        im = ImageGrab.grab(); im.save("spike.png"); RES["shot"] = im.size
        RES["px"] = im.getpixel((200, 200)); RES["px_bg"] = im.getpixel((5, 5))
    except Exception as e:
        RES["shot_err"] = repr(e)
    try:
        RES["texto"] = win.evaluate_js("document.getElementById('o').textContent")
    except Exception as e:
        RES["js_err"] = repr(e)
    print("RESULT", json.dumps(RES, default=str)); sys.stdout.flush()
    os._exit(0)
w = webview.create_window("spike", html=HTML, js_api=Api(), width=900, height=500, frameless=True, transparent=True, easy_drag=True)
webview.start(trabajo, w)
