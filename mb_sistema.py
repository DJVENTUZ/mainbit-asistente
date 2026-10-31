# -*- coding: utf-8 -*-
"""Funciones del sistema Windows: hardware, red, WiFi, dominio, tipografías,
IVANTI, políticas, impresora y restauración.

Todo está escrito para NO depender de internet ni de PowerShell cuando es posible
(las políticas del dominio a veces lo bloquean): serie y modelo salen de SMBIOS,
el estado del dominio de la API NetGetJoinInformation, etc.
"""
import json
import base64
import ctypes
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from mb_util import IS_WIN, dir_datos, escribir_json, leer_json, ultimos5

CREATE_NO_WINDOW = 0x08000000
DEMO = os.environ.get("MAINBIT_DEMO") == "1"      # solo para pruebas fuera de Windows


# ======================================================================
# Ejecución de comandos
# ======================================================================
def _dec(b):
    if not b:
        return ""
    for enc in ("utf-8", "cp850", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def run(cmd, timeout=60, env=None, entrada=None):
    """Ejecuta un comando sin ventana. Devuelve (código, salida, error)."""
    kw = {}
    if IS_WIN:
        kw["creationflags"] = CREATE_NO_WINDOW
    e = None
    if env:
        e = dict(os.environ)
        e.update(env)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=e, input=entrada, **kw)
        return r.returncode, _dec(r.stdout), _dec(r.stderr)
    except subprocess.TimeoutExpired:
        return -1, "", "Tiempo de espera agotado"
    except OSError as ex:
        return -2, "", str(ex)


def ps(script, env=None, timeout=60):
    """PowerShell por -EncodedCommand: sin problemas de comillas y NO le afecta
    la política de ejecución de scripts (solo aplica a archivos .ps1)."""
    if not IS_WIN:
        return -2, "", "PowerShell solo existe en Windows"
    pre = "try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8}catch{}\n"
    enc = base64.b64encode((pre + script).encode("utf-16-le")).decode("ascii")
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", enc], timeout=timeout, env=env)


def ps_q(valor):
    """Escapa una cadena para ponerla entre comillas simples en PowerShell."""
    return "'" + str(valor).replace("'", "''") + "'"


def es_admin():
    if not IS_WIN:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relanzar_como_admin(argumentos=""):
    """Pide elevación (UAC) y reabre el programa. Devuelve True si se lanzó."""
    if not IS_WIN:
        return False
    try:
        import sys
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, argumentos
        else:
            exe, params = sys.executable, f'"{os.path.abspath(sys.argv[0])}" {argumentos}'
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return r > 32
    except Exception:
        return False


def evitar_suspension(activar):
    if not IS_WIN:
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if activar else 0x80000000)
    except Exception:
        pass


def proceso_activo(nombre_exe):
    """True si hay un proceso con ese nombre (p. ej. OUTLOOK.EXE)."""
    if not IS_WIN:
        return False
    rc, out, _ = run(["tasklist", "/FI", f"IMAGENAME eq {nombre_exe}", "/FO", "CSV", "/NH"], timeout=20)
    return nombre_exe.lower() in out.lower()


def sesiones_activas():
    """Líneas de `query user` (sesiones abiertas, incluida la tuya)."""
    if not IS_WIN:
        return []
    rc, out, _ = run(["query", "user"], timeout=20)
    return [ln.strip() for ln in out.splitlines()[1:] if ln.strip()]


# ======================================================================
# SMBIOS (serie, modelo, tipo de equipo) — sin PowerShell
# ======================================================================
SERIE_BASURA = {"", "to be filled by o.e.m.", "to be filled by oem", "default string", "none",
                "system serial number", "0", "n/a", "na", "not applicable", "unknown", "o.e.m.",
                "not specified", "00000000", "123456789", "base board serial number",
                "chassis serial number", "serial number", "default"}
LAPTOP_CHASIS = {8, 9, 10, 11, 14, 30, 31, 32}


def serie_valida(s):
    s = (s or "").strip()
    return s.lower() not in SERIE_BASURA and len(set(s)) > 1


def smbios_parse(raw):
    """Interpreta el bloque RawSMBIOSData. Devuelve dict con los datos útiles."""
    if len(raw) < 8:
        return {}
    largo = struct.unpack_from("<I", raw, 4)[0]
    data = raw[8:8 + largo]
    out = {}
    i = 0
    while i + 4 <= len(data):
        t, ln = data[i], data[i + 1]
        if ln < 4 or i + ln > len(data):
            break
        area = data[i:i + ln]
        j = i + ln
        textos = []
        try:
            if data[j:j + 2] == b"\0\0":
                j += 2
            else:
                while True:
                    e = data.index(b"\0", j)
                    textos.append(data[j:e].decode("utf-8", "replace"))
                    j = e + 1
                    if data[j:j + 1] == b"\0":
                        j += 1
                        break
        except ValueError:
            break

        def S(off):
            if off >= len(area):
                return ""
            idx = area[off]
            return textos[idx - 1].strip() if 1 <= idx <= len(textos) else ""

        if t == 0 and "bios_fab" not in out:
            out["bios_fab"], out["bios_ver"] = S(4), S(5)
        elif t == 1 and "fabricante" not in out:
            out["fabricante"], out["modelo"], out["version"], out["serie"] = S(4), S(5), S(6), S(7)
        elif t == 2 and "placa_serie" not in out:
            out["placa_fab"], out["placa_modelo"], out["placa_serie"] = S(4), S(5), S(7)
        elif t == 3 and "chasis" not in out:
            out["chasis"] = area[5] & 0x7F if len(area) > 5 else 0
            out["chasis_serie"] = S(7)
        if t == 127:
            break
        i = j
    return out


def smbios_leer():
    if not IS_WIN:
        return {}
    try:
        k = ctypes.windll.kernel32
        sig = 0x52534D42  # 'RSMB'
        n = k.GetSystemFirmwareTable(sig, 0, None, 0)
        if not n:
            return {}
        buf = ctypes.create_string_buffer(n)
        k.GetSystemFirmwareTable(sig, 0, buf, n)
        return smbios_parse(buf.raw[:n])
    except Exception:
        return {}


