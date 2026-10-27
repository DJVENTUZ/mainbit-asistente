# -*- coding: utf-8 -*-
"""Relación de equipos (CSV maestro + XLSX de entrega) y evidencias para ServiceNow."""
import csv
import html
import os
import re
import time
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from mb_util import ultimos5

COLUMNAS = ["Usuario Nombre", "Apellido", "Serie cpu Nuevo", "Serie monitor Nuevo",
            "Serie monitor antiguo", "Serie cpu antiguo", "Tipo",
            "Hostname", "Fecha", "Estado", "Notas"]
REQUERIDAS = ["Usuario Nombre", "Apellido", "Serie cpu Nuevo", "Serie monitor Nuevo",
              "Serie monitor antiguo", "Serie cpu antiguo", "Tipo", "Hostname"]

# Tu «Tabla 1» original (SEMARNAT RELACIÓN DE EQUIPOS.numbers)
SEED = []   # los datos reales viven en RELACION_EQUIPOS.csv (junto al programa), no en el código
HOST_BASE_SEED = "124MD2"


def fila_vacia():
    return {c: "" for c in COLUMNAS}


def faltantes(fila):
    return [c for c in REQUERIDAS if not str(fila.get(c, "")).strip()]


def _csv_seguro(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "@") else v


# ======================================================================
# XLSX mínimo (sin librerías): 2 hojas, encabezado azul, panel fijo
# ======================================================================
def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _limpiar_xml(t):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(t))


def _hoja_xml(columnas, filas, anchos):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><cols>']
    for i, w in enumerate(anchos):
        out.append(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>')
    out.append("</cols><sheetData>")
    todas = [columnas] + [[f.get(c, "") if isinstance(f, dict) else f[i] for i, c in enumerate(columnas)]
                          for f in filas]
    for r, fila in enumerate(todas, start=1):
        out.append(f'<row r="{r}">')
        for c, v in enumerate(fila):
            t = _limpiar_xml(v)
            estilo = 1 if r == 1 else 2
            sp = ' xml:space="preserve"' if t != t.strip() else ""
            out.append(f'<c r="{_col(c)}{r}" s="{estilo}" t="inlineStr"><is><t{sp}>{xml_escape(t)}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


ESTILOS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
           '<fills count="3"><fill><patternFill patternType="none"/></fill>'
           '<fill><patternFill patternType="gray125"/></fill>'
           '<fill><patternFill patternType="solid"><fgColor rgb="FF1E4A7A"/><bgColor indexed="64"/></patternFill></fill></fills>'
           '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
           '<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right>'
           '<top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1">'
           '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
           '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1">'
           '<alignment vertical="center" wrapText="1"/></xf></cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def escribir_xlsx(path, hojas):
    """hojas = [(nombre, columnas, filas, anchos)]. Escritura atómica."""
    n = len(hojas)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(n)) + "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{xml_escape(h[0])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, h in enumerate(hojas))
          + "</sheets></workbook>")
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                        for i in range(n))
              + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
              "</Relationships>")
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/styles.xml", ESTILOS)
        for i, (nombre, cols, filas, anchos) in enumerate(hojas):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _hoja_xml(cols, filas, anchos))
    os.replace(tmp, path)


