# Pruebas en Windows real (runner de GitHub). Imprime PASS/FAIL/INFO por prueba; no cambia red ni dominio.
import os, sys, json, shutil, tempfile, threading, time, traceback, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mb_sistema as SYS, mb_motor as M, mb_util as U
fallos = []

def prueba(nombre):
    def deco(fn):
        try:
            r = fn()
            print(f"PASS  {nombre}" + (f"  -> {r}" if r is not None else ""), flush=True)
        except Exception as e:
            fallos.append(nombre)
            print(f"FAIL  {nombre}: {e}", flush=True)
            traceback.print_exc(file=sys.stdout)
        return fn
    return deco

@prueba("es Windows y admin")
def _():
    assert SYS.IS_WIN; return f"admin={SYS.es_admin()}"

@prueba("SMBIOS (serie/modelo/chasis)")
def _():
    d = SYS.smbios_leer(); print("INFO", json.dumps(d, default=str)[:400]); assert d is not None

@prueba("info_pc completo")
def _():
    i = SYS.info_pc(); print("INFO", json.dumps(i, default=str, ensure_ascii=False)[:600])
    assert i.get("host")

@prueba("monitores (WMI + registro EDID)")
def _():
    print("INFO wmi:", SYS.monitores_wmi()); print("INFO registro:", SYS.monitores_registro())

@prueba("adaptadores de red")
def _():
    a = SYS.adaptadores(); print("INFO", json.dumps(a, default=str)[:500]); assert isinstance(a, list)

@prueba("IPs y describir_ips")
def _():
    return SYS.describir_ips()

@prueba("estado de dominio")
def _():
    return SYS.estado_dominio()

@prueba("perfiles locales")
def _():
    return SYS.perfiles_locales()

@prueba("servicios / IVANTI (no instalado = ok)")
def _():
    r = SYS.ivanti_estado(U.cargar_config()); return {k: r[k] for k in ("existe", "items")}

@prueba("firewall abrir/cerrar")
def _():
    assert SYS.abrir_firewall(M.PORT_TRANSFER, M.PORT_DISCOVER), "no abrió"
    SYS.cerrar_firewall()

@prueba("robocopy")
def _():
    b = tempfile.mkdtemp(); s, d = os.path.join(b, "s"), os.path.join(b, "d"); os.makedirs(s)
    open(os.path.join(s, "ñ á.txt"), "w", encoding="utf-8").write("hola")
    ok, rc = SYS.robocopy(s, d); assert ok, rc
    assert os.path.exists(os.path.join(d, "ñ á.txt")); return f"rc={rc}"

@prueba("nombre y comprobación de una tipografía del sistema")
def _():
    f = os.path.join(os.environ["WINDIR"], "Fonts", "arial.ttf")
    n = SYS.nombre_fuente(f); print("INFO nombre:", n); assert n
    return SYS.fuente_instalada(f)

@prueba("hostname válido/ inválido")
def _():
    assert SYS.hostname_valido("124MD243H47")[0]; assert not SYS.hostname_valido("a" * 20)[0]

@prueba("migración loopback (rutas Windows, unicode, reanudación)")
def _():
    b = tempfile.mkdtemp(); o, dst = os.path.join(b, "Users", "ana", "Docs"), os.path.join(b, "dest")
    os.makedirs(o)
    for n, t in (("a.txt", b"hola"), ("ñandú.txt", b"uni"), ("g.bin", os.urandom(3_000_000))):
        open(os.path.join(o, n), "wb").write(t)
    log = lambda t, n=None: None
    jr, js = M.Job(log), M.Job(log); res = {}
    def rx():
        try: res["r"] = M.recibir(jr, dst, limite=10 * 1024 ** 3)
        except Exception as e: res["e"] = e
    threading.Thread(target=rx, daemon=True).start()
    import socket as _s
    for _ in range(60):
        try: _s.create_connection(("127.0.0.1", M.PORT_TRANSFER), timeout=1).close(); break
        except OSError: time.sleep(0.5)
    time.sleep(1)
    sc = M.escanear(js, [os.path.join(b, "Users")])
    env = M.enviar(js, "127.0.0.1", sc, {"host": "VIEJA", "serie": "MJ0TEST1"})
    time.sleep(1); jr.abortar()
    assert "e" not in res or isinstance(res["e"], M.Cancelado), res.get("e")
    for n in ("a.txt", "ñandú.txt", "g.bin"):
        p1, p2 = os.path.join(o, n), os.path.join(dst, "Users", "ana", "Docs", n)
        assert hashlib.md5(open(p1, "rb").read()).digest() == hashlib.md5(open(p2, "rb").read()).digest(), n
    return env.get("recibidos_ok")


