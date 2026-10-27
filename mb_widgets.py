# -*- coding: utf-8 -*-
"""Componentes visuales estilo iOS (modo oscuro) para Tkinter."""
import tkinter as tk

C = {
    "bg": "#000000", "card": "#1C1C1E", "fill": "#2C2C2E", "gray": "#3A3A3C",
    "label": "#FFFFFF", "label2": "#8E8E93", "label3": "#636366",
    "blue": "#0A84FF", "green": "#30D158", "red": "#FF453A", "yellow": "#FFD60A",
    "orange": "#FF9F0A", "disabled": "#2C2C2E",
}
NIVEL = {"ok": C["green"], "warn": C["yellow"], "err": C["red"], "info": C["blue"],
         "run": C["blue"], None: C["label3"]}
COL = {"ok": C["green"], "warn": C["yellow"], "err": C["red"], "info": C["blue"], None: C["label2"]}

ESC = 1.0       # factor de escala (DPI); lo fija la app


def S(n):
    return int(n * ESC)


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
           x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
           x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def oscurecer(hex_color, f):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, int(c * f)) for c in (r, g, b))


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, bg, fg="#FFFFFF", fondo_padre=None,
                 font=None, height=40, width=100, radius=13):
        super().__init__(parent, height=S(height), width=S(width), bg=fondo_padre or C["card"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command, self.color, self.fg = text, command, bg, fg
        self.font, self.radius, self.enabled, self._actual = font, S(radius), True, bg
        self.bind("<Configure>", lambda e: self._dibujar())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _dibujar(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fill = self._actual if self.enabled else C["disabled"]
        round_rect(self, 1, 1, w - 1, h - 1, self.radius, fill=fill, outline="")
        self.create_text(w // 2, h // 2, text=self.text, font=self.font,
                         fill=self.fg if self.enabled else C["label3"], width=max(10, w - 12))

    def _press(self, _e):
        if self.enabled:
            self._actual = oscurecer(self.color, 0.75)
            self._dibujar()

    def _release(self, e):
        if not self.enabled:
            return
        self._actual = self.color
        self._dibujar()
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def set_enabled(self, valor):
        self.enabled = valor
        self.configure(cursor="hand2" if valor else "arrow")
        self._dibujar()

    def set_text(self, texto):
        self.text = texto
        self._dibujar()


class Card(tk.Canvas):
    """Tarjeta redondeada (lista agrupada de iOS). El contenido va en .inner"""

    def __init__(self, parent, fondo_padre, fill=None, radius=18, pad=14, width=380):
        super().__init__(parent, bg=fondo_padre, highlightthickness=0, bd=0, width=S(width), height=10)
        self.fill, self.radius, self.pad = fill or C["card"], S(radius), S(pad)
        self.inner = tk.Frame(self, bg=self.fill)
        self.win = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._relayout)
        self.inner.bind("<Configure>", self._relayout)

    def _relayout(self, _e=None):
        w = self.winfo_width()
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if int(float(self.cget("height"))) != h:
            self.config(height=h)
        self.delete("bg")
        round_rect(self, 0, 0, w, h, self.radius, fill=self.fill, outline="", tags="bg")
        self.tag_lower("bg")
        self.itemconfigure(self.win, width=max(1, w - 2 * self.pad))


class Capsula(tk.Canvas):
    def __init__(self, parent, fondo_padre):
        super().__init__(parent, height=S(8), bg=fondo_padre, highlightthickness=0, bd=0)
        self.valor = 0
        self.bind("<Configure>", lambda e: self._dibujar())

    def set(self, v):
        v = max(0, min(100, v))
        if v != self.valor:
            self.valor = v
            self._dibujar()

    def _dibujar(self):
        self.delete("all")
        w, h = self.winfo_width(), S(8)
        round_rect(self, 0, 0, w, h, h // 2, fill=C["fill"], outline="")
        if self.valor > 0:
            round_rect(self, 0, 0, max(h, int(w * self.valor / 100)), h, h // 2, fill=C["blue"], outline="")


class Segmentado(tk.Canvas):
    def __init__(self, parent, opciones, comando, fondo_padre, font, width=300, height=34, seleccion=0):
        super().__init__(parent, width=S(width), height=S(height), bg=fondo_padre,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.opciones, self.comando, self.font = opciones, comando, font
        self.sel, self.activo = seleccion, True
        self.bind("<Configure>", lambda e: self._dibujar())
        self.bind("<Button-1>", self._click)

    def _dibujar(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        n = len(self.opciones)
        sw = w / n
        round_rect(self, 0, 0, w, h, S(10), fill=C["fill"], outline="")
        round_rect(self, self.sel * sw + 2, 2, self.sel * sw + sw - 2, h - 2, S(8), fill=C["gray"], outline="")
        for i, t in enumerate(self.opciones):
            self.create_text(sw * i + sw / 2, h / 2, text=t, font=self.font,
                             fill=C["label"] if (self.activo or i == self.sel) else C["label3"])

    def _click(self, e):
        if not self.activo:
            return
        i = max(0, min(len(self.opciones) - 1, int(e.x // (self.winfo_width() / len(self.opciones)))))
        if i != self.sel:
            self.seleccionar(i)
            self.comando(i)

    def seleccionar(self, i):
        self.sel = i
        self._dibujar()


class Switch(tk.Frame):
    """Interruptor estilo iOS con texto."""

    def __init__(self, parent, texto, variable, fondo, font, comando=None, detalle="", wrap=300):
        super().__init__(parent, bg=fondo)
        self.var, self.comando, self.fondo = variable, comando, fondo
        col = tk.Frame(self, bg=fondo)
        col.pack(side="left", fill="x", expand=True)
        self.lbl = tk.Label(col, text=texto, font=font, bg=fondo, fg=C["label"], anchor="w",
                            justify="left", wraplength=S(wrap))
        self.lbl.pack(fill="x")
        self.det = tk.Label(col, text=detalle, font=(font[0], max(7, font[1] - 1)), bg=fondo, fg=C["label2"],
                            anchor="w", justify="left", wraplength=S(wrap))
        if detalle:
            self.det.pack(fill="x")
        self.cv = tk.Canvas(self, width=S(46), height=S(28), bg=fondo, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(side="right", padx=(S(8), 0))
        for w in (self.cv, self.lbl, self.det):
            w.bind("<Button-1>", self._click)
        self.var.trace_add("write", lambda *a: self._dibujar())
        self._dibujar()

    def _dibujar(self):
        self.cv.delete("all")
        on = bool(self.var.get())
        w, h = S(46), S(28)
        round_rect(self.cv, 1, 2, w - 1, h - 2, (h - 4) // 2, fill=C["green"] if on else C["gray"], outline="")
        x = w - S(14) - 3 if on else S(14) + 1
        self.cv.create_oval(x - S(11), h // 2 - S(11), x + S(11), h // 2 + S(11), fill="#FFFFFF", outline="")

    def _click(self, _e=None):
        self.var.set(not self.var.get())
        if self.comando:
            self.comando()

    def set_texto(self, texto, detalle=None):
        self.lbl.config(text=texto)
        if detalle is not None:
            self.det.config(text=detalle)
            if detalle and not self.det.winfo_ismapped():
                self.det.pack(fill="x")


class FilaEstado(tk.Frame):
    """Fila con punto de color (estado), título, detalle y un botón opcional."""

    def __init__(self, parent, titulo, fondo, font, boton=None, comando=None, wrap=250):
        super().__init__(parent, bg=fondo)
        self.fondo, self.font = fondo, font
        self.dot = tk.Canvas(self, width=S(16), height=S(16), bg=fondo, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(0, S(10)), anchor="n", pady=(S(3), 0))
        col = tk.Frame(self, bg=fondo)
        col.pack(side="left", fill="x", expand=True)
        self.tit = tk.Label(col, text=titulo, font=font, bg=fondo, fg=C["label"], anchor="w", justify="left")
        self.tit.pack(fill="x")
        self.det = tk.Label(col, text="", font=(font[0], max(7, font[1] - 1)), bg=fondo, fg=C["label2"],
                            anchor="w", justify="left", wraplength=S(wrap))
        self.btn = None
        if boton:
            self.btn = RoundedButton(self, boton, comando, C["fill"], C["blue"], fondo,
                                     (font[0], max(8, font[1] - 1), "bold"), height=30, width=84, radius=10)
            self.btn.pack(side="right", padx=(S(8), 0))
        self.nivel = None
        self._pintar()

    def _pintar(self):
        self.dot.delete("all")
        self.dot.create_oval(S(2), S(2), S(14), S(14), fill=NIVEL.get(self.nivel, C["label3"]), outline="")

    def set(self, nivel, detalle=""):
        self.nivel = nivel
        self._pintar()
        self.det.config(text=detalle, fg=COL.get(nivel, C["label2"]) if nivel in ("err", "warn") else C["label2"])
        if detalle:
            self.det.pack(fill="x")
        else:
            self.det.pack_forget()


class ScrollFrame(tk.Frame):
    """Contenedor con desplazamiento por rueda del mouse e indicador fino tipo iOS."""

    def __init__(self, parent, fondo):
        super().__init__(parent, bg=fondo)
        self.fondo = fondo
        self.canvas = tk.Canvas(self, bg=fondo, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=fondo)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._actualizar)
        self.canvas.bind("<Configure>", self._ancho)
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.inner.bind("<Enter>", self._enter)

    def _ancho(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)
        self._actualizar()

    def _actualizar(self, _e=None):
        self.canvas.configure(scrollregion=(0, 0, 1, max(self.inner.winfo_reqheight(), 1)))
        self._indicador()

    def _indicador(self):
        self.canvas.delete("ind")
        total = max(self.inner.winfo_reqheight(), 1)
        vis = self.canvas.winfo_height()
        if vis <= 1 or total <= vis + 2:
            return
        y0 = self.canvas.canvasy(0)
        w = self.canvas.winfo_width()
        x = w - S(5)
        top = y0 + vis * (y0 / total)
        alto = max(S(30), vis * vis / total)
        round_rect(self.canvas, x, top + 2, x + S(3), top + alto, S(2), fill=C["gray"], outline="", tags="ind")

    def _rueda(self, e):
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        d = -1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1
        self.canvas.yview_scroll(d * 3, "units")
        self._indicador()

    def _enter(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._rueda)
        self.canvas.bind_all("<Button-4>", self._rueda)
        self.canvas.bind_all("<Button-5>", self._rueda)

    def _leave(self, _e=None):
        # si el puntero sigue sobre un hijo, Enter lo vuelve a activar
        x, y = self.winfo_pointerxy()
        w = self.winfo_containing(x, y)
        while w is not None and w is not self:
            w = w.master
        if w is None:
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(ev)

    def arriba(self):
        self.canvas.yview_moveto(0)
        self._indicador()
