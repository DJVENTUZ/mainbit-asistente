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
    assert not rep["copia"] and not [x for x in os.listdir(os.path.dirname(rel.csv)) if "antes_de" in x] and "Columna rara" in rep["ignoradas"]
    n = len(rel.filas); rep2 = rel.importar(p)
    assert rep2["nuevas"] == 0 and len(rel.filas) == n                      # idempotente
    # XLSX generado por la propia app (Tabla 1)
    r2 = R.Relacion(tempfile.mkdtemp())
    rep3 = r2.importar(rel.xlsx)
    assert rep3["nuevas"] == len(rel.filas), rep3
    print("INFO", R.texto_informe_importacion(rep))

@prueba("núcleo sin interfaz (mb_core) en Windows real")
def _():
    import mb_core as K
    c = K.Core()
    time.sleep(4)
    s = c.estado_publico()
    json.dumps(s)
    assert s["info"]["host"] and s["version"], s["info"]
    c.cambiar_modo(1); c.cambiar_modo(2); c.cambiar_modo(0)
    c.v_serie.set("ABCDEFGH47"); c.v_base.set("124MD2")
    assert c.estado_publico()["hostname"] == "124MD2FGH47"
    assert c.datos_reportes()["equipos"] >= 0 and c.datos_programas()["nuevos"] > 0
    assert c.ajustes_datos()["limite_gb"]
    print("INFO impresoras en estado:", len(s["impresoras_vieja"]), "unidades:", s["unidades"])
    c.vivo = False

@prueba("carpeta Instaladores + página de arranque para la PC vieja")
def _():
    import urllib.request, mb_arranque as ARR, mb_sistema as SYS
    d = tempfile.mkdtemp()
    for n in ("02_b.exe", "01_a.msi", "x.txt", "_ignorado.exe"):
        open(os.path.join(d, n), "wb").write(b"x")
    assert [os.path.basename(p) for p in SYS.listar_instaladores(d)] == ["01_a.msi", "02_b.exe"]
    ok, det = SYS.instalar_programa(os.path.join(d, "02_b.exe"), timeout=20)
    assert ok is False and "args" in det, (ok, det)               # un archivo falso no se instala y lo dice claro
    app = tempfile.mkdtemp(); open(os.path.join(app, "a.txt"), "w").write("1")
    os.makedirs(os.path.join(app, "Instaladores")); open(os.path.join(app, "Instaladores", "grande.exe"), "w").write("1")
    srv = ARR.iniciar(app, 50123)
    try:
        pag = urllib.request.urlopen("http://127.0.0.1:50123/").read().decode("utf-8")
        assert "iniciar.bat" in pag
        bat = urllib.request.urlopen("http://127.0.0.1:50123/iniciar.bat").read().decode("utf-8")
        assert "127.0.0.1:50123/app.zip" in bat and "RunAs" in bat, bat
        import zipfile, io
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen("http://127.0.0.1:50123/app.zip").read()))
        nombres = z.namelist()
        assert "MainbitAsistente/modo_vieja.flag" in nombres and "MainbitAsistente/a.txt" in nombres, nombres
        assert not any("Instaladores" in n for n in nombres), nombres
    finally:
        srv.shutdown()

@prueba("cierre de servicio: archiva la relación y vacía la vista")
def _():
    import mb_relacion as R
    d = tempfile.mkdtemp()
    rel = R.Relacion(d)
    rel.agregar_pendientes(["Ana Lopez", "Luis Perez"])
    n0 = len(rel.filas)
    assert n0 >= 2
    dest = os.path.join(d, "Servicios", "x")
    n = rel.archivar_y_vaciar(dest)
    assert n == n0 and len(rel.filas) == 0 and os.path.exists(os.path.join(dest, "RELACION_EQUIPOS.csv"))
    assert len(R.Relacion(d).filas) == 0                        # al reabrir sigue vacía (no vuelve la semilla)
    assert len(R.Relacion(dest).filas) == n0                    # y el archivo guarda todo

@prueba("coherencia: acciones de la interfaz web = acciones del núcleo")
def _():
    import re, mb_core as K
    h = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "index.html"), encoding="utf-8").read()
    usadas = set(re.findall(r"data-act=\"?'?([a-z_0-9]+)", h)) | set(re.findall(r"(?:B|SB|b)\('[^']*','([a-z_0-9]+)'", h)) | set(re.findall(r"act\('([a-z_0-9]+)'", h))
    ini = h.index("const LOCAL={")
    locales = set(re.findall(r"^ (?:async )?([a-z_0-9]+)\(", h[ini:ini + 7000], re.M))
    faltan = usadas - set(K.Core.ACCIONES) - locales
    assert not faltan, faltan
    sin_metodo = [a for a in K.Core.ACCIONES if not hasattr(K.Core, a)]
    assert not sin_metodo, sin_metodo

