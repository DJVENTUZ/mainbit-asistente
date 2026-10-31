"""Solo para probar sin pantalla (Linux): finge tkinter para poder importar el programa clásico."""
import sys, types


class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return lambda *a, **k: None


def instalar():
    try:
        import tkinter  # noqa
        return False
    except ImportError:
        pass
    m = types.ModuleType("tkinter")
    m.__getattr__ = lambda n: type(n, (_Any,), {})
    for nombre in ("tkinter.font", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog"):
        s = types.ModuleType(nombre)
        s.__getattr__ = lambda n: type(n, (_Any,), {})
        sys.modules[nombre] = s
        setattr(m, nombre.split(".")[1], s)
    sys.modules["tkinter"] = m
    return True