@prueba("diagnóstico de monitores (texto)")
def _():
    t = SYS.monitores_diagnostico(); print("INFO", t[:700]); assert "DIAGNÓSTICO" in t

@prueba("inventario de programas instalados")
def _():
    import mb_servicio as SV
    p = SV.programas_instalados(); print("INFO ejemplos:", [x["nombre"] for x in p[:6]]); assert len(p) > 3
    f, r, y = SV.comparar(p, p[:-1]); return f"faltan={len(f)} ruido={r} ya={y}"

@prueba("ficha de cierre + marcador + verificar/liberar")
def _():
    import mb_servicio as SV, datetime as D
    t = tempfile.mkdtemp()
    f, r = SV.escribir_cierre(t, {"host": "VIEJA", "serie": "X", "monitores": [{"serie": "A"}]},
                              {"total_archivos": 3, "enviados": 3, "recibidos_ok": 3, "omitidos": []})
    m = SV.escribir_marcador(f); print("INFO marcador:", m)
    assert not SV.verificar_liberar(t, "VIEJA")[0]
    assert SV.verificar_liberar(t, "VIEJA", D.datetime.now() + D.timedelta(days=3))[0]
    if m and os.path.exists(m): os.remove(m)


@prueba("parser de netsh wlan (ES/EN) y consulta real")
def _():
    es = "    Nombre : Wi-Fi\n    Estado : conectado\n    SSID : VIP_N\n    BSSID : aa\n"
    assert SYS.parse_wifi_interfaces(es) == [("VIP_N", True)]
    assert SYS.parse_wifi_interfaces("    Name : W\n    State : disconnected\n") == [("", False)]
    return SYS.wifi_conectado("VIP_N")

@prueba("interfaz Tk se construye (modo demo de datos reales)")
def _():
    import tkinter as tk, mainbit_asistente as A
    r = tk.Tk(); a = A.App(r)
    for _ in range(20): r.update(); time.sleep(0.1)
    for m in (1, 2, 0): a.seg.seleccionar(m); a.cambiar_modo(m); r.update()
    for s in (0, 1, 2): a.cambiar_sub(s); r.update()
    try:
        from PIL import ImageGrab; ImageGrab.grab().save("pantalla_ci.png")
    except Exception as e: print("INFO sin captura:", e)
    a.cambiar_sub(2); r.update()
    r.destroy()

@prueba("manual para el usuario")
def _():
    import mb_manual as MAN
    raiz = tempfile.mkdtemp()
    dest = os.path.join(raiz, "BACKUP_MIGRACION")
    for c, n in (("Desktop", 2), ("Documents", 5)):
        p = os.path.join(dest, "Users", "prueba", c, "sub"); os.makedirs(p)
        for i in range(n):
            open(os.path.join(p, f"a{i}.txt"), "w").write("x" * 500)
    os.makedirs(os.path.join(dest, "D", "Datos"))
    d = MAN.datos_servicio("PRUEBA1", dest, dominio_ok=True, restaurado={"origen": "prueba", "destino": "prueba"},
                           faltan=["Programa <x>"], tiene_lista=True, host_viejo="VIEJA1", fecha_borrado="01/01/2030",
                           soporte="Soporte 123")
    h, t, c = MAN.escribir_manual(os.path.join(raiz, "R"), d, copiar_escritorio=False)
    cont = open(h, encoding="utf-8").read()
    assert "Documentos (prueba)" in cont and "5 archivos" in cont and "&lt;x&gt;" in cont and "Soporte 123" in cont
    assert os.path.getsize(t) > 200
    shutil.rmtree(raiz, ignore_errors=True)
    return "manual ok"