@prueba("sin USB: copia local, sincronizar ⇄ USB sin perder datos, drivers, winget, perfil")
def _():
    import mb_local as L, mb_relacion as R, mb_sistema as SYS
    usb, loc = tempfile.mkdtemp(), tempfile.mkdtemp()
    ru, rl = R.Relacion(usb), R.Relacion(loc)
    ru.agregar_pendientes(["Ana Lopez"]); ru.guardar()
    rl.agregar_pendientes(["Ana Lopez", "Luis Perez"]); rl.filas[-1]["Serie cpu Nuevo"] = "ZZ99999"; rl.guardar()
    os.makedirs(os.path.join(loc, "Reportes")); open(os.path.join(loc, "Reportes", "a.txt"), "w").write("uno")
    os.makedirs(os.path.join(usb, "Reportes")); open(os.path.join(usb, "Reportes", "a.txt"), "w").write("distinto!")
    os.makedirs(os.path.join(usb, "Instaladores")); open(os.path.join(usb, "Instaladores", "x.msi"), "w").write("m")
    res = L.sincronizar(loc, usb, host="PCX")
    assert len(R.Relacion(usb).filas) >= 2, res                      # lo de la PC llegó a la USB
    assert os.path.exists(os.path.join(usb, "Reportes", "a.txt")) and os.path.exists(os.path.join(usb, "Reportes", "a_PCX.txt"))
    assert open(os.path.join(usb, "Reportes", "a.txt")).read() == "distinto!"   # nada se pisó
    assert os.path.exists(os.path.join(loc, "Instaladores", "x.msi"))          # y lo de la USB llegó a la PC
    L.sincronizar(loc, usb, host="PCX")                                         # idempotente
    assert len(R.Relacion(usb).filas) == len(R.Relacion(loc).filas)
    assert isinstance(SYS.winget_disponible(), bool)
    assert SYS.empaquetar_drivers(tempfile.mkdtemp(), [{"nombre": "X", "driver": "Driver Inexistente"}]) == []
    ok, msg = SYS.crear_perfil_dominio("usuario.inexistente", "no.existe.local")
    assert ok is False and msg
    assert SYS.instalar_drivers(tempfile.mkdtemp()) == ([], [])

@prueba("la relación sobrevive a actualizar (datos fuera de la carpeta del programa)")
def _():
    import mb_local as L, mb_relacion as R
    base = tempfile.mkdtemp()
    app_vieja, datos = os.path.join(base, "MainbitAsistente"), os.path.join(base, "MainbitDatos")
    os.makedirs(app_vieja)
    r = R.Relacion(app_vieja); r.agregar_pendientes(["Ana Lopez", "Luis Perez"]); r.guardar()   # versión anterior: dentro del programa
    os.makedirs(os.path.join(app_vieja, "Cierres")); open(os.path.join(app_vieja, "Cierres", "c.txt"), "w").write("x")
    L.migrar_datos(datos, [app_vieja])
    assert len(R.Relacion(datos).filas) == 2 and os.path.exists(os.path.join(datos, "Cierres", "c.txt"))
    import shutil; shutil.rmtree(app_vieja)                                      # "actualizar": se reemplaza la carpeta del programa
    assert len(R.Relacion(datos).filas) == 2                                    # la relación sigue ahí
    L.migrar_datos(datos, [app_vieja])                                          # repetir no duplica ni falla
    assert len(R.Relacion(datos).filas) == 2
    assert L.raiz_datos_usb(os.path.join("E:\\", "MainbitAsistente")).endswith("MainbitDatos")
    exe = os.path.join(tempfile.mkdtemp(), "MainbitAsistente.exe"); open(exe, "wb").write(b"x")
    lnk = L.crear_acceso(exe, tempfile.mkdtemp())
    assert lnk and os.path.exists(lnk) and lnk.endswith("Mainbit Toolkit.lnk"), lnk   # aparece en Inicio
    import mb_arranque as ARR
    assert "Descargar e iniciar" in ARR.PAGINA and "{CMD}" in ARR.PAGINA

@prueba("copia local exacta: sin restos de versiones viejas y sin tocar datos")
def _():
    import mb_local as L, mb_util as U, time as _t
    base = tempfile.mkdtemp(); src, dst = os.path.join(base, "usb", "MainbitAsistente"), os.path.join(base, "pc", "App")
    for d in (os.path.join(src, "_internal"), os.path.join(dst, "_internal"), os.path.join(dst, "Reportes")):
        os.makedirs(d)
    exe = os.path.basename(sys.executable)
    open(os.path.join(src, exe), "w").write("nuevo"); open(os.path.join(src, "_internal", "Python.Runtime.dll"), "w").write("3.0.5")
    open(os.path.join(dst, "_internal", "Python.Runtime.dll"), "w").write("3.2.1-viejo-pero-mas-reciente")   # fecha posterior a la de la USB
    os.utime(os.path.join(dst, "_internal", "Python.Runtime.dll"), (_t.time() + 5000, _t.time() + 5000))
    open(os.path.join(dst, "_internal", "resto_viejo.dll"), "w").write("x")
    open(os.path.join(dst, "Reportes", "r.txt"), "w").write("dato"); open(os.path.join(dst, "RELACION_EQUIPOS.csv"), "w").write("a,b\n")
    open(os.path.join(dst, "usb_origen.json"), "w").write("{}")
    ant_app, ant_loc = U.dir_app, L.dir_local
    U.dir_app, L.dir_local = (lambda: src), (lambda: dst)
    try:
        r = L.instalar_local({"usb_etiqueta": "USB Blue"})
    finally:
        U.dir_app, L.dir_local = ant_app, ant_loc
    assert r and os.path.isfile(r), r
    assert open(os.path.join(dst, "_internal", "Python.Runtime.dll")).read() == "3.0.5"      # idéntica a la de la USB
    assert not os.path.exists(os.path.join(dst, "_internal", "resto_viejo.dll"))              # sin restos
    assert open(os.path.join(dst, "Reportes", "r.txt")).read() == "dato" and os.path.exists(os.path.join(dst, "RELACION_EQUIPOS.csv"))   # datos intactos

print("\nRESUMEN:", "TODO OK" if not fallos else "FALLAN -> " + ", ".join(fallos))
sys.exit(1 if fallos else 0)
