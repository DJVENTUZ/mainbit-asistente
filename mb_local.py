# -*- coding: utf-8 -*-
"""Trabajo sin USB: la app se copia a la PC (ProgramData), se puede sacar la USB, y al volver a insertarla se
SINCRONIZA: la relación se fusiona en los dos sentidos (sin pisar datos) y los reportes se copian sin perder nada."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

import mb_util as U

DATOS_DIRS = ("Reportes", "Evidencias", "Cierres", "Servicios", "Diagnosticos", "Instaladores", "Tipografias", "Respaldos")
DATOS_ARCHIVOS = ("config_mainbit.json", "credenciales.ini", "usb_origen.json")


def dir_local():
    base = os.environ.get("ProgramData") or os.path.expanduser("~")
    return os.path.join(base, "MainbitToolkit", "App")


def _tipo_unidad(ruta):
    if os.name != "nt":
        return 0
    try:
        raiz = os.path.splitdrive(os.path.abspath(ruta))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(raiz)
    except Exception:
        return 0


def etiqueta_volumen(ruta):
    if os.name != "nt":
        return ""
    try:
        raiz = os.path.splitdrive(os.path.abspath(ruta))[0] + "\\"
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(raiz, buf, 261, None, None, None, None, 0):
            return buf.value
    except Exception:
        pass
    return ""


def en_usb(ruta, etiqueta="USB Blue"):
    """¿La carpeta está en una memoria USB (unidad extraíble o con la etiqueta de la USB)?"""
    return _tipo_unidad(ruta) == 2 or (bool(etiqueta) and etiqueta_volumen(ruta).lower() == etiqueta.lower())


def es_local(ruta=None):
    ruta = os.path.abspath(ruta or U.dir_app()).lower()
    return ruta.startswith(os.path.abspath(dir_local()).lower())


def debe_copiarse(cfg):
    return bool(getattr(sys, "frozen", False) and cfg.get("modo_local", True) and not es_local()
                and en_usb(U.dir_app(), cfg.get("usb_etiqueta", "USB Blue"))
                and not os.path.exists(os.path.join(U.dir_app(), "modo_vieja.flag")))


def _robocopy(origen, destino, extra):
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    r = subprocess.run(["robocopy", origen, destino, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"] + extra,
                       capture_output=True, **kw)
    return r.returncode < 8


def instalar_local(cfg):
    """Copia el programa a la PC y deja anotado de qué USB vino. Devuelve la ruta del .exe local o None."""
    src, dst = U.dir_app(), dir_local()
    os.makedirs(dst, exist_ok=True)
    primera = not os.path.exists(os.path.join(dst, "usb_origen.json"))
    xd = [os.path.join(src, d) for d in DATOS_DIRS if not primera] + [os.path.join(src, "BACKUP_MIGRACION")]
    xf = [] if primera else list(DATOS_ARCHIVOS) + ["RELACION_EQUIPOS.csv", "RELACION_EQUIPOS.xlsx", "estado_mainbit.json"]
    extra = ["/XO"] + (["/XD"] + xd if xd else []) + (["/XF"] + xf if xf else [])
    if not _robocopy(src, dst, extra):
        return None
    U.escribir_json(os.path.join(dst, "usb_origen.json"),
                    {"origen": src, "etiqueta": etiqueta_volumen(src) or cfg.get("usb_etiqueta", "USB Blue"),
                     "fecha": time.strftime("%Y-%m-%d %H:%M:%S")})
    exe = os.path.join(dst, os.path.basename(sys.executable))
    if not primera:                                   # trae también lo nuevo de la USB (relación, instaladores…)
        try:
            sincronizar(dst, src, solo_hacia_local=True)
        except Exception:
            pass
    return exe if os.path.isfile(exe) else None


def origen():
    return U.leer_json(os.path.join(U.dir_app(), "usb_origen.json"), None)


def buscar_usb():
    """Carpeta MainbitAsistente de la USB de origen si está conectada (aunque cambie de letra), o None."""
    o = origen()
    if not o:
        return None
    ruta = o.get("origen", "")
    if ruta and os.path.isdir(ruta) and not es_local(ruta) and etiqueta_volumen(ruta).lower() == (o.get("etiqueta") or "").lower():
        return ruta
    if os.name != "nt":
        return None
    nombre = os.path.basename(ruta.rstrip("\\/")) or "MainbitAsistente"
    for letra in "DEFGHIJKLMNOPQRSTUVWXYZ":
        cand = f"{letra}:\\{nombre}"
        if os.path.isdir(cand) and etiqueta_volumen(cand).lower() == (o.get("etiqueta") or "").lower():
            return cand
    return None


# ---------------------------------------------------------------- sincronización
def _copiar_sin_perder(origen_dir, destino_dir, host):
    """Copia archivos nuevos; si ya existe uno con el mismo nombre pero distinto contenido, conserva los dos."""
    n = 0
    if not os.path.isdir(origen_dir):
        return 0
    for raiz, _, archivos in os.walk(origen_dir):
        rel = os.path.relpath(raiz, origen_dir)
        for a in archivos:
            s = os.path.join(raiz, a)
            d_dir = os.path.normpath(os.path.join(destino_dir, rel))
            os.makedirs(d_dir, exist_ok=True)
            d = os.path.join(d_dir, a)
            if os.path.exists(d):
                if os.path.getsize(d) == os.path.getsize(s):
                    continue
                base, ext = os.path.splitext(a)
                d = os.path.join(d_dir, f"{base}_{host}{ext}")
                if os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s):
                    continue
            shutil.copy2(s, d)
            n += 1
    return n


def sincronizar(local, usb, solo_hacia_local=False, host=None, log=None):
    """Fusiona local ⇄ USB sin pisar nada. Devuelve un resumen."""
    from mb_relacion import Relacion
    host = host or os.environ.get("COMPUTERNAME", "PC")
    res = {"relacion_usb": 0, "relacion_local": 0, "archivos": 0, "config": 0}
    r_loc, r_usb = Relacion(local), Relacion(usb)
    if not solo_hacia_local and r_loc.filas:
        rep = r_usb.importar(r_loc.csv)
        res["relacion_usb"] = rep["nuevas"] + rep["completadas"]
        r_usb.guardar()
    if r_usb.filas:
        rep = r_loc.importar(r_usb.csv)
        res["relacion_local"] = rep["nuevas"] + rep["completadas"]
        r_loc.guardar()
    for d in ("Reportes", "Evidencias", "Cierres", "Servicios", "Diagnosticos"):
        if not solo_hacia_local:
            res["archivos"] += _copiar_sin_perder(os.path.join(local, d), os.path.join(usb, d), host)
    for d in ("Instaladores", "Tipografias"):
        res["archivos"] += _copiar_sin_perder(os.path.join(usb, d), os.path.join(local, d), host)
        if not solo_hacia_local:
            res["archivos"] += _copiar_sin_perder(os.path.join(local, d), os.path.join(usb, d), host)
    # configuración: solo se rellena lo que esté vacío o por defecto en el otro lado
    c_loc = U.leer_json(os.path.join(local, "config_mainbit.json"), {}) or {}
    c_usb = U.leer_json(os.path.join(usb, "config_mainbit.json"), {}) or {}
    for origen_c, destino_c, ruta in ((c_loc, c_usb, os.path.join(usb, "config_mainbit.json")),
                                      (c_usb, c_loc, os.path.join(local, "config_mainbit.json"))):
        if solo_hacia_local and ruta.startswith(usb):
            continue
        cambio = False
        for k in U.CLAVES_COMPARTIDAS:
            v = origen_c.get(k)
            if v not in (None, "") and destino_c.get(k) in (None, "", U.DEFAULTS.get(k)) and v != U.DEFAULTS.get(k):
                destino_c[k] = v
                cambio = True
                res["config"] += 1
        if cambio or (not os.path.exists(ruta) and destino_c):
            U.escribir_json(ruta, destino_c)
    if log:
        log(f"Sincronizado con la USB: {res}")
    return res
