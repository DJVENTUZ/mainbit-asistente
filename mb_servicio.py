# -*- coding: utf-8 -*-
"""Inventario de programas, reporte final del servicio y cierre (ficha) de la PC vieja."""
import json
import os
import re
from datetime import datetime, timedelta

import mb_util as U

IS_WIN = os.name == "nt"
DIAS_ESPERA = 2
_RUIDO = ("microsoft visual c++", "microsoft .net", ".net runtime", ".net desktop", ".net host", "windows software development",
          "microsoft edge", "webview2", "update for", "security update", "hotfix", "intel(r)", "intel®", "realtek",
          "nvidia", "amd ", "driver", "lenovo vantage", "lenovo system", "windows driver package", "microsoft update health",
          "vulkan", "directx")


def programas_instalados():
    """Lista [{nombre, version, editor}] desde el registro (equipo + usuario actual). Vacía fuera de Windows."""
    res, vistos = [], set()
    if not IS_WIN:
        return res
    try:
        import winreg
    except ImportError:
        return res
    ramas = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0)]
    for hive, ruta, extra in ramas:
        try:
            raiz = winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | extra)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(raiz)[0]):
            try:
                k = winreg.OpenKey(raiz, winreg.EnumKey(raiz, i))
                def v(n):
                    try:
                        return str(winreg.QueryValueEx(k, n)[0]).strip()
                    except OSError:
                        return ""
                nombre = v("DisplayName")
                if not nombre or v("SystemComponent") == "1" or v("ParentKeyName") or re.match(r"^KB\d+", nombre):
                    continue
                clave = (nombre.lower(), v("DisplayVersion"))
                if clave in vistos:
                    continue
                vistos.add(clave)
                res.append({"nombre": nombre, "version": v("DisplayVersion"), "editor": v("Publisher")})
            except OSError:
                continue
    return sorted(res, key=lambda x: x["nombre"].lower())


def _norm(n):
    n = n.lower()
    n = re.sub(r"\(x64\)|\(x86\)|64-bit|32-bit|\bx64\b|\bx86\b", "", n)
    n = re.sub(r"[\d.]+", "", n)
    return re.sub(r"[^a-z]+", " ", n).strip()


def es_ruido(nombre):
    n = nombre.lower()
    return any(r in n for r in _RUIDO)


def comparar(viejos, nuevos):
    """Devuelve (faltan, ruido_ignorado, ya_estan): programas de la PC vieja que NO están en la nueva."""
    en_nueva = {_norm(p["nombre"]) for p in nuevos}
    faltan, ruido, ya = [], 0, 0
    for p in viejos:
        if _norm(p["nombre"]) in en_nueva:
            ya += 1
        elif es_ruido(p["nombre"]):
            ruido += 1
        else:
            faltan.append(p)
    return faltan, ruido, ya


# ---------------------------------------------------------------- cierre de la PC vieja
def fecha_min_borrado(desde=None):
    return (desde or datetime.now()) + timedelta(days=DIAS_ESPERA)


def escribir_cierre(carpeta, info, res):
    """Guarda la ficha de cierre (json + txt) y devuelve (dict, ruta_json)."""
    os.makedirs(carpeta, exist_ok=True)
    ahora = datetime.now()
    problemas = len(res.get("fallidos", [])) + len(res.get("ilegibles", [])) + res.get("recibidos_mal", 0)
    ficha = {"host": info.get("host", ""), "serie_cpu": info.get("serie", ""),
             "monitores": [m.get("serie", "") for m in info.get("monitores", []) or []],
             "enviado": ahora.isoformat(timespec="seconds"),
             "no_borrar_antes_de": fecha_min_borrado(ahora).isoformat(timespec="seconds"),
             "archivos_total": res.get("total_archivos", 0), "enviados": res.get("enviados", 0),
             "ya_estaban": res.get("ya_estaban", 0), "verificados_receptor": res.get("recibidos_ok", 0),
             "omitidos": len(res.get("omitidos", [])), "problemas": problemas, "liberada": False}
    base = f"cierre_{re.sub(r'[^A-Za-z0-9_-]', '_', ficha['host'] or 'PC')}_{ahora:%Y%m%d_%H%M}"
    ruta = os.path.join(carpeta, base + ".json")
    U.escribir_json(ruta, ficha)
    with open(os.path.join(carpeta, base + ".txt"), "w", encoding="utf-8") as f:
        f.write(texto_ficha(ficha))
    return ficha, ruta


