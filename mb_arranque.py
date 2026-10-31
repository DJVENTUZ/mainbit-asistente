"""Servidor de arranque: la PC nueva comparte la app por HTTP para que la PC vieja la obtenga por el cable
(así no hace falta mover la USB entre varias migraciones). Nunca incluye datos de servicio."""
import http.server
import io
import os
import threading
import zipfile

PORT_ARRANQUE = 50010
EXCLUIR = ("credenciales.ini", "secretos.json", "usb_datos", "relacion", "evidencias", "cierres",
           "reportes", "diagnosticos", "config_mainbit.json", "respaldos")


def _excluido(rel):
    p = rel.lower().replace("\\", "/")
    return any(x in p for x in EXCLUIR)


def zip_de_app(carpeta):
    """ZIP en memoria con raíz 'MainbitAsistente/'."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, _, archivos in os.walk(carpeta):
            for a in archivos:
                ruta = os.path.join(raiz, a)
                rel = os.path.relpath(ruta, carpeta)
                if _excluido(rel):
                    continue
                z.write(ruta, "MainbitAsistente/" + rel.replace("\\", "/"))
    return buf.getvalue()


def iniciar(carpeta, puerto=PORT_ARRANQUE):
    datos = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/app.zip":
                self.send_error(404)
                return
            if "z" not in datos:
                datos["z"] = zip_de_app(carpeta)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(datos["z"])))
            self.end_headers()
            self.wfile.write(datos["z"])

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", puerto), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def comando_vieja(ip, puerto=PORT_ARRANQUE):
    """Línea de PowerShell para pegar en la PC vieja."""
    return (f"$d=\"$env:USERPROFILE\\Desktop\\MB.zip\"; iwr http://{ip}:{puerto}/app.zip -OutFile $d; "
            f"Expand-Archive $d \"$env:USERPROFILE\\Desktop\" -Force; "
            f"start \"$env:USERPROFILE\\Desktop\\MainbitAsistente\\MainbitAsistente.exe\"")