# ======================================================================
# Relación
# ======================================================================
class Relacion:
    def __init__(self, carpeta):
        self.dir = carpeta
        self.csv = os.path.join(carpeta, "RELACION_EQUIPOS.csv")
        self.xlsx = os.path.join(carpeta, "RELACION_EQUIPOS.xlsx")
        self.filas = []
        self.error_xlsx = ""
        self.cargar()

    # -- carga / guardado
    def cargar(self):
        self.filas = []
        if os.path.exists(self.csv):
            with open(self.csv, "r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    fila = fila_vacia()
                    for c in COLUMNAS:
                        fila[c] = (r.get(c) or "").strip()
                    self.filas.append(fila)
        else:
            self.filas = self.semilla()
            self.guardar()

    def semilla(self):
        filas = []
        for nom, ape, cpu, mon, mona, cpua, tipo in SEED:
            f = fila_vacia()
            f.update({"Usuario Nombre": nom, "Apellido": ape, "Serie cpu Nuevo": cpu,
                      "Serie monitor Nuevo": mon, "Serie monitor antiguo": mona,
                      "Serie cpu antiguo": cpua, "Tipo": tipo,
                      "Hostname": HOST_BASE_SEED + ultimos5(cpu)})
            self.recalcular(f, "Importado de tu relación original")
            filas.append(f)
        return filas

    @staticmethod
    def recalcular(fila, nota_base=None):
        falt = faltantes(fila)
        fila["Estado"] = "Hecho" if not falt else "Incompleto"
        partes = []
        if nota_base:
            partes.append(nota_base)
        elif fila.get("Notas") and not fila["Notas"].startswith(("Falta", "Importado")):
            partes.append(fila["Notas"])
        if falt:
            partes.append("Falta: " + ", ".join(falt))
        fila["Notas"] = ". ".join(partes)

    def guardar(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.csv + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNAS)
            for fila in self.filas:
                w.writerow([_csv_seguro(fila.get(c, "")) for c in COLUMNAS])
        os.replace(tmp, self.csv)
        try:
            self.exportar_xlsx()
            self.error_xlsx = ""
        except Exception as e:            # el CSV ya quedó guardado; el XLSX se regenera después
            self.error_xlsx = str(e)

    def exportar_xlsx(self):
        t2 = []
        for f in self.filas:
            h = f.get("Hostname", "")
            if h:
                t2.append([h[:-5], h[-5:], h])
        escribir_xlsx(self.xlsx, [
            ("Tabla 1", COLUMNAS, self.filas, [24, 22, 17, 19, 21, 17, 12, 17, 17, 12, 42]),
            ("Tabla 2", ["HOST BASE", "Últimos 5", "HOSTNAME"],
             [{"HOST BASE": a, "Últimos 5": b, "HOSTNAME": c} for a, b, c in t2], [16, 12, 18]),
        ])

    # -- consulta / edición
    def indice_por_serie(self, serie):
        s = (serie or "").strip().upper()
        if not s:
            return -1
        for i, f in enumerate(self.filas):
            if f["Serie cpu Nuevo"].strip().upper() == s:
                return i
        return -1

    def pendientes(self):
        """Usuarios cargados sin equipo asignado todavía."""
        return [(i, f) for i, f in enumerate(self.filas) if not f["Serie cpu Nuevo"].strip()]

    def agregar_pendientes(self, lineas):
        """Cada línea: 'Nombre Apellido' o 'Nombre<TAB>Apellido' o 'Nombre,Apellido'. Devuelve cuántos."""
        n = 0
        for ln in lineas:
            ln = ln.strip()
            if not ln:
                continue
            partes = re.split(r"[\t,;]", ln)
            if len(partes) >= 2:
                nom, ape = partes[0].strip(), " ".join(p.strip() for p in partes[1:])
            else:
                p = ln.split()
                nom, ape = (" ".join(p[:-2]), " ".join(p[-2:])) if len(p) > 3 else \
                           (p[0], " ".join(p[1:])) if len(p) > 1 else (ln, "")
            if any(f["Usuario Nombre"].lower() == nom.lower() and f["Apellido"].lower() == ape.lower()
                   for f in self.filas):
                continue
            f = fila_vacia()
            f["Usuario Nombre"], f["Apellido"] = nom, ape
            f["Estado"], f["Notas"] = "Pendiente", "Equipo aún sin asignar"
            self.filas.append(f)
            n += 1
        if n:
            self.guardar()
        return n

    def guardar_equipo(self, datos, indice=None):
        """Crea o actualiza la fila de este equipo. Devuelve (indice, fila)."""
        i = indice if indice is not None and indice >= 0 else self.indice_por_serie(datos.get("Serie cpu Nuevo"))
        if i is None or i < 0:
            self.filas.append(fila_vacia())
            i = len(self.filas) - 1
        fila = self.filas[i]
        for c in COLUMNAS:
            if c in datos and datos[c] is not None and c not in ("Estado", "Notas"):
                fila[c] = str(datos[c]).strip()
        if not fila["Fecha"]:
            fila["Fecha"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.recalcular(fila, datos.get("Notas") or None)
        self.guardar()
        return i, fila

    def resumen(self):
        hechos = sum(1 for f in self.filas if f["Estado"] == "Hecho")
        inc = sum(1 for f in self.filas if f["Estado"] == "Incompleto")
        pen = sum(1 for f in self.filas if f["Estado"] == "Pendiente")
        return hechos, inc, pen


# ======================================================================
# Evidencia técnica (para ServiceNow)
# ======================================================================
def construir_evidencia(titulo, secciones):
    """secciones = [(titulo, [(etiqueta, valor, nivel)])] nivel: ok|warn|err|None"""
    marca = {"ok": "[OK] ", "warn": "[!]  ", "err": "[X]  ", None: "     "}
    lineas = [titulo, "=" * len(titulo), f"Generado: {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    h = ['<!doctype html><html lang="es"><meta charset="utf-8"><title>' + html.escape(titulo) + '</title>'
         '<style>body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#111}h1{color:#1e4a7a}'
         'h2{border-bottom:2px solid #1e4a7a;padding-bottom:4px;margin-top:26px}td{padding:4px 12px;vertical-align:top}'
         '.ok{color:#18813a}.warn{color:#9a6b00}.err{color:#c0281c}.k{color:#555;width:260px}</style>'
         f'<h1>{html.escape(titulo)}</h1><p>Generado: {datetime.now():%Y-%m-%d %H:%M:%S}</p>']
    for tit, filas in secciones:
        lineas += [tit.upper(), "-" * len(tit)]
        h.append(f"<h2>{html.escape(tit)}</h2><table>")
        for etq, val, nivel in filas:
            lineas.append(f"{marca.get(nivel, '     ')}{etq}: {val}")
            h.append(f'<tr><td class="k">{html.escape(str(etq))}</td><td class="{nivel or ""}">{html.escape(str(val))}</td></tr>')
        lineas.append("")
        h.append("</table>")
    return "\n".join(lineas), "".join(h)


def escribir_evidencia(carpeta, hostname, titulo, secciones):
    os.makedirs(carpeta, exist_ok=True)
    txt, htm = construir_evidencia(titulo, secciones)
    base = os.path.join(carpeta, f"{re.sub(r'[^A-Za-z0-9_-]', '_', hostname)}_{datetime.now():%Y%m%d_%H%M}")
    with open(base + ".txt", "w", encoding="utf-8-sig") as f:
        f.write(txt)
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(htm)
    return base + ".txt", base + ".html"