# ======================================================================
# Monitores (EDID)
# ======================================================================
def edid_parse(e):
    """Interpreta un bloque EDID de 128 bytes. Devuelve dict o None."""
    if len(e) < 128 or e[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    v = (e[8] << 8) | e[9]
    fab = "".join(chr(64 + ((v >> s) & 31)) for s in (10, 5, 0))
    producto = struct.unpack_from("<H", e, 10)[0]
    num = struct.unpack_from("<I", e, 12)[0]
    anio = 1990 + e[17]
    nombre = serie_txt = ""
    otros = []
    for off in (54, 72, 90, 108):
        d = e[off:off + 18]
        if d[0:3] == b"\0\0\0":
            texto = d[5:18].split(b"\n")[0].decode("latin-1", "ignore").strip()
            if d[3] == 0xFC:
                nombre = texto
            elif d[3] == 0xFF:
                serie_txt = texto
            elif d[3] in (0xFE, 0xFD, 0xFA, 0xF9) and texto:
                otros.append("%02X:%s" % (d[3], texto))
    return {"fab": fab, "producto": "%04X" % producto, "serie": serie_txt or (str(num) if num else ""),
            "modelo": nombre, "anio": anio, "serie_txt": serie_txt, "serie_num": num,
            "semana": e[16], "otros": otros, "hex": bytes(e[:128]).hex()}


def monitores_registro():
    """Monitores vistos por Windows (incluye los que estuvieron conectados antes)."""
    res = []
    if not IS_WIN:
        return res
    try:
        import winreg
        raiz = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\DISPLAY")
        for i in range(winreg.QueryInfoKey(raiz)[0]):
            modelo = winreg.EnumKey(raiz, i)
            k1 = winreg.OpenKey(raiz, modelo)
            for j in range(winreg.QueryInfoKey(k1)[0]):
                inst = winreg.EnumKey(k1, j)
                try:
                    k2 = winreg.OpenKey(k1, inst + r"\Device Parameters")
                    edid, _ = winreg.QueryValueEx(k2, "EDID")
                    d = edid_parse(bytes(edid))
                    if d:
                        d["historico"] = True
                        d["instancia"] = modelo + "\\" + inst
                        res.append(d)
                except OSError:
                    pass
    except OSError:
        pass
    return res


def monitores_wmi():
    """Monitores activos ahora mismo (WmiMonitorID)."""
    script = r"""
function d($a){ if($a){ -join ($a | Where-Object {$_ -ne 0} | ForEach-Object {[char]$_}) } else { '' } }
Get-WmiObject -Namespace root\wmi -Class WmiMonitorID -ErrorAction SilentlyContinue | ForEach-Object {
  'MON|' + (d $_.SerialNumberID) + '|' + (d $_.UserFriendlyName) + '|' + (d $_.ManufacturerName) + '|' + $_.YearOfManufacture
}
"""
    rc, out, _ = ps(script, timeout=30)
    return parse_monitores_wmi(out)


def parse_monitores_wmi(texto):
    res = []
    for ln in texto.splitlines():
        if ln.startswith("MON|"):
            p = ln.rstrip("\r").split("|")
            p += [""] * (5 - len(p))
            res.append({"serie": p[1].strip(), "modelo": p[2].strip(), "fab": p[3].strip(),
                        "anio": p[4].strip(), "historico": False})
    return res


def monitores():
    m = monitores_wmi() if IS_WIN else []
    if not any(x["serie"] for x in m):
        reg = monitores_registro()
        if reg:
            m = reg
    return m


# ======================================================================
# Información del equipo
# ======================================================================
def nombre_host():
    return socket.gethostname()


def info_pc():
    """Todo lo que se necesita para la relación de equipos."""
    if DEMO:
        return {"host": "DESKTOP-DEMO01", "usuario": "Semarnat-ACEPP", "admin": True,
                "serie": "MZ043H47", "serie_fuente": "SMBIOS", "fabricante": "LENOVO",
                "modelo": "ThinkCentre M70s Gen 3", "chasis": 3, "perfil": "D2",
                "monitores": [{"serie": "V5XFMBW7", "modelo": "T22i-20", "fab": "LEN",
                               "anio": "2025", "historico": False}],
                "os": "Windows 11 Pro", "unido": False, "dominio": ""}
    sm = smbios_leer()
    serie = sm.get("serie", "")
    fuente = "SMBIOS"
    if not serie_valida(serie):
        for alt, f in ((sm.get("placa_serie", ""), "placa base"), (sm.get("chasis_serie", ""), "chasis")):
            if serie_valida(alt):
                serie, fuente = alt, f
                break
        else:
            serie, fuente = (serie if serie_valida(serie) else ""), "no detectada"
    if not serie and IS_WIN:      # respaldo: PowerShell/WMI
        rc, out, _ = ps("(Get-WmiObject Win32_BIOS).SerialNumber", timeout=20)
        s = out.strip().splitlines()[0].strip() if out.strip() else ""
        if serie_valida(s):
            serie, fuente = s, "WMI"
    chasis = sm.get("chasis", 0)
    unido, dom = estado_dominio()
    return {"host": nombre_host(), "usuario": os.environ.get("USERNAME", ""), "admin": es_admin(),
            "serie": serie.strip(), "serie_fuente": fuente,
            "fabricante": sm.get("fabricante", ""), "modelo": sm.get("modelo", ""),
            "chasis": chasis, "perfil": "L2" if chasis in LAPTOP_CHASIS else "D2",
            "monitores": monitores(), "os": nombre_os(), "unido": unido, "dominio": dom}


def nombre_os():
    if not IS_WIN:
        return os.uname().sysname if hasattr(os, "uname") else "?"
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        nombre = winreg.QueryValueEx(k, "ProductName")[0]
        build = int(winreg.QueryValueEx(k, "CurrentBuild")[0])
        if build >= 22000 and "Windows 10" in nombre:
            nombre = nombre.replace("Windows 10", "Windows 11")
        return nombre
    except Exception:
        return "Windows"


def series_monitores(info):
    return ", ".join(m["serie"] for m in info.get("monitores", []) if m.get("serie"))


# ======================================================================
# Hostname
# ======================================================================
def hostname_propuesto(cfg, info, base=None):
    """124MD2 + últimos 5 de la serie (así lo tiene tu Tabla 2). Con guion: 124-M-D2-43H47"""
    perfil = cfg.get("perfil") or info.get("perfil") or "D2"
    u5 = ultimos5(info.get("serie", ""))
    if base is None:
        if cfg.get("guion"):
            base = f"{cfg['unidad']}-{cfg['empresa']}-{perfil}-"
        else:
            base = f"{cfg['unidad']}{cfg['empresa']}{perfil}"
    return (base + u5) if u5 else base


def hostname_valido(h):
    """Reglas NetBIOS: 1-15 caracteres, letras/números/guion, sin guion al inicio/fin, no solo números."""
    if not h or len(h) > 15:
        return False, "Debe tener entre 1 y 15 caracteres (tiene %d)" % len(h or "")
    if not re.fullmatch(r"[A-Za-z0-9-]+", h):
        return False, "Solo letras, números y guion medio"
    if h.startswith("-") or h.endswith("-"):
        return False, "No puede empezar ni terminar con guion"
    if h.isdigit():
        return False, "No puede ser solo números"
    return True, ""


# ======================================================================
# Red
# ======================================================================
def ips_locales():
    """[(ip, adaptador_o_vacio)] — todas las IPv4 locales."""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in [x for x in ips]:
                ips.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips and not ip.startswith("127."):
            ips.append(ip)
    except OSError:
        pass
    return sorted(ips)


def parse_adaptadores(texto):
    res = []
    for ln in texto.splitlines():
        if ln.startswith("NIC|"):
            p = ln.rstrip("\r").split("|") + [""] * 11
            res.append({"alias": p[1], "nombre": p[2], "estado": p[3], "mac": p[4],
                        "ips": [x for x in p[5].split(",") if x], "dhcp": p[6].lower() == "true",
                        "gateway": p[7], "mascara": p[8], "dns": p[9]})
    return res


def clasificar(a):
    n = (a["alias"] + " " + a["nombre"]).lower()
    if any(x in n for x in ("wi-fi", "wifi", "wireless", "wlan", "802.11", "inalám", "inalam")):
        return "wifi"
    if any(x in n for x in ("bluetooth", "virtual", "vpn", "hyper-v", "vmware", "vbox", "tap-", "loopback")):
        return "otro"
    return "cable"


def adaptadores():
    if DEMO:
        return [{"alias": "Ethernet", "nombre": "Realtek PCIe GbE", "estado": "2", "mac": "AA:BB:CC:00:11:22",
                 "ips": ["169.254.182.182"], "dhcp": True, "gateway": "", "mascara": "255.255.0.0", "dns": "",
                 "tipo": "cable"},
                {"alias": "Wi-Fi", "nombre": "MediaTek Wi-Fi 6E", "estado": "2", "mac": "AA:BB:CC:00:11:33",
                 "ips": ["10.110.8.44"], "dhcp": True, "gateway": "10.110.8.1", "mascara": "255.255.255.0",
                 "dns": "10.110.2.10", "tipo": "wifi"}]
    script = r"""
Get-WmiObject Win32_NetworkAdapter -Filter "PhysicalAdapter=True" | ForEach-Object {
  $c = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter ("Index=" + $_.DeviceID)
  'NIC|' + $_.NetConnectionID + '|' + $_.Name + '|' + $_.NetConnectionStatus + '|' + $_.MACAddress + '|' +
  ($c.IPAddress -join ',') + '|' + $c.DHCPEnabled + '|' + ($c.DefaultIPGateway -join ',') + '|' +
  ($c.IPSubnet -join ',') + '|' + ($c.DNSServerSearchOrder -join ',')
}
"""
    rc, out, _ = ps(script, timeout=40)
    res = parse_adaptadores(out)
    for a in res:
        a["tipo"] = clasificar(a)
        a["ips"] = [i for i in a["ips"] if "." in i]      # solo IPv4
    return res


def adaptador_cable(adaptadores_lista=None):
    lista = adaptadores_lista if adaptadores_lista is not None else adaptadores()
    cables = [a for a in lista if a["tipo"] == "cable"]
    conectados = [a for a in cables if a["estado"] == "2"]
    return (conectados or cables or [None])[0]


def describir_ips(lista=None):
    """Texto claro para mostrar al técnico qué IP es de cable y cuál de WiFi."""
    lista = lista if lista is not None else adaptadores()
    filas = []
    for a in lista:
        if a["ips"] and a["estado"] == "2":
            etiqueta = {"cable": "Cable", "wifi": "WiFi"}.get(a["tipo"], "Red")
            for ip in a["ips"]:
                filas.append((etiqueta, ip))
    if not filas:
        filas = [("Red", ip) for ip in ips_locales()]
    return filas


def tcp_abierto(host, puerto, timeout=3):
    try:
        s = socket.create_connection((host, puerto), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def sntp_desfase(host, puerto=123, timeout=3):
    """Segundos de diferencia entre el reloj del servidor y este equipo (+ = servidor adelantado)."""
    pkt = b"\x1b" + 47 * b"\0"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t1 = time.time()
        s.sendto(pkt, (host, puerto))
        data, _ = s.recvfrom(512)
        t4 = time.time()
    finally:
        s.close()
    secs, frac = struct.unpack("!II", data[40:48])
    ts = secs - 2208988800 + frac / 2 ** 32
    return ts - (t1 + t4) / 2


def verificar_red(dominio, adaptadores_lista=None):
    """Revisión previa a unir al dominio. Devuelve [(nombre, nivel, detalle)] y la IP del DC."""
    res, dc_ip = [], None
    if DEMO:
        return [("Red corporativa (DHCP)", "ok", "10.110.8.44 por WiFi"),
                ("DNS resuelve el dominio", "ok", f"{dominio} → 10.110.2.10"),
                ("Controlador de dominio responde", "ok", "puerto 389 abierto"),
                ("Hora sincronizada", "ok", "diferencia 1 s")], "10.110.2.10"
    lista = adaptadores_lista if adaptadores_lista is not None else adaptadores()
    if not (dominio or "").strip():
        return [("DNS resuelve el dominio", "err",
                 "El dominio está VACÍO: abre Ajustes y escribe dominio.semarnat.gob.mx (falta config_mainbit.json)")], None
    corp = [(a, ip) for a in lista if a["estado"] == "2" for ip in a["ips"]
            if not ip.startswith("169.254.") and not ip.startswith("192.168.77.")]
    if corp:
        a, ip = corp[0]
        res.append(("Red corporativa (DHCP)", "ok", f"{ip} por {a['alias']}"))
    else:
        res.append(("Red corporativa (DHCP)", "err",
                    "Sin IP corporativa (la 192.168.77.x/169.254.x.x es solo el cable directo): conecta el WiFi VIP_N y quita «Cable directo (IP fija)»"))
    try:
        dc_ip = socket.gethostbyname(dominio)
        if dc_ip.startswith("0.") or dc_ip.startswith("127."):
            raise OSError("respuesta inválida " + dc_ip)
        res.append(("DNS resuelve el dominio", "ok", f"{dominio} → {dc_ip}"))
    except OSError:
        res.append(("DNS resuelve el dominio", "err",
                    f"No resuelve {dominio}. Revisa que estés en la red corporativa (no en cable directo)"))
    if dc_ip:
        ok = [p for p in (389, 445, 88) if tcp_abierto(dc_ip, p, 3)]
        if ok:
            res.append(("Controlador de dominio responde", "ok", f"puerto {ok[0]} abierto"))
        else:
            res.append(("Controlador de dominio responde", "err", f"{dc_ip} no responde (389/445/88)"))
        try:
            d = sntp_desfase(dc_ip)
            if abs(d) > 240:
                res.append(("Hora sincronizada", "err",
                            f"El reloj difiere {int(d)} s del dominio (máx. 300). Pulsa «Sincronizar hora»"))
            else:
                res.append(("Hora sincronizada", "ok", f"diferencia {int(abs(d))} s"))
        except OSError:
            res.append(("Hora sincronizada", "warn", "No se pudo comprobar (no es bloqueante)"))
    return res, dc_ip


def sincronizar_hora(dc):
    log = []
    for cmd in (["net", "start", "w32time"],
                ["w32tm", "/config", f"/manualpeerlist:{dc}", "/syncfromflags:manual", "/update"],
                ["w32tm", "/resync", "/force"]):
        rc, out, err = run(cmd, timeout=30)
        log.append(f"{' '.join(cmd)} → {rc}")
    return log


# ---------------------------------------------------------------- WiFi
def wifi_perfil_xml(ssid, clave, wpa3=False):
    auth = "WPA3SAE" if wpa3 else "WPA2PSK"
    return ('<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
            f"<name>{xml_escape(ssid)}</name><SSIDConfig><SSID><name>{xml_escape(ssid)}</name></SSID></SSIDConfig>"
            "<connectionType>ESS</connectionType><connectionMode>auto</connectionMode><MSM><security>"
            f"<authEncryption><authentication>{auth}</authentication><encryption>AES</encryption>"
            "<useOneX>false</useOneX></authEncryption><sharedKey><keyType>passPhrase</keyType>"
            f"<protected>false</protected><keyMaterial>{xml_escape(clave)}</keyMaterial></sharedKey>"
            "</security></MSM></WLANProfile>")


def parse_wifi_interfaces(texto):
    """[(ssid, conectado)] de cada interfaz WiFi en la salida de `netsh wlan show interfaces` (ES/EN)."""
    res, ssid, estado = [], None, None
    def cerrar():
        if ssid is not None or estado is not None:
            res.append(((ssid or "").strip(), (estado or "").strip().lower() in ("conectado", "connected")))
    for ln in texto.splitlines():
        if re.match(r"\s*(Nombre|Name)\s*:", ln):          # empieza una interfaz nueva
            cerrar(); ssid, estado = None, None
            continue
        m = re.match(r"\s*(SSID|Estado|State)\s*:\s*(.*)$", ln)
        if m:
            if m.group(1) in ("Estado", "State"):
                estado = m.group(2)
            else:
                ssid = m.group(2)
    cerrar()
    return res


def wifi_conectado(ssid_esperado=None):
    """(ssid, conectado). Con ssid_esperado, busca entre todas las interfaces y compara sin mayúsculas."""
    rc, out, _ = run(["netsh", "wlan", "show", "interfaces"], timeout=15)
    ifs = parse_wifi_interfaces(out)
    if ssid_esperado:
        for s, ok in ifs:
            if ok and s.strip().lower() == ssid_esperado.strip().lower():
                return s, True
    for s, ok in ifs:
        if ok:
            return s, True
    return (ifs[0][0] if ifs else ""), False


def _esperar_wifi(ssid, segundos, log=None):
    fin = time.time() + segundos
    while time.time() < fin:
        s, ok = wifi_conectado(ssid)
        if ok and s.strip().lower() == ssid.strip().lower():
            return True
        time.sleep(2)
    return False


def wifi_conectar(ssid, clave, log=lambda t: None):
    """Crea el perfil WiFi y conecta. El archivo temporal con la clave se borra siempre."""
    if not IS_WIN:
        return False, "Solo en Windows"
    s, ok = wifi_conectado(ssid)
    if ok and s.strip().lower() == ssid.strip().lower():
        return True, f"Ya estaba conectado a {ssid}"
    for wpa3 in (False, True):
        tmp = os.path.join(tempfile.gettempdir(), "mb_wifi_%d.xml" % os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(wifi_perfil_xml(ssid, clave, wpa3))
            rc, out, err = run(["netsh", "wlan", "add", "profile", f"filename={tmp}", "user=all"], timeout=20)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if rc != 0:
            return False, (out + err).strip() or "No se pudo crear el perfil WiFi"
        run(["netsh", "wlan", "connect", f"name={ssid}"], timeout=20)
        # La primera conexión en un Windows recién instalado puede tardar: se espera hasta 45 s
        # ANTES de probar WPA3 (cambiar el perfil a medias cortaba una conexión que sí iba a funcionar).
        if _esperar_wifi(ssid, 45):
            return True, f"Conectado a {ssid}"
        log(f"WPA2 no conectó{', probando WPA3' if not wpa3 else ''}…")
    if _esperar_wifi(ssid, 15):
        return True, f"Conectado a {ssid}"
    return False, ("No se logró conectar. Revisa que la señal de %s esté disponible y la clave sea correcta" % ssid)


# ---------------------------------------------------------------- IP fija (cable directo)
ESTADO_RED = None


def _ruta_red_original():
    return os.path.join(dir_datos(), "red_original.json")


def ip_fija_aplicar(alias, ip, mascara="255.255.255.0"):
    """Pone IP fija en el cable. Guarda lo anterior para poder restaurarlo."""
    lista = adaptadores()
    a = next((x for x in lista if x["alias"] == alias), None)
    if a and not os.path.exists(_ruta_red_original()):
        escribir_json(_ruta_red_original(), a)
    rc, out, err = run(["netsh", "interface", "ip", "set", "address", f"name={alias}", "static", ip, mascara],
                       timeout=30)
    return rc == 0, (out + err).strip()


def ip_fija_pendiente():
    return os.path.exists(_ruta_red_original())


def ip_fija_restaurar():
    """Devuelve el adaptador a como estaba (DHCP o su IP fija original)."""
    orig = leer_json(_ruta_red_original())
    if not orig:
        return True, "Nada que restaurar"
    alias = orig["alias"]
    msgs = []
    if orig.get("dhcp", True) or not orig.get("ips"):
        r1 = run(["netsh", "interface", "ip", "set", "address", f"name={alias}", "dhcp"], timeout=30)
        r2 = run(["netsh", "interface", "ip", "set", "dnsservers", f"name={alias}", "source=dhcp"], timeout=30)
        ok = r1[0] == 0
        msgs.append("DHCP restaurado")
    else:
        cmd = ["netsh", "interface", "ip", "set", "address", f"name={alias}", "static", orig["ips"][0],
               (orig.get("mascara", "255.255.255.0").split(",")[0] or "255.255.255.0")]
        if orig.get("gateway"):
            cmd += [orig["gateway"].split(",")[0], "1"]
        r1 = run(cmd, timeout=30)
        ok = r1[0] == 0
        msgs.append("IP fija original restaurada")
        dns = [d for d in orig.get("dns", "").split(",") if d]
        for n, d in enumerate(dns):
            run(["netsh", "interface", "ip", "set" if n == 0 else "add", "dnsservers", f"name={alias}",
                 *(["static", d, "primary"] if n == 0 else [d])], timeout=30)
    if ok:
        try:
            os.remove(_ruta_red_original())
        except OSError:
            pass
    return ok, "; ".join(msgs) if ok else "No se pudo restaurar la red automáticamente"


# ---------------------------------------------------------------- firewall
def _netsh_fw(args):
    rc, _, _ = run(["netsh", "advfirewall", "firewall"] + args, timeout=20)
    return rc == 0


def abrir_firewall(p_tcp, p_udp, p_extra=None):
    if not IS_WIN or not es_admin():
        return False
    if p_extra:
        _netsh_fw(["add", "rule", "name=Mainbit Asistente ARRANQUE", "dir=in", "action=allow",
                   "protocol=TCP", f"localport={p_extra}", "profile=any"])
    a = _netsh_fw(["add", "rule", "name=Mainbit Asistente TCP", "dir=in", "action=allow",
                   "protocol=TCP", f"localport={p_tcp}", "profile=any"])
    b = _netsh_fw(["add", "rule", "name=Mainbit Asistente UDP", "dir=in", "action=allow",
                   "protocol=UDP", f"localport={p_udp}", "profile=any"])
    return a and b


def cerrar_firewall():
    if IS_WIN and es_admin():
        _netsh_fw(["delete", "rule", "name=Mainbit Asistente TCP"])
        _netsh_fw(["delete", "rule", "name=Mainbit Asistente UDP"])
        _netsh_fw(["delete", "rule", "name=Mainbit Asistente ARRANQUE"])


# ======================================================================
# Dominio
# ======================================================================
def estado_dominio():
    """(unido_a_dominio, nombre). Usa la API de Windows, no PowerShell."""
    if IS_WIN:
        try:
            buf = ctypes.c_void_p()
            st = ctypes.c_int()
            r = ctypes.windll.netapi32.NetGetJoinInformation(None, ctypes.byref(buf), ctypes.byref(st))
            if r == 0:
                nombre = ctypes.wstring_at(buf.value) if buf.value else ""
                ctypes.windll.netapi32.NetApiBufferFree(buf)
                return st.value == 3, nombre
        except Exception:
            pass
    return False, ""


def usuario_completo(usuario, dominio):
    """MAINBIT → MAINBIT@dominio.semarnat.gob.mx (UPN); respeta DOMINIO\\usuario y usuario@dominio."""
    u = usuario.strip()
    if "\\" in u or "@" in u:
        return u
    return f"{u}@{dominio}"


TRADUCCIONES = [
    (r"(user name or password is incorrect|nombre de usuario o la contraseña|nombre de usuario o contraseña|1326|logon failure)",
     "Usuario o contraseña de dominio incorrectos. Verifica el usuario (prueba también SEMARNAT\\MAINBIT) y la contraseña. "
     "No reintentes muchas veces: el dominio puede bloquear la cuenta."),
    (r"(domain .* does not exist|could not be contacted|no existe o no se pudo contactar|no se pudo establecer contacto|1355)",
     "No se encontró el dominio. El equipo debe estar en la red corporativa (WiFi VIP_N o cable a la red) y no solo en el cable directo."),
    (r"(network path was not found|no se encontr[oó] la ruta de acceso de red|\b53\b)",
     "No hay ruta de red al dominio. Revisa la conexión a la red corporativa."),
    (r"(account already exists|la cuenta ya existe|ya existe una cuenta|2224)",
     "Ya existe una cuenta de equipo con ese nombre en el dominio. Pide al administrador que la elimine o usa otro nombre."),
    (r"(locked out|bloquead)", "La cuenta de dominio está bloqueada. Espera o avisa al administrador."),
    (r"(clock|time difference|diferencia de hora|skew)",
     "El reloj del equipo difiere del dominio. Pulsa «Sincronizar hora» y reintenta."),
    (r"(access is denied|acceso denegado|5\b)",
     "Acceso denegado: abre el programa como administrador o la cuenta no tiene permiso para unir equipos."),
    (r"(computer name .* not valid|nombre del equipo no es v[aá]lido|not a valid computer name)",
     "El nombre del equipo no es válido (máx. 15 caracteres, solo letras, números y guion)."),
    (r"(already in that domain|ya est[aá] en ese dominio)", "El equipo ya pertenece a ese dominio."),
]


def traducir_error_dominio(msg):
    for patron, texto in TRADUCCIONES:
        if re.search(patron, msg, re.I):
            return texto
    return "No se pudo completar la operación."


def unir_dominio(dominio, usuario, clave, nuevo_nombre=None):
    """Renombra (si hace falta) y une al dominio con Add-Computer. Devuelve (ok, mensaje).
    La contraseña viaja por variable de entorno del proceso hijo: no queda en la línea de comandos."""
    env = {"MB_USER": usuario_completo(usuario, dominio), "MB_PASS": clave, "MB_DOM": dominio,
           "MB_NAME": nuevo_nombre or ""}
    script = r"""
$ErrorActionPreference = 'Stop'
try {
  $sec = ConvertTo-SecureString $env:MB_PASS -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential($env:MB_USER, $sec)
  $p = @{ DomainName = $env:MB_DOM; Credential = $cred; Force = $true }
  if ($env:MB_NAME -and ($env:MB_NAME -ne $env:COMPUTERNAME)) { $p['NewName'] = $env:MB_NAME }
  Add-Computer @p
  'MB_OK'
} catch { 'MB_ERR|' + $_.Exception.Message }
"""
    rc, out, err = ps(script, env=env, timeout=240)
    return parse_resultado_union(out, err)


def parse_resultado_union(out, err=""):
    if "MB_OK" in out:
        return True, "El equipo se unió al dominio. Reinicia para aplicar los cambios."
    m = re.search(r"MB_ERR\|(.*)", out, re.S)
    crudo = (m.group(1).strip() if m else (out + err).strip()) or "Sin detalle"
    return False, f"{traducir_error_dominio(crudo)}\n\nDetalle técnico: {crudo[:400]}"


def renombrar_equipo(nombre):
    rc, out, err = ps("try { Rename-Computer -NewName %s -Force -ErrorAction Stop; 'MB_OK' } "
                      "catch { 'MB_ERR|' + $_.Exception.Message }" % ps_q(nombre), timeout=60)
    if "MB_OK" in out:
        return True, "Nombre cambiado. Se aplica al reiniciar."
    m = re.search(r"MB_ERR\|(.*)", out, re.S)
    return False, (m.group(1).strip() if m else (out + err).strip()) or "No se pudo renombrar"


def reiniciar(segundos=20, motivo="Mainbit Asistente: reinicio para aplicar cambios"):
    return run(["shutdown", "/r", "/t", str(int(segundos)), "/c", motivo], timeout=15)[0] == 0


def cancelar_reinicio():
    return run(["shutdown", "/a"], timeout=15)[0] == 0


def abrir_sysdm():
    try:
        subprocess.Popen(["control.exe", "sysdm.cpl"], creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
        return True
    except OSError:
        return False


# ======================================================================
# Tipografías (Noto Sans ×72 + Patria ×3)
# ======================================================================
EXT_FUENTES = (".ttf", ".otf", ".ttc")


def dir_fuentes_windows():
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def buscar_dir_fuentes(extra_dirs=()):
    """Busca la carpeta «Tipografías» en los sitios habituales."""
    nombres = ("Tipografías", "Tipografias", "tipografías", "Fuentes", "Fonts")
    bases = list(extra_dirs)
    if IS_WIN:
        bases += [r"C:\Users\Public\Desktop", "C:\\"]
        try:
            for u in os.listdir(r"C:\Users"):
                bases.append(os.path.join(r"C:\Users", u, "Desktop"))
        except OSError:
            pass
    bases.append(os.path.join(os.path.expanduser("~"), "Desktop"))
    vistos = set()
    for b in bases:
        for n in nombres:
            p = os.path.join(b, n)
            if p.lower() in vistos:
                continue
            vistos.add(p.lower())
            if os.path.isdir(p) and listar_fuentes(p):
                return p
    return ""


def listar_fuentes(carpeta):
    res = []
    for raiz, _d, files in os.walk(carpeta):
        for f in files:
            if f.lower().endswith(EXT_FUENTES):
                res.append(os.path.join(raiz, f))
    return sorted(res)


def contar_fuentes(archivos):
    noto = sum(1 for f in archivos if "noto" in os.path.basename(f).lower())
    patria = sum(1 for f in archivos if "patria" in os.path.basename(f).lower())
    return noto, patria


def nombre_fuente(path):
    """Nombre completo de la fuente (nameID 4) leyendo solo la tabla 'name' del archivo."""
    try:
        with open(path, "rb") as f:
            cab = f.read(12)
            base = 0
            if cab[:4] == b"ttcf":                      # colección .ttc: primera fuente
                f.seek(12)
                base = struct.unpack(">I", f.read(4))[0]
                f.seek(base)
                cab = f.read(12)
            n_tablas = struct.unpack_from(">H", cab, 4)[0]
            f.seek(base + 12)
            dirtab = f.read(16 * n_tablas)
            nm = None
            for i in range(n_tablas):
                tag, _c, off, ln = struct.unpack_from(">4sIII", dirtab, 16 * i)
                if tag == b"name":
                    nm = (off, ln)
                    break
            if not nm:
                return ""
            f.seek(nm[0])
            d = f.read(min(nm[1], 1 << 20))
        cuenta, so = struct.unpack_from(">HH", d, 2)
        mejor = ""
        for i in range(cuenta):
            pid, eid, lid, nid, ln2, o2 = struct.unpack_from(">HHHHHH", d, 6 + 12 * i)
            if nid != 4:
                continue
            raw = d[so + o2: so + o2 + ln2]
            if pid == 3:
                return raw.decode("utf-16-be", "ignore")
            if pid == 1 and not mejor:
                mejor = raw.decode("latin-1", "ignore")
        return mejor
    except Exception:
        return ""


def fuente_instalada(src):
    return os.path.exists(os.path.join(dir_fuentes_windows(), os.path.basename(src)))


def instalar_fuentes(archivos, progreso=lambda i, n, nombre: None, cancelar=lambda: False):
    """Instala para TODOS los usuarios (copia a C:\\Windows\\Fonts + registro HKLM).
    Devuelve (instaladas, ya_estaban, fallidas[(archivo, motivo)])."""
    if not IS_WIN:
        return 0, 0, [("", "Solo en Windows")]
    import winreg
    destino = dir_fuentes_windows()
    ok = ya = 0
    fallos = []
    clave = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", 0,
                               winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    gdi = ctypes.windll.gdi32
    for i, src in enumerate(archivos):
        if cancelar():
            break
        base = os.path.basename(src)
        progreso(i, len(archivos), base)
        try:
            dst = os.path.join(destino, base)
            existia = os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)
            if not existia:
                shutil.copy2(src, dst)
            nombre = nombre_fuente(src) or os.path.splitext(base)[0]
            tipo = "OpenType" if base.lower().endswith(".otf") else "TrueType"
            winreg.SetValueEx(clave, f"{nombre} ({tipo})", 0, winreg.REG_SZ, base)
            if not existia:
                gdi.AddFontResourceW(dst)
                ok += 1
            else:
                ya += 1
        except Exception as e:
            fallos.append((base, str(e)))
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 2, 1000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass
    return ok, ya, fallos


# ======================================================================
# IVANTI y políticas
# ======================================================================
def parse_servicios(texto):
    res = []
    for ln in texto.splitlines():
        if ln.startswith("SVC|"):
            p = ln.rstrip("\r").split("|") + [""] * 4
            res.append({"nombre": p[1], "visible": p[2], "estado": p[3]})
    return res


def ivanti_estado(cfg):
    base = cfg["ivanti_dir"]
    if not os.path.isdir(base):
        pf = os.environ.get("ProgramFiles(x86)")
        if pf and os.path.isdir(os.path.join(pf, "Ivanti")):
            base = os.path.join(pf, "Ivanti")
    res = {"existe": os.path.isdir(base), "ruta": base, "items": 0, "servicios": []}
    epm = os.path.join(base, "EPM Agent")
    try:
        if os.path.isdir(epm):
            res["items"] = len(os.listdir(epm))
    except OSError:
        pass
    if IS_WIN:
        rc, out, _ = ps(r"Get-Service | Where-Object { $_.DisplayName -match 'ivanti|landesk|ldms' -or "
                        r"$_.Name -match 'ivanti|landesk|ldms' } | ForEach-Object "
                        r"{ 'SVC|' + $_.Name + '|' + $_.DisplayName + '|' + $_.Status }", timeout=30)
        res["servicios"] = parse_servicios(out)
    return res


def gpo_nombres_de_xml(xml_texto):
    """Nombres de GPO aplicadas al equipo desde la salida XML de gpresult."""
    raiz = ET.fromstring(xml_texto)
    comp = None
    for el in raiz.iter():
        if el.tag.split("}")[-1] == "ComputerResults":
            comp = el
            break
    nombres = []
    if comp is not None:
        for g in comp.iter():
            if g.tag.split("}")[-1] == "GPO":
                for h in g:
                    if h.tag.split("}")[-1] == "Name" and h.text:
                        nombres.append(h.text.strip())
    return list(dict.fromkeys(nombres))


def actualizar_politicas():
    """gpupdate /force y devuelve (ok, [GPO aplicadas])."""
    rc, out, err = run(["gpupdate", "/force"], timeout=240, entrada=b"n\n")
    tmp = os.path.join(tempfile.gettempdir(), "mb_gpresult.xml")
    rc2, out2, err2 = run(["gpresult", "/scope", "computer", "/x", tmp, "/f"], timeout=120)
    nombres = []
    try:
        with open(tmp, "r", encoding="utf-8", errors="ignore") as f:
            nombres = gpo_nombres_de_xml(f.read())
    except (OSError, ET.ParseError):
        pass
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return rc == 0, nombres


# ======================================================================
# Impresora
# ======================================================================
def impresora_servidor(ruta):
    m = re.match(r"\\\\([^\\]+)\\", ruta)
    return m.group(1) if m else ""


def impresora_existe(ruta):
    nombre = ruta.rsplit("\\", 1)[-1]
    rc, out, _ = ps("Get-Printer | Where-Object { $_.Name -like %s } | ForEach-Object { 'PRN|' + $_.Name }"
                    % ps_q("*" + nombre + "*"), timeout=30)
    return "PRN|" in out


def conectar_impresora(ruta):
    """Conecta la impresora de red (por usuario). Puede pedir credenciales de administrador."""
    srv = impresora_servidor(ruta)
    if srv and not tcp_abierto(srv, 445, 3):
        return False, f"No hay conexión con {srv} (¿estás en la red corporativa?)"
    rc, out, err = ps("try { Add-Printer -ConnectionName %s -ErrorAction Stop; 'MB_OK' } "
                      "catch { 'MB_ERR|' + $_.Exception.Message }" % ps_q(ruta), timeout=180)
    if "MB_OK" in out:
        return True, "Impresora conectada"
    rc, out2, err2 = run(["rundll32", "printui.dll,PrintUIEntry", "/in", "/n" + ruta], timeout=180)
    time.sleep(3)
    if impresora_existe(ruta):
        return True, "Impresora conectada"
    m = re.search(r"MB_ERR\|(.*)", out, re.S)
    return False, ("No se pudo conectar automáticamente"
                   + (f": {m.group(1).strip()[:200]}" if m else "")
                   + ". Usa Inicio+R → " + ruta.rsplit("\\", 1)[0] + " y «Conectar».")


# ======================================================================
# Restauración al perfil del usuario (robocopy)
# ======================================================================
CARPETAS_PERFIL = ["Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "Favorites"]
PERFILES_SISTEMA = {"default", "default user", "all users", "public", "defaultuser0", "desktop.ini"}


def perfiles_locales():
    base = os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "Users")
    try:
        return sorted(d for d in os.listdir(base)
                      if d.lower() not in PERFILES_SISTEMA and os.path.isdir(os.path.join(base, d)))
    except OSError:
        return []


def robocopy(src, dst, log=lambda t: None, cancelar=lambda: False):
    """Copia sin pisar archivos más nuevos. Devuelve (ok, código)."""
    cmd = ["robocopy", src, dst, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/XJ", "/XO",
           "/NP", "/NDL", "/NJH", "/NJS", "/MT:16"]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
    except OSError as e:
        log(f"robocopy no disponible: {e}")
        return False, -2
    for ln in iter(p.stdout.readline, b""):
        if cancelar():
            p.kill()
            return False, -3
        t = _dec(ln).strip()
        if t:
            log(t)
    p.wait()
    return p.returncode < 8, p.returncode


def monitores_diagnostico():
    """Texto con TODO lo que Windows sabe de cada monitor (para comparar con la etiqueta física)."""
    lin = ["DIAGNÓSTICO DE MONITORES", "Equipo: " + nombre_host(), ""]
    act = monitores_wmi() if IS_WIN else []
    lin.append("== Monitores ACTIVOS ahora (WMI) ==")
    for m in act or [{}]:
        lin.append("  " + (json.dumps(m, ensure_ascii=False) if m else "(ninguno)"))
    lin += ["", "== Todos los monitores que Windows recuerda (registro EDID) =="]
    for m in monitores_registro():
        lin.append(f"- Instancia: {m.get('instancia', '')}")
        lin.append(f"    Fabricante: {m['fab']}  Producto: {m['producto']}  Modelo: {m['modelo']}")
        lin.append(f"    Serie (texto 0xFF): {m.get('serie_txt') or '(vacío)'}")
        lin.append(f"    Serie numérica: {m.get('serie_num')} (hex {int(m.get('serie_num') or 0):08X})")
        lin.append(f"    Fabricado: semana {m.get('semana')} de {m['anio']}   Otros campos: {m.get('otros') or '-'}")
        lin.append(f"    EDID: {m.get('hex')}")
    lin += ["", "Anota la serie de la ETIQUETA física de cada monitor junto a este archivo y envíalo."]
    return "\n".join(lin)


# ======================================================================
# Impresoras: leer en la PC vieja y clonar en la nueva (nunca borra nada)
# ======================================================================
_IGNORAR_IMPRESORAS = ("microsoft print to pdf", "microsoft xps", "onenote", "fax",
                       "send to onenote", "adobe pdf", "pdf creator")


def listar_impresoras():
    """Impresoras de la PC: [{nombre, driver, puerto, host, compartida, recurso, red, predeterminada}]."""
    if not IS_WIN:
        return []
    script = (
        "$d=(Get-CimInstance Win32_Printer | Where-Object {$_.Default}).Name;"
        "$p=@{};Get-PrinterPort | ForEach-Object {$p[$_.Name]=$_.PrinterHostAddress};"
        "Get-Printer | ForEach-Object { [pscustomobject]@{"
        "nombre=$_.Name;driver=$_.DriverName;puerto=$_.PortName;host=$p[$_.PortName];"
        "compartida=[bool]$_.Shared;recurso=$_.ShareName;tipo=[string]$_.Type;"
        "predeterminada=($_.Name -eq $d)} } | ConvertTo-Json -Compress")
    rc, out, _ = ps(script, timeout=60)
    out = (out or "").strip()
    if not out:
        return []
    try:
        datos = json.loads(out)
    except ValueError:
        return []
    if isinstance(datos, dict):
        datos = [datos]
    res = []
    for d in datos:
        nom = str(d.get("nombre") or "")
        if not nom or any(x in nom.lower() for x in _IGNORAR_IMPRESORAS):
            continue
        red = nom.startswith("\\\\") or str(d.get("tipo", "")).lower() == "connection"
        res.append({"nombre": nom, "driver": d.get("driver") or "", "puerto": d.get("puerto") or "",
                    "host": d.get("host") or "", "compartida": bool(d.get("compartida")),
                    "recurso": d.get("recurso") or "", "red": red,
                    "predeterminada": bool(d.get("predeterminada"))})
    return res


def clonar_impresora(imp):
    """Instala una impresora descrita por listar_impresoras(). Devuelve (ok, mensaje)."""
    nom = imp.get("nombre", "")
    if imp.get("red") and nom.startswith("\\\\"):
        return conectar_impresora(nom)
    host, drv = imp.get("host"), imp.get("driver")
    if not host or not drv:
        return False, "Impresora local sin dirección de red: instálala con su driver."
    puerto = imp.get("puerto") or f"IP_{host}"
    rc, out, _ = ps(
        "try { if(-not (Get-PrinterDriver -Name %s -ErrorAction SilentlyContinue)){'MB_SIN_DRIVER';return};"
        "if(-not (Get-PrinterPort -Name %s -ErrorAction SilentlyContinue)){Add-PrinterPort -Name %s -PrinterHostAddress %s};"
        "if(-not (Get-Printer -Name %s -ErrorAction SilentlyContinue)){Add-Printer -Name %s -DriverName %s -PortName %s};"
        "'MB_OK' } catch { 'MB_ERR|' + $_.Exception.Message }"
        % (ps_q(drv), ps_q(puerto), ps_q(puerto), ps_q(host), ps_q(nom), ps_q(nom), ps_q(drv), ps_q(puerto)),
        timeout=120)
    if "MB_OK" in out:
        return True, "Impresora creada"
    if "MB_SIN_DRIVER" in out:
        return False, f"Falta el driver «{drv}» (instálalo y vuelve a intentar)"
    m = re.search(r"MB_ERR\|(.*)", out, re.S)
    return False, (m.group(1).strip()[:200] if m else "No se pudo crear")


def predeterminar_impresora(nombre):
    if not IS_WIN:
        return False
    rc, out, _ = ps("(New-Object -ComObject WScript.Network).SetDefaultPrinter(%s); 'MB_OK'" % ps_q(nombre), timeout=30)
    return "MB_OK" in out
