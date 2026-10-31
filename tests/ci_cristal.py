# -*- coding: utf-8 -*-
"""Prueba real del cristal en el servidor de Windows: pone una ventana ROJA detrás y mide el color
que se ve dentro de la ventana de la app para cada receta. Solo informa (no rompe la compilación)."""
import ctypes, ctypes.wintypes as wt, os, subprocess, sys, time
from PIL import ImageGrab
import tkinter as tk

exe = sys.argv[1]
ctypes.windll.user32.SetProcessDPIAware()
root = tk.Tk(); root.attributes("-fullscreen", True); root.update()
cv = tk.Canvas(root, highlightthickness=0, bg="#ff0000"); cv.pack(fill="both", expand=True)
for yy in range(0, root.winfo_screenheight(), 12):          # rayas rojo/azul de 6 px: el desenfoque las borra
    cv.create_rectangle(0, yy, root.winfo_screenwidth(), yy + 6, fill="#ff0000", outline="")
    cv.create_rectangle(0, yy + 6, root.winfo_screenwidth(), yy + 12, fill="#0000ff", outline="")
root.update()
u = ctypes.windll.user32
out = []
for rec in sys.argv[2:]:
    nombre = rec
    env = dict(os.environ)
    if "@" in rec:
        ra, modo = rec.split("@")
        env.update(MB_RECETA_A=ra, MB_FORZAR=modo)
    else:
        env["MB_RECETA"] = rec
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
        col = [im.getpixel((x + 4, y - 60 + j))[0] - im.getpixel((x + 4, y - 60 + j))[2] for j in range(0, 120, 2)]
        mu = sum(col) / len(col)
        sd = (sum((v - mu) ** 2 for v in col) / len(col)) ** .5
        out.append(f"{nombre}=rgb{m} contraste={sd:.0f}")
    except Exception as e:
        out.append(f"{rec}=error {e!r}")
    p.kill(); time.sleep(1.5)
print("CRISTAL " + " | ".join(out))
