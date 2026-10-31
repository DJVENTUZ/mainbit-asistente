"""Manual para el usuario: explica, en lenguaje sencillo, dónde quedó cada cosa en su equipo nuevo.
Se genera en la PC nueva con datos reales del servicio (nada personal vive en el código)."""
import os, html
from datetime import datetime
import mb_util as U

NOMBRES = {"Desktop": "Escritorio", "Documents": "Documentos", "Downloads": "Descargas", "Pictures": "Imágenes",
           "Videos": "Videos", "Music": "Música", "Favorites": "Favoritos"}
TOPE_ENTRADAS = 300000


def medir_carpeta(ruta):
    """(archivos, bytes) de una carpeta, sin seguir enlaces y con tope para no tardar de más."""
    arch = tam = n = 0
    pila = [ruta]
    while pila and n < TOPE_ENTRADAS:
        d = pila.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    n += 1
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pila.append(e.path)
                        else:
                            arch += 1
                            tam += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return arch, tam


def inventario_respaldo(destino, carpetas_perfil=None):
    """Lo que realmente quedó en la carpeta de respaldo: {'usuarios': [...], 'unidad_d': ruta|''}."""
    carpetas_perfil = carpetas_perfil or list(NOMBRES)
    res = {"usuarios": [], "unidad_d": ""}
    raiz = os.path.join(destino, "Users")
    try:
        usuarios = sorted(d for d in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, d)))
    except OSError:
        usuarios = []
    for u in usuarios:
        cs = []
        for c in carpetas_perfil:
            ruta = os.path.join(raiz, u, c)
            if os.path.isdir(ruta):
                a, t = medir_carpeta(ruta)
                cs.append({"clave": c, "nombre": NOMBRES.get(c, c), "ruta": ruta, "archivos": a, "bytes": t})
        if cs:
            res["usuarios"].append({"usuario": u, "carpetas": cs})
    d = os.path.join(destino, "D")
    if os.path.isdir(d):
        res["unidad_d"] = d
    return res


def _e(x):
    return html.escape(str(x if x is not None else ""))


def secciones(d):
    """Lista de (titulo, [(etiqueta, texto)]) en lenguaje para personas, a partir del dict de datos."""
    s = []
    s.append(("Tu equipo nuevo", [
        ("Nombre del equipo", d.get("host") or "—"),
        ("Fecha del servicio", d.get("fecha", "")),
        ("Cómo entrar", "Con tu usuario y contraseña de siempre." if d.get("dominio_ok") else
         "Si no puedes entrar con tu usuario de siempre, avisa a soporte."),
    ]))
    filas = []
    rest = d.get("restaurado") or {}
    for u in d.get("respaldo", {}).get("usuarios", []):
        for c in u["carpetas"]:
            cuando = ""
            if rest and rest.get("origen") == u["usuario"]:
                cuando = f"Ya copiado a tu carpeta {c['nombre']} de este equipo."
            filas.append((f"{c['nombre']} ({u['usuario']})",
                          f"{c['archivos']:,} archivos · {U.fmt_bytes(c['bytes'])} · copia de seguridad en {c['ruta']}. {cuando}".strip()))
    if d.get("respaldo", {}).get("unidad_d"):
        filas.append(("Unidad D: de tu equipo anterior", f"Quedó en {d['respaldo']['unidad_d']}"))
    if not filas:
        filas.append(("Archivos", "No se trajeron archivos en este servicio."))
    s.append(("Dónde quedaron tus archivos", filas))
    if rest:
        nombres = [c["nombre"] for u in d.get("respaldo", {}).get("usuarios", []) if u["usuario"] == rest.get("origen")
                   for c in u["carpetas"]]
        s.append(("Tus carpetas de siempre", [
            (", ".join(nombres) or "Tus carpetas",
             f"Ya están en tu usuario ({rest.get('destino', '—')}), como las tenías. La copia de seguridad sigue en la carpeta de respaldo.")]))
    else:
        s.append(("Tus carpetas de siempre", [
            ("Pendiente", "Después de tu primer inicio de sesión, soporte copiará tus carpetas a tu usuario. "
                          "Mientras tanto, tus archivos están en la copia de seguridad.")]))
    conf = []
    imp = d.get("impresora")
    conf.append(("Impresora", imp if imp else "No se instaló en este servicio."))
    if d.get("fuentes"):
        conf.append(("Tipografías institucionales", d["fuentes"]))
    s.append(("Configuración de tu equipo", conf))
    faltan = d.get("faltan") or []
    prog = []
    if d.get("tiene_lista"):
        if faltan:
            prog.append(("Programas que tenías y aún no están", ", ".join(faltan[:15]) + (" …" if len(faltan) > 15 else "")))
            prog.append(("Qué hacer", "Pídelos a soporte indicando el nombre del programa."))
        else:
            prog.append(("Programas", "Todos tus programas importantes quedaron instalados."))
    else:
        prog.append(("Programas", "No se comparó la lista de programas. Si te falta alguno, pídelo a soporte."))
    s.append(("Tus programas", prog))
    viejo = [("Equipo anterior", d.get("host_viejo") or "—")]
    if d.get("fecha_borrado"):
        viejo.append(("Se conserva hasta", f"{d['fecha_borrado']} como mínimo. No lo borres ni lo apagues sin avisar."))
    s.append(("Tu equipo anterior", viejo))
    sop = d.get("soporte") or "tu soporte técnico"
    s.append(("Si no encuentras algo", [
        ("Primero", "Busca el nombre del archivo en el buscador de Windows (tecla Windows y escribir)."),
        ("Después", f"Avisa a {sop} con el nombre del equipo ({d.get('host') or '—'}); todo quedó registrado."),
    ]))
    return s


