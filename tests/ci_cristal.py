# -*- coding: utf-8 -*-
"""Prueba real del cristal en el servidor de Windows: pone una ventana ROJA detrás y mide el color
que se ve dentro de la ventana de la app para cada receta. Solo informa (no rompe la compilación)."""
import ctypes, ctypes.wintypes as wt, os, subprocess, sys, time
from PIL import ImageGrab
import tkinter as tk

exe = sys.argv[1]
ctypes.windll.user32.SetProcessDPIAware()
root = tk.Tk(); root.attributes("-fullscreen", True); root.configure(bg="#ff0000"); root.update()
u = ctypes.windll.user32
out = []
for rec in sys.argv[2:]:
    env = dict(os.environ, MB_RECETA=rec)
    p = subprocess.Popen([exe], env=env)
    hwnd = 0
    t0 = time.time()
    while time.time() - t0 < 40 and not hwnd:
        root.update(); time.sleep(0.3); hwnd = u.FindWindowW(None, "Mainbit Toolkit")
    t0 = time.time()
    while time.time() - t0 < 14:
        root.update(); time.sleep(0.2)
    try:
        pt = wt.POINT(0, 0); u.ClientToScreen(hwnd, ctypes.byref(pt))
        rc = wt.RECT(); u.GetClientRect(hwnd, ctypes.byref(rc))
        x, y = pt.x + 4, pt.y + rc.bottom // 2
        im = ImageGrab.grab(all_screens=True).convert("RGB")
        px = [im.getpixel((x + i, y + j)) for i in range(0, 8) for j in range(0, 40, 5)]
        m = tuple(sum(c[k] for c in px) // len(px) for k in range(3))
        out.append(f"{rec}={m}")
    except Exception as e:
        out.append(f"{rec}=error {e!r}")
    p.kill(); time.sleep(1.5)
print("CRISTAL " + " | ".join(out))