def texto_ficha(f):
    return "\n".join([
        "FICHA DE CIERRE · PC VIEJA", f"Equipo: {f['host']}   Serie CPU: {f['serie_cpu']}",
        f"Monitores (serie leída): {', '.join(x for x in f['monitores'] if x) or '—'}",
        f"Envío completado: {f['enviado'].replace('T', ' ')}",
        f"NO BORRAR ANTES DE: {f['no_borrar_antes_de'].replace('T', ' ')}",
        f"Archivos: {f['archivos_total']:,} · enviados {f['enviados']:,} · ya estaban {f['ya_estaban']:,} · "
        f"verificados por el receptor {f['verificados_receptor']:,} · omitidos {f['omitidos']:,}",
        f"Problemas: {f['problemas']}", "",
        "Este programa NO borra nada. El borrado seguro es manual, tras el plazo y con autorización."])


def escribir_marcador(f):
    """Archivo visible en el escritorio de la PC vieja. Devuelve la ruta (o '' si no pudo)."""
    nombre = f"NO_BORRAR_HASTA_{f['no_borrar_antes_de'][:10]}.txt"
    candidatos = []
    if IS_WIN:
        candidatos.append(os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"))
    candidatos.append(os.path.join(os.path.expanduser("~"), "Desktop"))
    for d in candidatos:
        try:
            if os.path.isdir(d):
                ruta = os.path.join(d, nombre)
                with open(ruta, "w", encoding="utf-8") as fh:
                    fh.write(texto_ficha(f))
                return ruta
        except OSError:
            continue
    return ""


def ultima_ficha(carpeta, host):
    mejor = None
    try:
        for n in os.listdir(carpeta):
            if n.startswith("cierre_") and n.endswith(".json"):
                d = U.leer_json(os.path.join(carpeta, n), None)
                if d and (not host or d.get("host", "").lower() == host.lower()):
                    if not mejor or d["enviado"] > mejor[0]["enviado"]:
                        mejor = (d, os.path.join(carpeta, n))
    except OSError:
        pass
    return mejor


def verificar_liberar(carpeta, host, ahora=None):
    """(ok, mensaje). Marca la ficha como liberada solo si pasó el plazo y no hubo problemas."""
    u = ultima_ficha(carpeta, host)
    if not u:
        return False, f"No hay ficha de cierre para {host}. Haz primero el envío desde esta PC."
    f, ruta = u
    ahora = ahora or datetime.now()
    limite = datetime.fromisoformat(f["no_borrar_antes_de"])
    if ahora < limite:
        falta = limite - ahora
        h = int(falta.total_seconds() // 3600)
        return False, f"Aún NO: faltan {h // 24} d {h % 24} h (hasta {limite:%d/%m/%Y %H:%M})."
    if f["problemas"]:
        return False, f"No liberar: el envío tuvo {f['problemas']} problemas. Revisa el reporte de la PC nueva."
    if f["verificados_receptor"] + f["ya_estaban"] < f["archivos_total"] - f["omitidos"]:
        return False, "No liberar: el receptor no verificó todos los archivos del envío."
    f["liberada"] = True
    f["liberada_el"] = ahora.isoformat(timespec="seconds")
    U.escribir_json(ruta, f)
    return True, (f"LISTA PARA BORRADO (plazo cumplido y envío verificado). "
                  "Falta SOLO la autorización y el borrado seguro, que se hacen aparte.")
