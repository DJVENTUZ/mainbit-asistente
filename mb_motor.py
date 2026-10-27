# -*- coding: utf-8 -*-
"""Motor de transferencia Mainbit v3 (TCP directo, sin SMB ni credenciales).

Protocolo MAINBIT3
  Emisor  -> cabecera JSON (archivos, carpetas, bytes, info de la PC vieja)
  Receptor-> 'A' aceptado  |  'R'+motivo (límite de tamaño)
  Emisor  -> carpetas ('D'), luego MANIFIESTO (ruta, tamaño, fecha) de todos los archivos
  Receptor-> 'K' + bytes/archivos que FALTAN + mapa de bits   |  'R'+motivo (falta espacio)
  Emisor  -> solo los archivos que faltan ('F' + datos + CRC32), notas ('S') y fin ('E')
  Receptor-> 'K'/'X' + verificados + fallidos

Por eso, si se corta el cable o se cierra algo, al volver a enviar RETOMA: no vuelve a copiar
lo que ya llegó completo y verificado.
"""
import ctypes
import errno
import json
import os
import shutil
import socket
import stat
import struct
import threading
import time
import zlib
from datetime import datetime

from mb_util import (IS_WIN, acortar, dentro, fmt_bytes, fmt_tiempo, limpio, lp, ruta_segura)

MAGIC = "MAINBIT3"
PORT_TRANSFER = 50008
PORT_DISCOVER = 50009
CHUNK = 1024 * 1024
PEQUENO = 256 * 1024
LIMITE_BYTES_DEFECTO = 500 * 1024 ** 3
MARGEN_LIBRE = 1024 ** 3

EXCLUIR_NOMBRES = {"$recycle.bin", "system volume information", "pagefile.sys", "hiberfil.sys",
                   "swapfile.sys", "thumbs.db", "$windows.~bt", "$windows.~ws", "windows.old"}
EXCLUIR_RAIZ = {"windows", "program files", "program files (x86)", "programdata", "recovery",
                "msocache", "config.msi", "intel", "perflogs"}
PERFILES_EXCLUIDOS = {"default", "default user", "all users", "defaultuser0"}
CACHE_DIRS = {"cache", "cache2", "code cache", "gpucache", "media cache", "service worker",
              "crashpad", "temp", "tmp", "shadercache", "dawncache", "grshadercache"}
MARCADORES = ("Local/Google/Chrome/User Data/Default/Bookmarks",
              "Local/Microsoft/Edge/User Data/Default/Bookmarks")
ATTR_REPARSE = 0x400
ATTR_NUBE = 0x1000 | 0x40000 | 0x400000


class Cancelado(Exception):
    pass


class Rechazado(Exception):
    pass


class DiscoLleno(Exception):
    pass


class LimiteExcedido(Exception):
    def __init__(self, total, desglose=None):
        super().__init__(total)
        self.total = total
        self.desglose = desglose or []


# ----------------------------------------------------------------------
# Estado compartido
# ----------------------------------------------------------------------
class Progreso:
    def __init__(self):
        self.fase = "idle"      # idle|analizando|esperando|conectando|transfiriendo|fin|error
        self.files_total = 0
        self.files_done = 0
        self.files_ya = 0       # ya estaban en el destino (reanudación)
        self.bytes_total = 0
        self.bytes_done = 0
        self.actual = ""
        self.completo = False


class Job:
    def __init__(self, log):
        self.prog = Progreso()
        self.cancel = threading.Event()
        self.log = log
        self.socks = []

    def registrar(self, s):
        self.socks.append(s)
        return s

    def abortar(self):
        self.cancel.set()
        for s in list(self.socks):
            cerrar_sock(s)


# ----------------------------------------------------------------------
# Red
# ----------------------------------------------------------------------
def recv_exact(sock, n):
    buf = bytearray(n)
    mv = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(mv[got:], n - got)
        if not k:
            raise ConnectionError("La conexión se cerró antes de tiempo.")
        got += k
    return bytes(buf)


