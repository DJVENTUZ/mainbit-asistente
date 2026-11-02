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


PAGINA = """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mainbit · PC vieja</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px;color:#0b1b33;
 font:17px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
 background:radial-gradient(1000px 600px at 12% 8%,#8fc7ff 0,transparent 60%),radial-gradient(900px 600px at 92% 92%,#b9a7ff 0,transparent 60%),#e8f1ff}
.card{width:100%;max-width:660px;padding:38px 40px;border-radius:30px;background:rgba(255,255,255,.58);
 backdrop-filter:blur(28px) saturate(1.6);-webkit-backdrop-filter:blur(28px) saturate(1.6);
 border:1px solid rgba(255,255,255,.8);box-shadow:0 30px 80px rgba(20,60,140,.22)}
.logo{display:flex;align-items:center;gap:12px;font-weight:600;color:#0a84ff;letter-spacing:.2px}
.logo i{width:34px;height:34px;border-radius:10px;background:linear-gradient(145deg,#4ab0ff,#0a60ff);display:grid;place-items:center;color:#fff;font-style:normal;font-weight:700}
h1{font-size:34px;font-weight:600;letter-spacing:-.6px;margin:18px 0 6px}
.sub{color:#4a5d7a;margin:0 0 26px}
.paso{display:flex;gap:16px;align-items:flex-start;margin:0 0 16px}
.n{flex:none;width:32px;height:32px;border-radius:50%;background:rgba(10,132,255,.14);color:#0a6cdc;display:grid;place-items:center;font-weight:600}
.btn{display:flex;align-items:center;justify-content:center;gap:10px;margin:28px 0 14px;padding:19px 30px;border-radius:999px;
 background:linear-gradient(180deg,#3da0ff,#0a74f0);color:#fff;text-decoration:none;font-weight:600;font-size:19px;
 box-shadow:0 12px 30px rgba(10,116,240,.45),inset 0 1px 0 rgba(255,255,255,.5);transition:transform .2s}
.btn:hover{transform:translateY(-2px)}
.nota{font-size:14px;color:#5b6b85;margin:0}
details{margin-top:20px;font-size:14px;color:#445873}
summary{cursor:pointer;color:#0a6cdc;font-weight:600}
code{display:block;margin-top:10px;padding:12px 14px;border-radius:14px;background:rgba(10,30,70,.07);word-break:break-all;font:12.5px Consolas,monospace}
</style>
<body><div class="card">
<div class="logo"><i>M</i>Mainbit Toolkit</div>
<h1>Preparar esta PC (vieja)</h1>
<p class="sub">Tres pasos y ya. El resto lo hace el programa solo.</p>
<div class="paso"><span class="n">1</span><div>Pulsa el botón azul de abajo. Se descarga un archivo pequeño.</div></div>
<div class="paso"><span class="n">2</span><div>Ábrelo con doble clic y acepta el aviso de administrador.</div></div>
<div class="paso"><span class="n">3</span><div>El programa se abre y busca la PC nueva. Cuando diga <b>«PC nueva encontrada»</b>, pulsa <b>Enviar</b>.</div></div>
<a class="btn" href="/iniciar.bat">⬇ Descargar e iniciar</a>
<p class="nota">Si Windows avisa que el archivo es poco común: elige «Conservar» y después «Ejecutar de todos modos».</p>
<details><summary>¿No abre? Hazlo a mano</summary>Abre PowerShell como administrador y pega:<code>{CMD}</code></details>
</div></body></html>"""


def iniciar(carpeta, puerto=PORT_ARRANQUE):
    datos = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ruta = self.path.split("?")[0]
            hh = (self.headers.get("Host") or "").split(":")
            host = hh[0]
            pto = int(hh[1]) if len(hh) > 1 and hh[1].isdigit() else puerto
            if ruta in ("/", "/index.html"):
                cuerpo = PAGINA.replace("{CMD}", comando_vieja(host, pto).replace("&", "&amp;").replace("<", "&lt;")).encode("utf-8")
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


def url_base(ip, puerto):
    return f"http://{ip}" if int(puerto) == 80 else f"http://{ip}:{puerto}"


def iniciar_auto(carpeta):
    """Puerto 80 (la dirección es solo la IP, fácil de escribir); si está ocupado, el 50010. Devuelve (servidor, puerto)."""
    try:
        return iniciar(carpeta, 80), 80
    except OSError:
        return iniciar(carpeta, PORT_ARRANQUE), PORT_ARRANQUE


def comando_vieja(ip, puerto=PORT_ARRANQUE):
    """Línea de PowerShell para pegar en la PC vieja."""
    return (f"$d=\"$env:USERPROFILE\\Desktop\\MB.zip\"; iwr {url_base(ip, puerto)}/app.zip -OutFile $d; "
            f"Expand-Archive $d \"$env:USERPROFILE\\Desktop\" -Force; "
            f"Start-Process \"$env:USERPROFILE\\Desktop\\MainbitAsistente\\MainbitAsistente.exe\" -Verb RunAs")
