# -*- coding: utf-8 -*-
"""Utilidades comunes de Mainbit Asistente."""
import json
import os
import sys
import tempfile

APP_NAME = "Mainbit Asistente"
VERSION = "3.0"
IS_WIN = os.name == "nt"

DEFAULTS = {
    "unidad": "124",              # Unidad administrativa (parte 1 del hostname)
    "empresa": "M",               # Empresa Mainbit (parte 2)
    "tipo_defecto": "D2 Y P03",
    "perfil": "",                 # D2 / L2; vacío = detectar por tipo de chasis
    "guion": False,               # False: 124MD243H47   True: 124-M-D2-43H47
    "dominio": "",
    "usuario_dominio": "",
    "impresora": "",
    "usar_impresora": True,
    "limite_gb": 500,
    "excluir_cache": True,
    "fuentes_dir": "",
    "fuentes_noto": 72,
    "fuentes_patria": 3,
    "destino_backup": r"C:\BACKUP_MIGRACION",
    "ivanti_dir": r"C:\Program Files (x86)\Ivanti",
    "ivanti_items": 24,
    "usuario_local_nueva": "",
    "base_hostname": "",          # última base usada (ej. 124MD2) para no reescribirla en cada PC
    "wifi_ssid": "",
}


def dir_app():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def dir_datos():
    """Carpeta para estado que debe sobrevivir a reinicios (ProgramData)."""
    if IS_WIN:
        p = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "MainbitAsistente")
    else:
        p = os.path.join(os.path.expanduser("~"), ".mainbit_asistente")
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".w")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return p
    except OSError:
        p = os.path.join(tempfile.gettempdir(), "MainbitAsistente")
        os.makedirs(p, exist_ok=True)
        return p


def dir_escribible(preferida):
    """Devuelve `preferida` si se puede escribir; si no, Documentos\\Mainbit."""
    try:
        os.makedirs(preferida, exist_ok=True)
        probe = os.path.join(preferida, ".w_mainbit")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return preferida
    except OSError:
        alt = os.path.join(os.path.expanduser("~"), "Documents", "Mainbit")
        os.makedirs(alt, exist_ok=True)
        return alt


def leer_json(path, defecto=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def escribir_json(path, datos):
    """Escritura atómica: nunca deja un archivo a medias si se corta la luz/USB."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def cargar_config():
    cfg = dict(DEFAULTS)
    guardada = leer_json(os.path.join(dir_app(), "config_mainbit.json"), {}) or {}
    for k, v in guardada.items():
        if k in DEFAULTS:
            cfg[k] = v
    return cfg


def guardar_config(cfg):
    try:
        escribir_json(os.path.join(dir_app(), "config_mainbit.json"),
                      {k: cfg[k] for k in DEFAULTS if cfg.get(k) != DEFAULTS[k]})
    except OSError:
        pass


# ---------------------------------------------------------------- formato
def fmt_bytes(n):
    n = float(max(n, 0))
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_tiempo(s):
    if s is None or s != s or s == float("inf"):
        return "—"
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def acortar(texto, n=56):
    if len(texto) <= n:
        return texto
    return texto[: n // 2 - 1] + "…" + texto[-(n // 2):]


# ---------------------------------------------------------------- rutas
def lp(path):
    """Prefijo \\\\?\\ en Windows para rutas de más de 260 caracteres."""
    if not IS_WIN or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def limpio(path):
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def dentro(a, b):
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def ruta_segura(base, rel):
    """Impide que el emisor escriba fuera de la carpeta destino (.., C:, rutas absolutas)."""
    rel = rel.replace("\\", "/")
    partes = rel.split("/")
    for p in partes:
        if p in ("", ".", "..") or ":" in p or p != p.strip(" ") and False:
            raise ValueError(f"Ruta no permitida: {rel}")
    dest = os.path.normpath(os.path.join(base, *partes))
    if not dentro(os.path.normcase(dest), os.path.normcase(os.path.normpath(base))):
        raise ValueError(f"Ruta no permitida: {rel}")
    return dest


def ultimos5(serie):
    s = "".join(c for c in (serie or "").upper() if c.isalnum())
    return s[-5:]