def afinar(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass


def cerrar_sock(s):
    for fn in (lambda: s.shutdown(socket.SHUT_RDWR), s.close):
        try:
            fn()
        except OSError:
            pass


def ips_locales():
    from mb_sistema import ips_locales as f
    return f()


# ----------------------------------------------------------------------
# Unidades de disco
# ----------------------------------------------------------------------
def unidades():
    """Unidades del equipo: [{letra, tipo, etiqueta, total, libre}] tipo: fijo|usb|cd|red"""
    res = []
    if not IS_WIN:
        return res
    try:
        mascara = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not mascara & (1 << i):
                continue
            letra = chr(65 + i) + ":"
            tipo_n = ctypes.windll.kernel32.GetDriveTypeW(letra + "\\")
            tipo = {2: "usb", 3: "fijo", 4: "red", 5: "cd"}.get(tipo_n, "otro")
            etiqueta = ""
            total = libre = 0
            if tipo in ("fijo", "usb"):
                try:
                    buf = ctypes.create_unicode_buffer(261)
                    ctypes.windll.kernel32.GetVolumeInformationW(letra + "\\", buf, 261, None, None, None, None, 0)
                    etiqueta = buf.value
                    du = shutil.disk_usage(letra + "\\")
                    total, libre = du.total, du.free
                except OSError:
                    pass
            res.append({"letra": letra, "tipo": tipo, "etiqueta": etiqueta, "total": total, "libre": libre})
    except Exception:
        pass
    return res


# ----------------------------------------------------------------------
# Análisis previo
# ----------------------------------------------------------------------
def nombre_unico(nombre, usados):
    base, n = nombre, 2
    while nombre.lower() in usados:
        nombre = f"{base} ({n})"
        n += 1
    usados.add(nombre.lower())
    return nombre


def _excluir_archivo(nombre_l, en_appdata):
    """Basura que no se migra: hive del perfil, bloqueos de Office y .tmp dentro de AppData."""
    return (nombre_l.startswith("ntuser.dat") or nombre_l.startswith("~$")
            or (en_appdata and nombre_l.endswith(".tmp")))


def escanear(job, rutas, limite=LIMITE_BYTES_DEFECTO, excluir_cache=True):
    """Recorre las rutas, cuenta archivos/peso y aborta si pasa del límite.
    No sigue enlaces/uniones (evita ciclos infinitos que llenan el disco)."""
    p = job.prog
    archivos, carpetas, omitidos = [], [], []
    excluidos = {"cache": 0, "perfiles": 0, "sistema": 0, "nube": 0, "enlaces": 0}
    desglose = []
    usados = set()
    total = 0
    for ruta in rutas:
        ruta = os.path.abspath(ruta)
        total_antes = total
        n_antes = len(archivos)
        if os.path.isfile(lp(ruta)):
            try:
                st = os.stat(lp(ruta))
            except OSError:
                omitidos.append(("sin acceso", ruta))
                continue
            archivos.append((lp(ruta), nombre_unico(os.path.basename(ruta), usados), st.st_size, st.st_mtime))
            total += st.st_size
            p.files_total += 1
            p.bytes_total = total
            desglose.append((os.path.basename(ruta), st.st_size, 1))
            if total > limite:
                raise LimiteExcedido(total, desglose)
            continue
        if not os.path.isdir(lp(ruta)):
            omitidos.append(("no existe", ruta))
            continue

        es_raiz = os.path.dirname(ruta) == ruta
        base = os.path.basename(os.path.normpath(ruta)) or ruta.replace(":", "").strip("\\/") or "RAIZ"
        es_users = base.lower() == "users"
        top = nombre_unico(base, usados)
        carpetas.append(top)
        pila = [(ruta, top)]
        while pila:
            if job.cancel.is_set():
                raise Cancelado()
            d, rel = pila.pop()
            try:
                it = os.scandir(lp(d))
            except OSError:
                omitidos.append(("sin acceso a la carpeta", d))
                continue
            partes = rel.split("/")
            en_appdata = any(x.lower() == "appdata" for x in partes)
            with it:
                while True:
                    try:
                        e = next(it)
                    except StopIteration:
                        break
                    except OSError:
                        omitidos.append(("error leyendo carpeta", d))
                        break
                    low = e.name.lower()
                    if low in EXCLUIR_NOMBRES:
                        excluidos["sistema"] += 1
                        continue
                    if es_raiz and d == ruta and low in EXCLUIR_RAIZ:
                        excluidos["sistema"] += 1
                        continue
                    if es_users and len(partes) == 1 and low in PERFILES_EXCLUIDOS:
                        excluidos["perfiles"] += 1
                        continue
                    rel_e = f"{rel}/{e.name}"
                    try:
                        if e.is_symlink():
                            excluidos["enlaces"] += 1
                            omitidos.append(("enlace simbólico", e.path))
                            continue
                        st = e.stat(follow_symlinks=False)
                        attrs = getattr(st, "st_file_attributes", 0)
                        if e.is_dir(follow_symlinks=False):
                            if attrs & ATTR_REPARSE:
                                excluidos["enlaces"] += 1
                                omitidos.append(("unión/enlace de carpeta", e.path))
                                continue
                            if excluir_cache:
                                if en_appdata and low in CACHE_DIRS:
                                    excluidos["cache"] += 1
                                    continue
                                if low == "local" and partes and partes[-1].lower() == "appdata":
                                    excluidos["cache"] += 1      # AppData\Local completo = cachés
                                    continue
                                if low == "appdata":
                                    # se conservan solo los marcadores de Chrome/Edge
                                    for m in MARCADORES:
                                        f_abs = os.path.join(e.path, *m.split("/"))
                                        try:
                                            stm = os.stat(lp(f_abs))
                                            archivos.append((lp(f_abs), f"{rel_e}/{m}", stm.st_size, stm.st_mtime))
                                            total += stm.st_size
                                            p.files_total += 1
                                        except OSError:
                                            pass
                            carpetas.append(rel_e)
                            pila.append((e.path, rel_e))
                        elif e.is_file(follow_symlinks=False):
                            if attrs & ATTR_NUBE:
                                excluidos["nube"] += 1
                                omitidos.append(("solo en la nube (no descargado)", e.path))
                                continue
                            if excluir_cache and _excluir_archivo(low, en_appdata):
                                excluidos["cache"] += 1
                                continue
                            archivos.append((e.path, rel_e, st.st_size, st.st_mtime))
                            total += st.st_size
                            p.files_total += 1
                            p.bytes_total = total
                            if total > limite:
                                desglose.append((top + " (parcial)", total - total_antes, len(archivos) - n_antes))
                                raise LimiteExcedido(total, desglose)
                    except OSError:
                        omitidos.append(("sin acceso", e.path))
        desglose.append((top, total - total_antes, len(archivos) - n_antes))
    p.bytes_total = total
    return {"archivos": archivos, "carpetas": carpetas, "bytes": total, "omitidos": omitidos,
            "excluidos": excluidos, "desglose": desglose}


# ----------------------------------------------------------------------
# EMISOR
# ----------------------------------------------------------------------
def buscar_receptor(ips=None):
    """Broadcast por cada interfaz local. Devuelve (ip, nombre, version) o None."""
    for ip in (ips if ips is not None else ips_locales()) or [""]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((ip, 0))
            s.settimeout(1.5)
            dests = ["255.255.255.255"]
            if ip.startswith("169.254."):
                dests.append("169.254.255.255")
            elif ip:
                dests.append(ip.rsplit(".", 1)[0] + ".255")
            for d in dests:
                try:
                    s.sendto(b"MAINBIT_DISCOVER", (d, PORT_DISCOVER))
                except OSError:
                    pass
            while True:
                data, addr = s.recvfrom(1024)
                if data.startswith(b"MAINBIT_HERE"):
                    p = data.decode("utf-8", "replace").split("|")
                    return addr[0], (p[1] if len(p) > 1 else ""), (p[2] if len(p) > 2 else "2")
        except OSError:
            pass
        finally:
            s.close()
    return None


MSG_VERSION = ("La otra PC no entendió el saludo: casi siempre es una versión distinta del programa.\n"
               "Abre «Mainbit Asistente» en las DOS PCs (cierra antes la versión vieja).")


def enviar(job, ip, scan, info=None, port=PORT_TRANSFER):
    p = job.prog
    archivos, carpetas, omitidos = scan["archivos"], scan["carpetas"], scan["omitidos"]
    p.fase = "conectando"
    p.files_done = p.bytes_done = p.files_ya = 0
    p.files_total, p.bytes_total = len(archivos), scan["bytes"]
    job.log(f"Conectando con {ip}…", None)
    s = job.registrar(socket.create_connection((ip, port), timeout=10))
    try:
        afinar(s)
        s.settimeout(60)
        head = json.dumps({"magic": MAGIC, "files": len(archivos), "dirs": len(carpetas),
                           "bytes": scan["bytes"], "skipped": len(omitidos),
                           "host": socket.gethostname(), "info": info or {}}).encode("utf-8")
        s.sendall(struct.pack("!I", len(head)) + head)
        try:
            st, n = struct.unpack("!cH", recv_exact(s, 3))
            msg = recv_exact(s, n).decode("utf-8", "replace") if n else ""
        except (ConnectionError, socket.timeout):
            if job.cancel.is_set():
                raise Cancelado()
            raise Rechazado(MSG_VERSION)
        if st != b"A":
            raise Rechazado(msg or "El receptor rechazó la transferencia.")

        lote = bytearray()
        for rel in carpetas:
            pb = rel.encode("utf-8")
            lote += b"D" + struct.pack("!I", len(pb)) + pb
            if len(lote) > 65536:
                s.sendall(lote)
                lote.clear()
        if lote:
            s.sendall(lote)

        # --- manifiesto: el receptor responde qué le falta (reanudación)
        job.log("Comparando con lo que ya hay en la PC nueva…", None)
        man = bytearray()
        for _ruta, rel, size, mtime in archivos:
            pb = rel.encode("utf-8")
            man += struct.pack("!I", len(pb)) + pb + struct.pack("!Qd", size, float(mtime))
            if len(man) > (1 << 20):
                s.sendall(man)
                man.clear()
        if man:
            s.sendall(man)
        s.settimeout(300)           # el receptor puede tardar en comparar cientos de miles de archivos
        st = recv_exact(s, 1)
        if st == b"R":
            n = struct.unpack("!H", recv_exact(s, 2))[0]
            raise Rechazado(recv_exact(s, n).decode("utf-8", "replace"))
        need_bytes, need_count = struct.unpack("!QI", recv_exact(s, 12))
        bitmap = recv_exact(s, (len(archivos) + 7) // 8)
        s.settimeout(60)

        p.fase = "transfiriendo"
        p.files_total, p.bytes_total = need_count, need_bytes
        p.files_ya = len(archivos) - need_count
        if p.files_ya:
            job.log(f"{p.files_ya:,} archivos ya estaban completos en la PC nueva: se omiten.", "info")
        job.log(f"Transfiriendo {need_count:,} archivos · {fmt_bytes(need_bytes)}…", "ok")

        buf = bytearray(CHUNK)
        mv = memoryview(buf)
        fallidos, ilegibles = [], []
        top_prev = None
        for idx, (ruta, rel, size, mtime) in enumerate(archivos):
            if not (bitmap[idx >> 3] >> (idx & 7)) & 1:
                continue
            if job.cancel.is_set():
                raise Cancelado()
            try:
                f = open(ruta, "rb")
            except OSError as e:
                # el receptor espera este archivo: se envía vacío con CRC inválido (queda descartado)
                f = None
                fallidos.append((rel, e.strerror or str(e)))
                if len(fallidos) <= 100:
                    job.log(f"No se pudo abrir: {acortar(rel)} ({e.strerror or e})", "warn")
            top = rel.split("/", 1)[0]
            if top != top_prev:
                top_prev = top
                job.log(f"Carpeta: {top}", "info")
            p.actual = rel
            pb = rel.encode("utf-8")
            cab = b"F" + struct.pack("!I", len(pb)) + pb + struct.pack("!Qd", size, float(mtime))
            buenas = f is not None
            try:
                if size <= PEQUENO:
                    data = b""
                    if f is not None:
                        try:
                            data = f.read(size)
                        except OSError:
                            data = b""
                    if len(data) < size:
                        buenas = False
                        data += bytes(size - len(data))
                    crc = zlib.crc32(data)
                    if not buenas:
                        crc ^= 0xFFFFFFFF
                    s.sendall(cab + data + struct.pack("!I", crc & 0xFFFFFFFF))
                    p.bytes_done += size
                else:
                    s.sendall(cab)
                    crc, restante = 0, size
                    while restante:
                        want = min(CHUNK, restante)
                        n = 0
                        if buenas:
                            try:
                                n = f.readinto(mv[:want])
                            except OSError:
                                n = 0
                        if not n:
                            buenas = False
                            relleno = bytes(want)
                            s.sendall(relleno)
                            crc = zlib.crc32(relleno, crc)
                            n = want
                        else:
                            s.sendall(mv[:n])
                            crc = zlib.crc32(mv[:n], crc)
                        restante -= n
                        p.bytes_done += n
                        if job.cancel.is_set():
                            raise Cancelado()
                    if not buenas:
                        crc ^= 0xFFFFFFFF
                    s.sendall(struct.pack("!I", crc & 0xFFFFFFFF))
            finally:
                if f is not None:
                    f.close()
            if not buenas and f is not None:
                ilegibles.append(rel)
                job.log(f"Ilegible o modificado durante el envío: {acortar(rel)}", "warn")
            p.files_done += 1

        notas = [f"{m} | {limpio(r)}" for m, r in omitidos]
        notas += [f"no se pudo abrir | {r} ({m})" for r, m in fallidos]
        notas += [f"ilegible o modificado durante el envío | {r}" for r in ilegibles]
        texto = "\n".join(notas[:5000]).encode("utf-8")
        s.sendall(b"S" + struct.pack("!I", len(texto)) + texto + b"E")
        s.settimeout(120)
        st, ok, bad = struct.unpack("!cII", recv_exact(s, 9))
        return {"enviados": p.files_done, "ya_estaban": p.files_ya, "fallidos": fallidos,
                "ilegibles": ilegibles, "omitidos": omitidos, "recibidos_ok": ok, "recibidos_mal": bad,
                "total_archivos": len(archivos)}
    finally:
        cerrar_sock(s)


def enviar_con_reintentos(job, ip, scan, info=None, max_reintentos=8, espera=5, port=PORT_TRANSFER):
    """Si se corta la conexión (cable flojo, receptor reiniciado…) reintenta solo y RETOMA."""
    intento = 0
    while True:
        try:
            return enviar(job, ip, scan, info, port)
        except (Cancelado, Rechazado):
            raise
        except (ConnectionError, OSError, socket.timeout) as e:
            if job.cancel.is_set():
                raise Cancelado()
            intento += 1
            if intento > max_reintentos:
                raise
            job.log(f"Conexión interrumpida ({e}). Reintento {intento}/{max_reintentos} en {espera} s…", "warn")
            for _ in range(espera * 10):
                if job.cancel.is_set():
                    raise Cancelado()
                time.sleep(0.1)


# ----------------------------------------------------------------------
# RECEPTOR
# ----------------------------------------------------------------------
def servidor_discovery(stop, puerto=PORT_DISCOVER):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", puerto))
        sock.settimeout(1.0)
        resp = b"MAINBIT_HERE|" + socket.gethostname().encode("utf-8", "replace") + b"|3"
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            if data == b"MAINBIT_DISCOVER":
                sock.sendto(resp, addr)
    except OSError:
        pass
    finally:
        sock.close()


def _rechazar(conn, motivo):
    try:
        b = motivo.encode("utf-8")[:60000]
        conn.sendall(struct.pack("!cH", b"R", len(b)) + b)
    except OSError:
        pass


def _necesita(final, size, mtime):
    """True si el archivo NO está ya completo (mismo tamaño y fecha) en el destino."""
    try:
        st = os.stat(final)
    except OSError:
        return True
    return not (st.st_size == size and abs(st.st_mtime - mtime) < 2.0)


def _recibir_archivo(conn, mv, final, size, mtime, p):
    """Recibe un archivo. Devuelve (ok, motivo). Siempre consume los bytes del flujo."""
    tmp = final + ".mbpart"
    f = None
    err = ""
    try:
        os.makedirs(os.path.dirname(final), exist_ok=True)
        if os.path.exists(final):
            try:
                os.chmod(final, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        f = open(tmp, "wb")
    except OSError as e:
        err = e.strerror or str(e)
    crc, restante = 0, size
    try:
        while restante:
            k = conn.recv_into(mv[:min(CHUNK, restante)])
            if not k:
                raise ConnectionError("Conexión interrumpida durante un archivo.")
            if f is not None:
                try:
                    f.write(mv[:k])
                except OSError as e:
                    if e.errno == errno.ENOSPC:
                        raise DiscoLleno("El disco de la PC nueva se llenó.")
                    err = e.strerror or str(e)
                    f.close()
                    f = None
            crc = zlib.crc32(mv[:k], crc)
            restante -= k
            p.bytes_done += k
        crc_rx = struct.unpack("!I", recv_exact(conn, 4))[0]
    except BaseException:
        if f is not None:
            f.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if f is None:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, err or "no se pudo escribir"
    f.close()
    if crc_rx != (crc & 0xFFFFFFFF):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "verificación CRC fallida u origen ilegible"
    try:
        os.replace(tmp, final)
        try:
            os.utime(final, (mtime, mtime))
        except (OSError, OverflowError, ValueError):
            pass
    except OSError as e:
        return False, e.strerror or str(e)
    return True, ""


def escribir_reporte(destino, d):
    L = ["Mainbit Asistente — reporte de migración",
         f"Fecha: {datetime.now():%Y-%m-%d %H:%M:%S}",
         f"Emisor: {d['host']} ({d['ip']})",
         f"Archivos verificados en esta sesión: {d['ok']:,}",
         f"Archivos que ya estaban completos (reanudación): {d['ya']:,}",
         f"Datos recibidos: {fmt_bytes(d['bytes'])}",
         f"Duración de la sesión: {fmt_tiempo(d['seg'])}",
         f"Archivos con verificación fallida (descartados): {len(d['malos'])}"]
    L += [f"   - {r} ({m})" for r, m in d["malos"][:2000]]
    L += ["", "Notas del emisor (omitidos / no leídos):", d["notas"] or "   (ninguna)"]
    ruta = os.path.join(destino, "_REPORTE_MIGRACION.txt")
    try:
        with open(lp(ruta), "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        return ruta
    except OSError:
        return None


def recibir(job, destino, limite=LIMITE_BYTES_DEFECTO, port=PORT_TRANSFER, al_conectar=None):
    """Espera al emisor. Si una sesión se interrumpe, sigue escuchando para REANUDAR.
    Solo termina cuando llega una migración completa (o al cancelar)."""
    p = job.prog
    destino = os.path.abspath(destino)
    os.makedirs(lp(destino), exist_ok=True)
    srv = job.registrar(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    if os.name != "nt":
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    srv.settimeout(1.0)
    try:
        while True:
            p.fase = "esperando"
            p.files_total = p.files_done = p.bytes_total = p.bytes_done = p.files_ya = 0
            p.actual = ""
            conn = addr = None
            while conn is None:
                if job.cancel.is_set():
                    raise Cancelado()
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if job.cancel.is_set():
                        raise Cancelado()
                    raise
            job.registrar(conn)
            try:
                res = _sesion(job, conn, addr, destino, limite, al_conectar)
                if res is not None:
                    return res
            except Cancelado:
                raise
            except DiscoLleno as e:
                job.log(f"{e} Libera espacio y vuelve a enviar: se retoma donde quedó.", "err")
            except (ConnectionError, OSError, socket.timeout, struct.error, ValueError) as e:
                if job.cancel.is_set():
                    raise Cancelado()
                job.log(f"Sesión interrumpida ({e}). Sigo esperando: al reenviar se retoma lo pendiente.", "warn")
            finally:
                cerrar_sock(conn)
    finally:
        cerrar_sock(srv)


def _sesion(job, conn, addr, destino, limite, al_conectar):
    """Una sesión. Devuelve el resultado si terminó bien; None si se ignoró/rechazó."""
    p = job.prog
    conn.settimeout(15)
    afinar(conn)
    try:
        hlen = struct.unpack("!I", recv_exact(conn, 4))[0]
        if hlen > 1_000_000:
            raise ValueError("cabecera")
        head = json.loads(recv_exact(conn, hlen).decode("utf-8"))
    except (OSError, ValueError, struct.error):
        job.log(f"Conexión no válida desde {addr[0]} (ignorada).", "warn")
        return None
    if head.get("magic") != MAGIC:
        _rechazar(conn, "El emisor usa una versión anterior del programa. Abre «Mainbit Asistente» "
                        "en las dos PCs.")
        job.log(f"{addr[0]} usa una versión anterior (rechazada).", "err")
        return None
    total, nfiles = int(head["bytes"]), int(head["files"])
    if total > limite:
        _rechazar(conn, f"La migración ({fmt_bytes(total)}) supera el límite de {fmt_bytes(limite)}.")
        job.log(f"Migración de {addr[0]} rechazada: supera {fmt_bytes(limite)}.", "err")
        return None
    conn.sendall(struct.pack("!cH", b"A", 0))

    host = str(head.get("host", "?"))
    info = head.get("info") or {}
    job.log(f"Conectado con {host} ({addr[0]}): {nfiles:,} archivos · {fmt_bytes(total)}", "ok")
    if al_conectar:
        try:
            al_conectar(host, addr[0], info)
        except Exception:
            pass
    try:
        with open(lp(os.path.join(destino, "_INFO_PC_VIEJA.json")), "w", encoding="utf-8") as f:
            json.dump({"host": host, "ip": addr[0], "info": info, "archivos": nfiles, "bytes": total,
                       "fecha": datetime.now().isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    p.fase = "transfiriendo"
    p.files_total, p.bytes_total = nfiles, total
    t0 = time.monotonic()
    conn.settimeout(60)

    # --- carpetas
    pendientes_dirs = int(head.get("dirs", 0))
    for _ in range(pendientes_dirs):
        tipo = recv_exact(conn, 1)
        if tipo != b"D":
            raise ValueError("Protocolo inválido (carpetas).")
        n = struct.unpack("!I", recv_exact(conn, 4))[0]
        rel = recv_exact(conn, n).decode("utf-8")
        try:
            os.makedirs(lp(ruta_segura(destino, rel)), exist_ok=True)
        except (ValueError, OSError):
            pass

    # --- manifiesto -> qué falta
    conn.settimeout(300)
    necesita = bytearray((nfiles + 7) // 8)
    need_bytes = need_count = 0
    for i in range(nfiles):
        n = struct.unpack("!I", recv_exact(conn, 4))[0]
        rel = recv_exact(conn, n).decode("utf-8")
        size, mtime = struct.unpack("!Qd", recv_exact(conn, 16))
        try:
            requerido = _necesita(lp(ruta_segura(destino, rel)), size, mtime)
        except ValueError:
            requerido = False
            job.log(f"Ruta no permitida, se omite: {acortar(rel)}", "warn")
        if requerido:
            necesita[i >> 3] |= 1 << (i & 7)
            need_bytes += size
            need_count += 1
        if job.cancel.is_set():
            raise Cancelado()
    conn.settimeout(60)
    libre = shutil.disk_usage(destino).free
    if libre < need_bytes + MARGEN_LIBRE:
        motivo = (f"Espacio insuficiente en la PC nueva: se necesitan {fmt_bytes(need_bytes + MARGEN_LIBRE)} "
                  f"y hay {fmt_bytes(libre)} libres.")
        _rechazar(conn, motivo)
        job.log(motivo, "err")
        return None
    conn.sendall(b"K" + struct.pack("!QI", need_bytes, need_count) + bytes(necesita))
    p.files_total, p.bytes_total = need_count, need_bytes
    p.files_ya = nfiles - need_count
    p.files_done = p.bytes_done = 0
    if p.files_ya:
        job.log(f"{p.files_ya:,} archivos ya estaban completos: se omiten.", "info")

    # --- archivos
    ok, malos, notas = 0, [], ""
    buf = bytearray(CHUNK)
    mv = memoryview(buf)
    top_prev = None
    while True:
        if job.cancel.is_set():
            raise Cancelado()
        tipo = recv_exact(conn, 1)
        if tipo == b"E":
            break
        n = struct.unpack("!I", recv_exact(conn, 4))[0]
        datos = recv_exact(conn, n)
        if tipo == b"S":
            notas = datos.decode("utf-8", "replace")
            continue
        if tipo != b"F":
            raise ValueError("Protocolo inválido.")
        rel = datos.decode("utf-8")
        size, mtime = struct.unpack("!Qd", recv_exact(conn, 16))
        p.actual = rel
        top = rel.split("/", 1)[0]
        if top != top_prev:
            top_prev = top
            job.log(f"Carpeta: {top}", "info")
        try:
            final = lp(ruta_segura(destino, rel))
        except ValueError as e:
            final = None
            motivo_ruta = str(e)
        if final is None:           # ruta peligrosa: se consume y descarta
            restante = size
            while restante:
                k = conn.recv_into(mv[:min(CHUNK, restante)])
                if not k:
                    raise ConnectionError("Conexión interrumpida.")
                restante -= k
            recv_exact(conn, 4)
            malos.append((rel, motivo_ruta))
        else:
            bien, motivo = _recibir_archivo(conn, mv, final, size, mtime, p)
            if bien:
                ok += 1
            else:
                malos.append((rel, motivo))
                job.log(f"Descartado: {acortar(rel)} ({motivo})", "err")
        p.files_done += 1

    seg = time.monotonic() - t0
    rep = escribir_reporte(destino, {"host": host, "ip": addr[0], "ok": ok, "ya": p.files_ya, "malos": malos,
                                     "bytes": p.bytes_done, "seg": seg, "notas": notas})
    conn.sendall(struct.pack("!cII", b"K" if not malos else b"X", ok, len(malos)))
    return {"ok": ok, "ya": p.files_ya, "malos": malos, "host": host, "ip": addr[0], "info": info,
            "bytes": p.bytes_done, "seg": seg, "reporte": rep, "notas": notas, "total": nfiles}
