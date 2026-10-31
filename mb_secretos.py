"""Almacén de secretos cifrado con PIN (scrypt + AES-GCM). Nada va en texto plano ni en el repo.
Uso: guardar(ruta, pin, {"wifi_pass": "..."}) / leer(ruta, pin)."""
import base64
import hashlib
import json
import os

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:      # el motor sigue funcionando sin el paquete; solo no recuerda claves
    AESGCM = None


def disponible():
    return AESGCM is not None


def _clave(pin, sal):
    return hashlib.scrypt(pin.encode("utf-8"), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)


def guardar(ruta, pin, datos):
    if not AESGCM:
        return False
    if len(pin) < 4:
        raise ValueError("El PIN debe tener al menos 4 caracteres")
    sal, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_clave(pin, sal)).encrypt(nonce, json.dumps(datos).encode("utf-8"), b"mainbit1")
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "s": base64.b64encode(sal).decode(), "n": base64.b64encode(nonce).decode(),
                   "c": base64.b64encode(ct).decode()}, f)
    os.replace(tmp, ruta)
    return True


def leer(ruta, pin):
    """Devuelve el dict o None si no existe / PIN incorrecto."""
    if not AESGCM or not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        sal, nonce, ct = (base64.b64decode(d[k]) for k in ("s", "n", "c"))
        return json.loads(AESGCM(_clave(pin, sal)).decrypt(nonce, ct, b"mainbit1"))
    except Exception:
        return None
