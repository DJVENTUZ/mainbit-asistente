"""Descargas robustas: reintentos, reanudación (.part), SHA-256 opcional. Solo biblioteca estándar."""
import hashlib
import os
import urllib.request
import urllib.error
import time

UA = "MainbitAsistente/1"


def descargar(url, destino, sha256=None, reintentos=3, progreso=None, timeout=30):
    """Devuelve (ok, mensaje). Escribe en destino+'.part' y renombra al terminar."""
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    parte = destino + ".part"
    ultimo = ""
    for intento in range(1, reintentos + 1):
        try:
            tam = os.path.getsize(parte) if os.path.exists(parte) else 0
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            if tam:
                req.add_header("Range", f"bytes={tam}-")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if tam and r.status != 206:
                    tam = 0
                total = r.headers.get("Content-Length")
                total = int(total) + tam if total else 0
                with open(parte, "ab" if tam else "wb") as f:
                    while True:
                        b = r.read(65536)
                        if not b:
                            break
                        f.write(b)
                        tam += len(b)
                        if progreso:
                            progreso(tam, total)
            if sha256 and sha256_archivo(parte) != sha256.lower():
                os.remove(parte)
                return False, "La suma SHA-256 no coincide (archivo descartado)"
            os.replace(parte, destino)
            return True, "Descargado"
        except (urllib.error.URLError, OSError, ValueError) as e:
            ultimo = str(e)[:150]
            time.sleep(min(2 * intento, 6))
    return False, f"Sin descarga: {ultimo}"


def sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
