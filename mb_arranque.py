"""Servidor de arranque: la PC nueva comparte la app por HTTP para que la PC vieja la obtenga por el cable
(así no hace falta mover la USB entre varias migraciones). Nunca incluye datos de servicio."""
import http.server
import io
import os
import threading
import zipfile

PORT_ARRANQUE = 50010
EXCLUIR = ("credenciales.ini", "secretos.json", "usb_datos", "relacion", "evidencias", "cierres",
           "reportes", "diagnosticos", "config_mainbit.json", "respaldos", "instaladores", "tipografias")


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
        z.writestr("MainbitAsistente/modo_vieja.flag", "1")      # la app sabe que es la PC vieja y busca sola a la nueva
    return buf.getvalue()


PAGINA = """<!doctype html><meta charset="utf-8"><title>Mainbit</title>
<body style="font:20px Segoe UI,sans-serif;max-width:640px;margin:40px auto;color:#10203a">
<h1>Mainbit · PC vieja</h1>
<p><b>1.</b> Pulsa el botón azul. Se descarga un archivo.</p>
<p><b>2.</b> Ábrelo (doble clic) y acepta el aviso de administrador.</p>
<p><b>3.</b> El programa se abre solo y busca la PC nueva. Cuando diga «PC nueva encontrada», pulsa <b>Enviar a la PC nueva</b>.</p>
<p><a href="/iniciar.bat" style="display:inline-block;background:#0a84ff;color:#fff;padding:18px 34px;border-radius:40px;text-decoration:none;font-weight:700">Descargar e iniciar</a></p>
<p style="font-size:14px;color:#566">Si Windows avisa que el archivo es poco común, elige «Conservar» y «Ejecutar de todos modos».</p></body>"""


def iniciar(carpeta, puerto=PORT_ARRANQUE):
    datos = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ruta = self.path.split("?")[0]
            hh = (self.headers.get("Host") or "").split(":")
            host = hh[0]
            pto = int(hh[1]) if len(hh) > 1 and hh[1].isdigit() else puerto
            if ruta in ("/", "/index.html"):
                cuerpo = PAGINA.replace("{HOST}", host).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)
                return
            if ruta == "/iniciar.bat":
                cuerpo = ("@echo off\r\n" + 'powershell -NoProfile -ExecutionPolicy Bypass -Command "' +
                          comando_vieja(host, pto).replace('"', '\\"') + '"\r\n').encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="INICIAR_MAINBIT.bat"')
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)
                return
            if ruta != "/app.zip":
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
            f"Start-Process \"$env:USERPROFILE\\Desktop\\MainbitAsistente\\MainbitAsistente.exe\" -Verb RunAs")