def construir(d):
    sec = secciones(d)
    txt = ["TU EQUIPO NUEVO: DÓNDE QUEDÓ TODO", "=" * 40, ""]
    for t, filas in sec:
        txt.append(t.upper())
        for a, b in filas:
            txt.append(f"  - {a}: {b}")
        txt.append("")
    txt.append("Generado por Mainbit Toolkit.")
    css = ("body{font-family:'Segoe UI',Arial,sans-serif;max-width:820px;margin:28px auto;padding:0 18px;color:#14233f}"
           "h1{font-size:26px;margin:0 0 4px}.sub{color:#52607a;margin-bottom:22px}"
           "h2{font-size:15px;letter-spacing:.06em;text-transform:uppercase;color:#0a5bd6;margin:22px 0 8px;border-bottom:2px solid #dbe6fb;padding-bottom:4px}"
           "table{width:100%;border-collapse:collapse}td{padding:8px 10px;vertical-align:top;border-bottom:1px solid #edf1f8}"
           "td:first-child{width:34%;font-weight:600}.pie{margin-top:26px;color:#7b869b;font-size:12px}"
           "@media print{body{margin:0}}")
    cuerpo = []
    for t, filas in sec:
        cuerpo.append(f"<h2>{_e(t)}</h2><table>" + "".join(
            f"<tr><td>{_e(a)}</td><td>{_e(b)}</td></tr>" for a, b in filas) + "</table>")
    htm = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Tu equipo nuevo</title><style>{css}</style></head>"
           f"<body><h1>Tu equipo nuevo: dónde quedó todo</h1><div class='sub'>{_e(d.get('host', ''))} · {_e(d.get('fecha', ''))}</div>"
           + "".join(cuerpo) + "<div class='pie'>Generado por Mainbit Toolkit. Puedes imprimirlo o guardarlo como PDF desde el navegador.</div></body></html>")
    return "\n".join(txt), htm


def datos_servicio(host, destino, fecha=None, **kw):
    """Arma el dict de datos con el inventario real del respaldo; kw completa lo demás."""
    d = {"host": host, "fecha": (fecha or datetime.now()).strftime("%d/%m/%Y %H:%M"),
         "respaldo": inventario_respaldo(destino, kw.pop("carpetas_perfil", None))}
    d.update(kw)
    return d


def escritorio_publico():
    for d in (os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
              os.path.join(os.path.expanduser("~"), "Desktop")):
        if os.path.isdir(d):
            return d
    return ""


def escribir_manual(carpeta, d, copiar_escritorio=True):
    """Guarda el manual en Reportes y una copia visible en el escritorio. Devuelve (html, txt, copia)."""
    os.makedirs(carpeta, exist_ok=True)
    txt, htm = construir(d)
    base = os.path.join(carpeta, "Manual_usuario_" + "".join(c if c.isalnum() or c in "-_" else "_" for c in (d.get("host") or "PC"))
                        + f"_{datetime.now():%Y%m%d_%H%M}")
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(htm)
    with open(base + ".txt", "w", encoding="utf-8-sig") as f:
        f.write(txt)
    copia = ""
    if copiar_escritorio:
        esc = escritorio_publico()
        if esc:
            try:
                copia = os.path.join(esc, "LEEME - Tu equipo nuevo.html")
                with open(copia, "w", encoding="utf-8") as f:
                    f.write(htm)
            except OSError:
                copia = ""
    return base + ".html", base + ".txt", copia