@prueba("impresoras, config compartida, secretos, descargas, arranque")
def _():
    import mb_secretos as SEC, mb_descargas as DES, mb_arranque as ARR, urllib.request
    imps = SYS.listar_impresoras()
    print("INFO impresoras:", len(imps), [i["nombre"] for i in imps][:5])
    assert isinstance(imps, list)
    cfg = dict(U.DEFAULTS)
    adop = U.adoptar_config(cfg, {"dominio": "x.test", "wifi_ssid": "RED", "clave_falsa": "no"})
    assert cfg["dominio"] == "x.test" and "clave_falsa" not in cfg and len(adop) == 2
    cfg2 = dict(U.DEFAULTS); cfg2["dominio"] = "propio"
    U.adoptar_config(cfg2, {"dominio": "otro"})
    assert cfg2["dominio"] == "propio"
    d = tempfile.mkdtemp()
    if SEC.disponible():
        r = os.path.join(d, "s.json")
        SEC.guardar(r, "1234", {"wifi_clave": "abc"})
        assert SEC.leer(r, "1234") == {"wifi_clave": "abc"} and SEC.leer(r, "0000") is None
        assert "abc" not in open(r).read()
    else:
        print("INFO cryptography no disponible")
    app = os.path.join(d, "app"); os.makedirs(app)
    open(os.path.join(app, "x.exe"), "w").write("x"); open(os.path.join(app, "credenciales.ini"), "w").write("secreto")
    srv = ARR.iniciar(app, 50110)
    try:
        z = os.path.join(d, "a.zip")
        ok, m = DES.descargar("http://127.0.0.1:50110/app.zip", z)
        assert ok, m
        import zipfile
        n = zipfile.ZipFile(z).namelist()
        assert "MainbitAsistente/x.exe" in n and not any("credenciales" in x for x in n), n
        ok, m = DES.descargar("http://127.0.0.1:50110/app.zip", z + "2", sha256="0" * 64, reintentos=1)
        assert not ok
    finally:
        srv.shutdown()
    ok, m = DES.descargar("https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/README.md", os.path.join(d, "r.md"), reintentos=1)
    print("INFO descarga internet:", ok, m)

@prueba("importar tablas de versiones previas")
def _():
    import mb_relacion as R
    d = tempfile.mkdtemp()
    rel = R.Relacion(d)
    rel.agregar_pendientes(["Ana Lopez"])
    rel.guardar_equipo({"Usuario Nombre": "Luis", "Apellido": "Ortega", "Serie cpu Nuevo": "ABC12345", "Tipo": "D2"})
    # CSV de una versión previa con encabezados distintos, punto y coma y acentos
    p = os.path.join(d, "vieja.csv")
    open(p, "w", encoding="utf-8-sig").write(
        "Nombre;Apellidos;Serie CPU nuevo;Monitor nuevo;Tipo;Columna rara\n"
        "Luis;Ortega;abc12345;MON1;P03;x\n"
        "Ana;Lopez;ZZZ99999;MON2;D2;x\n"
        "Maria;Soto;QQQ11111;;D2;x\n")
    rep = rel.importar(p)
    assert rep["nuevas"] == 1 and rep["completadas"] == 2, rep
    assert any(c["campo"] == "Tipo" for c in rep["conflictos"]), rep      # D2 vs P03: se conserva D2
    luis = rel.filas[rel.indice_por_serie("ABC12345")]
    assert luis["Tipo"] == "D2" and luis["Serie monitor Nuevo"] == "MON1"
    assert os.path.exists(rep["copia"]) and "Columna rara" in rep["ignoradas"]
    n = len(rel.filas); rep2 = rel.importar(p)
    assert rep2["nuevas"] == 0 and len(rel.filas) == n                      # idempotente
    # XLSX generado por la propia app (Tabla 1)
    r2 = R.Relacion(tempfile.mkdtemp())
    rep3 = r2.importar(rel.xlsx)
    assert rep3["nuevas"] == len(rel.filas), rep3
    print("INFO", R.texto_informe_importacion(rep))

print("\nRESUMEN:", "TODO OK" if not fallos else "FALLAN -> " + ", ".join(fallos))
sys.exit(1 if fallos else 0)
