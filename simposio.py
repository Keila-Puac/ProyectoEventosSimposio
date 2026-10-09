"""
Lógica del caso «Validación de pagos y acceso al Simposio de Ingeniería».

Basado en los algoritmos del equipo (automata.py): mismos estados q0..q7/qE, mismos símbolos,
misma regla de umbrales (>= UMBRAL_ALTO coincidencia alta, >= UMBRAL_MIN intermedia) y la
misma revisión manual. Diferencias respecto a esa versión:

  * Los datos viven en archivos CSV dentro de DATA_DIR (como el resto de la web), no en MySQL.
  * El sistema NO genera tickets: asigna uno de los que Coordinación ya cargó (caso, 2.º momento).
  * Un AFD controla cada pago y cada ticket (un único estado tras cada entrada).

AFND (búsqueda de identidad)           AFD de ticket                  AFD de pago
  q0 -R-> q1 -N-> q2 -C-> q3 -C-> q4     DISPONIBLE -asignar-> ASIGNADO   PENDIENTE -> VALIDADO / REVISION /
  q4 -S1..S4-> q5.x ; q5.1 -V-> q7       ASIGNADO  -ingresar-> UTILIZADO               DUPLICADO / INCOMPLETO /
  q5.2/5.3/5.4 -M-> q6 -V-> q7 | -X-> qE                                               MONTO_INCORRECTO
                                                                       REVISION -> VALIDADO / RECHAZADO
"""
import contextlib
import json
import os
import threading
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

import pandas as pd

try:  # rapidfuzz es lo recomendado; si no está instalado se usa una similitud equivalente de difflib
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover
    fuzz = process = None

try:
    import fcntl
except ImportError:  # Windows: basta el candado de hilos
    fcntl = None

UMBRAL_ALTO = float(os.getenv("UMBRAL_ALTO", 95))
UMBRAL_MIN = float(os.getenv("UMBRAL_MIN", 75))
MONTO_CORRECTO = Decimal(os.getenv("MONTO_SIMPOSIO", "280"))   # el reporte de 2025 mostraba Q280
UMBRAL_CARNET = float(os.getenv("UMBRAL_CARNET", 85))  # si el estudiante ya dio su carnet, basta una afinidad menor
LIMITE_CANDIDATOS = 3

COL_PART = ["carnet", "nombre", "correo"]
COL_PAGOS = ["recibo", "fecha", "nombre", "monto", "concepto", "estado", "carnet", "ticket",
             "motivo", "candidatos", "traza", "actualizado"]
COL_TICKETS = ["ticket", "estado", "carnet", "recibo", "fecha_asignacion", "fecha_uso", "correo_enviado"]

ESTADOS_PAGO = ["PENDIENTE", "VALIDADO", "REVISION", "DUPLICADO", "INCOMPLETO", "MONTO_INCORRECTO", "RECHAZADO"]

# ---------- AFD de pagos y de tickets ----------
PAGO_DELTA = {
    ("PENDIENTE", "validar"): "VALIDADO", ("PENDIENTE", "revisar"): "REVISION",
    ("PENDIENTE", "duplicar"): "DUPLICADO", ("PENDIENTE", "incompleto"): "INCOMPLETO",
    ("PENDIENTE", "monto"): "MONTO_INCORRECTO",
    ("REVISION", "validar"): "VALIDADO", ("REVISION", "rechazar"): "RECHAZADO",
}
TICKET_DELTA = {  # AFD completo: una entrada rechazada deja el ticket en el mismo estado
    ("DISPONIBLE", "asignar"): "ASIGNADO", ("DISPONIBLE", "ingresar"): "DISPONIBLE",
    ("ASIGNADO", "asignar"): "ASIGNADO", ("ASIGNADO", "ingresar"): "UTILIZADO",
    ("UTILIZADO", "asignar"): "UTILIZADO", ("UTILIZADO", "ingresar"): "UTILIZADO",
}


def afd_pago(estado, simbolo):
    try:
        return PAGO_DELTA[(estado, simbolo)]
    except KeyError:
        raise ValueError(f"Transición no permitida: pago {estado} con «{simbolo}»") from None


def afd_ticket(estado, simbolo):
    return TICKET_DELTA[(estado, simbolo)]


# ---------- utilidades ----------
def normalizar(texto):
    """Minúsculas, sin tildes y sin espacios de más."""
    if texto is None:
        return ""
    texto = unicodedata.normalize("NFD", str(texto).lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(texto.split())


def _decimal(valor):
    try:
        return Decimal(str(valor).strip().replace(",", "").replace("Q", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ratio(a, b):
    ordenado = lambda s: " ".join(sorted(s.split()))
    return 100 * max(SequenceMatcher(None, a, b).ratio(),
                     SequenceMatcher(None, ordenado(a), ordenado(b)).ratio())


def _afinidad(a, b):
    """Afinidad 0-100 entre dos nombres ya normalizados, tolerante a segundos nombres/apellidos omitidos
    y a errores de dedo: cada palabra del nombre más corto debe aparecer (casi igual) en el más largo."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    corto, largo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    libres, hallados = list(largo), 0
    for t in corto:
        mejor = max(libres, key=lambda x: SequenceMatcher(None, t, x).ratio(), default=None)
        if mejor is not None and SequenceMatcher(None, t, mejor).ratio() >= 0.8:
            hallados += 1
            libres.remove(mejor)
    por_palabras = 100 * hallados / len(corto) if len(corto) >= 2 else 0.0
    return max(por_palabras, _ratio(a, b))


def _mejores(consulta, nombres, limite):
    """[(nombre, puntaje, indice)] de mayor a menor similitud."""
    if process is not None:
        return [(n, float(s), i) for n, s, i in process.extract(consulta, nombres, scorer=fuzz.WRatio, limit=limite)]
    puntos = sorted(((_ratio(consulta, n), i) for i, n in enumerate(nombres)), reverse=True)[:limite]
    return [(nombres[i], s, i) for s, i in puntos]


def traza_texto(traza_json):
    """'["q0","R",["q1"],...]' -> 'q0 —R→ q1 —N→ q2 …' (para mostrar en pantalla)."""
    try:
        t = json.loads(traza_json) if isinstance(traza_json, str) else traza_json
        partes = [t[0]]
        for simbolo, estados in zip(t[1::2], t[2::2]):
            partes.append(f"—{simbolo}→ {'/'.join(estados) if isinstance(estados, list) else estados}")
        return " ".join(partes)
    except (TypeError, ValueError, IndexError):
        return ""


# ---------- lectura de archivos (Excel/CSV con encabezado en cualquier fila) ----------
def _mapear(fila_norm):
    m = {}
    for j, n in enumerate(fila_norm):
        if not n:
            continue
        if "recibo" in n:
            m.setdefault("recibo", j)
        elif n.startswith("nombre") or n in ("participante", "estudiante"):
            m.setdefault("nombre", j)
        elif "monto" in n:
            m.setdefault("monto", j)
        elif "fecha" in n:
            m.setdefault("fecha", j)
        elif "concepto" in n:
            m.setdefault("concepto", j)
        elif n.startswith("carn") or n in ("matricula", "id"):
            m.setdefault("carnet", j)
        elif n.startswith("correo") or n in ("email", "mail"):
            m.setdefault("correo", j)
        elif "ticket" in n or n in ("boleto", "codigo", "codigoqr", "identificador"):
            m.setdefault("ticket", j)
    return m


def _tipo(m):
    if "recibo" in m and "nombre" in m:
        return "pagos"
    if "carnet" in m and "nombre" in m:
        return "estudiantes"
    if "ticket" in m and "nombre" not in m and "recibo" not in m:
        return "tickets"
    return None


def _celda(v):
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    return s[:-2] if s.endswith(".0") and s[:-2].replace("-", "").isdigit() else s


def leer_archivo(archivo):
    """
    Devuelve [(tipo, hoja, filas)] con tipo = pagos | estudiantes | tickets.
    Busca el encabezado entre las primeras 60 filas de cada hoja (el reporte de Tesorería trae membrete).
    """
    nombre = archivo.filename.lower()
    if nombre.endswith(".csv"):
        try:
            hojas = {"CSV": pd.read_csv(archivo, dtype=str, header=None, encoding="utf-8-sig")}
        except UnicodeDecodeError:
            archivo.stream.seek(0)
            hojas = {"CSV": pd.read_csv(archivo, dtype=str, header=None, encoding="latin-1")}
    else:
        hojas = pd.read_excel(archivo, sheet_name=None, dtype=str, header=None)
    salida = []
    for hoja, df in hojas.items():
        df = df.fillna("")
        for i in range(min(len(df), 60)):
            m = _mapear([normalizar(c).replace(" ", "") for c in df.iloc[i]])
            tipo = _tipo(m)
            if not tipo:
                continue
            filas = []
            for _, fila in df.iloc[i + 1:].iterrows():
                r = {k: _celda(fila.iloc[j]) for k, j in m.items()}
                if any(r.values()):
                    filas.append(r)
            salida.append((tipo, hoja, filas))
            break
    return salida


# ---------- AFND ----------
class Automata:
    NOMBRES = {
        "q0": "Esperando recibo", "q1": "Recibo localizado", "q2": "Nombre extraído",
        "q3": "Preparando comparación", "q4": "Comparando estudiantes",
        "q5.1": "Coincidencia única alta", "q5.2": "Varias coincidencias altas",
        "q5.3": "Sin coincidencia suficiente", "q5.4": "Coincidencia intermedia",
        "q6": "Revisión manual", "q7": "Pago aceptado", "qE": "Error / rechazado",
    }
    DELTA = {
        ("q0", "R"): {"q1"}, ("q0", "X"): {"qE"},
        ("q1", "N"): {"q2"}, ("q1", "X"): {"qE"},
        ("q2", "C"): {"q3"}, ("q3", "C"): {"q4"},
        ("q4", "S1"): {"q5.1"}, ("q4", "S2"): {"q5.2"},
        ("q4", "S3"): {"q5.3"}, ("q4", "S4"): {"q5.4"},
        ("q5.1", "V"): {"q7"}, ("q5.1", "M"): {"q6"},  # M desde q5.1: el carnet escrito no coincide
        ("q5.2", "M"): {"q6"}, ("q5.3", "M"): {"q6"}, ("q5.4", "M"): {"q6"},
        ("q6", "V"): {"q7"}, ("q6", "X"): {"qE"},
    }

    def __init__(self):
        self.reiniciar()

    def reiniciar(self):
        self.actuales = {"q0"}
        self.traza = ["q0"]

    def leer(self, simbolo):
        siguientes = set()
        for q in sorted(self.actuales):
            siguientes |= self.DELTA.get((q, simbolo), set())
        self.actuales = siguientes or {"qE"}
        self.traza.extend([simbolo, sorted(self.actuales)])
        return self.actuales

    def reiniciar_a_error(self):
        self.actuales = {"qE"}
        self.traza.extend(["X", ["qE"]])

    def acepta(self):
        return "q7" in self.actuales


class AFND(Automata):
    """Relaciona un pago de Tesorería con el listado oficial de estudiantes."""

    def __init__(self, estudiantes):
        super().__init__()
        self.estudiantes = estudiantes
        self.nombres = [normalizar(e["nombre"]) for e in estudiantes]

    @staticmethod
    def clasificar(candidatos):
        if not candidatos:
            return "S3"
        if candidatos[0][1] >= UMBRAL_ALTO:
            return "S2" if len(candidatos) > 1 and candidatos[1][1] >= UMBRAL_ALTO else "S1"
        return "S4" if candidatos[0][1] >= UMBRAL_MIN else "S3"

    def _cands(self, items):
        return [{"carnet": self.estudiantes[i]["carnet"], "nombre": self.estudiantes[i]["nombre"],
                 "puntaje": round(p, 1)} for _, p, i in items]

    def _resultado(self, estado, motivo, estudiante=None, candidatos=()):
        return {"estado": estado, "motivo": motivo, "estudiante": estudiante,
                "candidatos": list(candidatos), "traza": self.traza}

    def evaluar(self, pago, aceptados, carnet_ingresado=None):
        self.reiniciar()
        if not str(pago.get("recibo", "")).strip() or not normalizar(pago.get("nombre", "")):
            self.leer("X")
            return self._resultado("INCOMPLETO", "Falta el número de recibo o el nombre en el reporte.")
        self.leer("R")
        monto = _decimal(pago.get("monto"))
        if monto is None:
            self.leer("X")
            return self._resultado("INCOMPLETO", "El reporte no trae un monto legible.")
        if monto != MONTO_CORRECTO:
            self.leer("X")
            return self._resultado("MONTO_INCORRECTO", f"Monto {monto} distinto del esperado ({MONTO_CORRECTO}).")
        self.leer("N")
        consulta = normalizar(pago["nombre"])
        self.leer("C")
        self.leer("C")
        todos = _mejores(consulta, self.nombres, LIMITE_CANDIDATOS)
        # Si el estudiante ya escribió su carnet, se compara el nombre del recibo SOLO con el de ese carnet.
        if carnet_ingresado and str(carnet_ingresado).strip():
            k = str(carnet_ingresado).strip().lower()
            idx = next((i for i, e in enumerate(self.estudiantes) if e["carnet"].lower() == k), None)
            if idx is not None:
                puntaje = _afinidad(consulta, self.nombres[idx])
                self.leer("S1" if puntaje >= UMBRAL_CARNET else "S4")
                est = self.estudiantes[idx]
                cands = self._cands([(est["nombre"], puntaje, idx)])
                if puntaje >= UMBRAL_CARNET:
                    if est["carnet"] in aceptados:
                        self.reiniciar_a_error()
                        return self._resultado("DUPLICADO", f"{est['nombre']} ya tiene un pago validado con otro recibo.",
                                               candidatos=cands)
                    self.leer("V")
                    return self._resultado("VALIDADO", f"Nombre del recibo coincide con el carnet ({puntaje:.0f}%).", est, cands)
                self.leer("M")
                return self._resultado("REVISION", f"El nombre del recibo no coincide lo suficiente con el carnet ({puntaje:.0f}%).",
                                       None, cands)
        # q4: se descartan los estudiantes que ya tienen un pago validado
        libres = [i for i, e in enumerate(self.estudiantes) if e["carnet"] not in aceptados]
        top = []
        if libres:
            sub = _mejores(consulta, [self.nombres[i] for i in libres], LIMITE_CANDIDATOS)
            top = [(n, p, libres[k]) for n, p, k in sub]
        # ¿el pagador es alguien que ya pagó? -> pago duplicado
        if todos and todos[0][1] >= UMBRAL_ALTO and self.estudiantes[todos[0][2]]["carnet"] in aceptados \
                and not (top and top[0][1] >= UMBRAL_ALTO):
            self.leer("X")
            e = self.estudiantes[todos[0][2]]
            return self._resultado("DUPLICADO", f"{e['nombre']} ya tiene un pago validado con otro recibo.",
                                   candidatos=self._cands(todos[:1]))
        simbolo = self.clasificar(top)
        self.leer(simbolo)
        cands = self._cands(top)
        if simbolo == "S1":
            est = self.estudiantes[top[0][2]]
            if carnet_ingresado and str(carnet_ingresado).strip().lower() != est["carnet"].lower():
                self.leer("M")
                return self._resultado("REVISION", "El carnet escrito no coincide con el nombre del recibo.", None, cands)
            self.leer("V")
            return self._resultado("VALIDADO", f"Coincidencia única ({top[0][1]:.0f}%).", est, cands)
        self.leer("M")
        motivos = {"S2": "Varios estudiantes con coincidencia alta.", "S3": "Ningún estudiante alcanza la similitud mínima.",
                   "S4": "Coincidencia intermedia: falta confirmar la identidad."}
        return self._resultado("REVISION", motivos[simbolo], None, cands)


# ---------- almacenamiento ----------
class _Bloqueo:
    """Candado reentrante entre hilos y (en Linux) entre procesos de gunicorn."""

    def __init__(self, ruta):
        self.ruta, self.rlock, self.nivel, self.fd = ruta, threading.RLock(), 0, None

    @contextlib.contextmanager
    def __call__(self):
        with self.rlock:
            if self.nivel == 0 and fcntl:
                self.fd = open(self.ruta, "a")
                fcntl.flock(self.fd, fcntl.LOCK_EX)
            self.nivel += 1
            try:
                yield
            finally:
                self.nivel -= 1
                if self.nivel == 0 and self.fd:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                    self.fd.close()
                    self.fd = None


class Almacen:
    def __init__(self, data_dir):
        self.f_part = os.path.join(data_dir, "participantes.csv")
        self.f_pagos = os.path.join(data_dir, "pagos.csv")
        self.f_tickets = os.path.join(data_dir, "tickets.csv")
        self.bloqueo = _Bloqueo(os.path.join(data_dir, ".lock"))

    # --- E/S ---
    @staticmethod
    def _leer(ruta, cols):
        if not os.path.exists(ruta):
            return []
        df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
        for c in cols:
            if c not in df:
                df[c] = ""
        return df[cols].to_dict("records")

    @staticmethod
    def _escribir(ruta, filas, cols):
        tmp = ruta + ".tmp"
        pd.DataFrame(filas, columns=cols).to_csv(tmp, index=False)
        os.replace(tmp, ruta)

    def participantes(self):
        return self._leer(self.f_part, COL_PART)

    def pagos(self):
        return self._leer(self.f_pagos, COL_PAGOS)

    def tickets(self):
        return self._leer(self.f_tickets, COL_TICKETS)

    def buscar_participante(self, carnet):
        c = str(carnet).strip().lower()
        return next((p for p in self.participantes() if p["carnet"].lower() == c), None)

    # --- cargas ---
    def importar_estudiantes(self, filas, reemplazar=True):
        with self.bloqueo():
            nuevos = {}
            for f in filas:
                c = f.get("carnet", "").strip()
                if c and c not in nuevos:
                    nuevos[c] = {"carnet": c, "nombre": f.get("nombre", "").strip(), "correo": f.get("correo", "").strip()}
            base = {} if reemplazar else {p["carnet"]: p for p in self.participantes()}
            base.update(nuevos)
            self._escribir(self.f_part, list(base.values()), COL_PART)
            return len(nuevos), len(base)

    def importar_pagos(self, filas):
        """Agrega pagos nuevos (idempotente por recibo). Marca recibos repetidos e incompletos."""
        with self.bloqueo():
            pagos = self.pagos()
            vistos = {p["recibo"] for p in pagos}
            nuevos = omitidos = 0
            en_lote = set()
            for n, f in enumerate(filas, start=1):
                recibo, nombre = f.get("recibo", "").strip(), f.get("nombre", "").strip()
                if not recibo and normalizar(nombre).startswith("total"):
                    continue                   # fila de totales del reporte, no es un pago
                fecha = f.get("fecha", "").strip()[:10]
                fila = {c: "" for c in COL_PAGOS}
                fila.update(fecha=fecha, nombre=nombre, monto=f.get("monto", ""), concepto=f.get("concepto", ""),
                            estado="PENDIENTE", actualizado=_ahora())
                if not recibo:
                    fila["recibo"] = f"SIN-RECIBO-{len(pagos) + n:04d}"
                    fila["estado"] = afd_pago("PENDIENTE", "incompleto")
                    fila["motivo"] = "El reporte no trae número de recibo."
                elif recibo in vistos:
                    if recibo not in en_lote:
                        omitidos += 1          # ya se importó en una carga anterior: se ignora
                        continue
                    k = 2
                    while f"{recibo}#{k}" in vistos:
                        k += 1
                    fila["recibo"] = f"{recibo}#{k}"
                    fila["estado"] = afd_pago("PENDIENTE", "duplicar")
                    fila["motivo"] = f"El recibo {recibo} aparece más de una vez en el reporte."
                else:
                    fila["recibo"] = recibo
                    en_lote.add(recibo)
                    if not nombre:
                        fila["estado"] = afd_pago("PENDIENTE", "incompleto")
                        fila["motivo"] = "El reporte no trae nombre."
                pagos.append(fila)
                vistos.add(fila["recibo"])
                nuevos += 1
            self._escribir(self.f_pagos, pagos, COL_PAGOS)
            return nuevos, omitidos

    def importar_tickets(self, codigos):
        with self.bloqueo():
            tickets = self.tickets()
            existentes = {t["ticket"] for t in tickets}
            nuevos = 0
            for c in codigos:
                c = c.strip()
                if c and c not in existentes:
                    tickets.append({"ticket": c, "estado": "DISPONIBLE", "carnet": "", "recibo": "",
                                    "fecha_asignacion": "", "fecha_uso": "", "correo_enviado": ""})
                    existentes.add(c)
                    nuevos += 1
            self._escribir(self.f_tickets, tickets, COL_TICKETS)
            return nuevos, len(codigos) - nuevos

    def tickets_de_prueba(self, cantidad, prefijo="SIM-"):
        return self.importar_tickets([f"{prefijo}{i:04d}" for i in range(1, cantidad + 1)])

    def vaciar_pagos(self):
        with self.bloqueo():
            if any(t["estado"] != "DISPONIBLE" for t in self.tickets()):
                return False
            self._escribir(self.f_pagos, [], COL_PAGOS)
            return True

    # --- núcleo: evaluar un pago y asignar ticket ---
    @staticmethod
    def _aplicar(pago, res):
        pago["estado"] = afd_pago(pago["estado"], {"VALIDADO": "validar", "REVISION": "revisar", "DUPLICADO": "duplicar",
                                                  "INCOMPLETO": "incompleto", "MONTO_INCORRECTO": "monto"}[res["estado"]])
        pago["carnet"] = res["estudiante"]["carnet"] if res["estudiante"] else ""
        pago["motivo"] = res["motivo"]
        pago["candidatos"] = json.dumps(res["candidatos"], ensure_ascii=False)
        pago["traza"] = json.dumps(res["traza"], ensure_ascii=False)
        pago["actualizado"] = _ahora()

    @staticmethod
    def _asignar_ticket(pago, tickets):
        """2.º momento: toma un ticket DISPONIBLE ya cargado por Coordinación y lo liga al pago."""
        if pago["ticket"]:
            return pago["ticket"]
        previo = next((t for t in tickets if t["carnet"] == pago["carnet"]), None)
        if previo:                                  # un estudiante, un ticket
            pago["ticket"] = previo["ticket"]
            return previo["ticket"]
        libre = next((t for t in tickets if t["estado"] == "DISPONIBLE"), None)
        if libre is None:
            return ""
        libre.update(estado=afd_ticket("DISPONIBLE", "asignar"), carnet=pago["carnet"], recibo=pago["recibo"],
                     fecha_asignacion=_ahora())
        pago["ticket"] = libre["ticket"]
        return libre["ticket"]

    def _procesar(self, afnd, pago, pagos, tickets, carnet_ingresado=None):
        aceptados = {p["carnet"] for p in pagos if p["estado"] == "VALIDADO" and p["carnet"]}
        res = afnd.evaluar(pago, aceptados, carnet_ingresado)
        self._aplicar(pago, res)
        if pago["estado"] == "VALIDADO":
            self._asignar_ticket(pago, tickets)
        return res

    def conciliar(self):
        """Procesa todos los pagos PENDIENTES (1.er momento, en lote). Devuelve el conteo por estado."""
        with self.bloqueo():
            pagos, tickets, est = self.pagos(), self.tickets(), self.participantes()
            if not est:
                return None
            afnd = AFND(est)
            conteo = {}
            for p in pagos:
                if p["estado"] == "PENDIENTE":
                    self._procesar(afnd, p, pagos, tickets)
                    conteo[p["estado"]] = conteo.get(p["estado"], 0) + 1
            # validados que quedaron sin ticket (p. ej. faltaban tickets) vuelven a intentarlo
            for p in pagos:
                if p["estado"] == "VALIDADO" and not p["ticket"]:
                    self._asignar_ticket(p, tickets)
            self._escribir(self.f_pagos, pagos, COL_PAGOS)
            self._escribir(self.f_tickets, tickets, COL_TICKETS)
            return conteo

    def resolver_revision(self, recibo, aceptar, carnet=""):
        with self.bloqueo():
            pagos, tickets = self.pagos(), self.tickets()
            pago = next((p for p in pagos if p["recibo"] == recibo), None)
            if pago is None or pago["estado"] != "REVISION":
                return False, "Ese pago ya no está en revisión."
            if aceptar:
                est = next((e for e in self.participantes() if e["carnet"].lower() == carnet.strip().lower()), None)
                if est is None:
                    return False, f"El carnet «{carnet}» no está en el listado oficial."
                if any(p["estado"] == "VALIDADO" and p["carnet"] == est["carnet"] for p in pagos):
                    return False, f"{est['nombre']} ya tiene otro pago validado."
                pago["estado"] = afd_pago("REVISION", "validar")
                pago["carnet"], pago["motivo"] = est["carnet"], "Aceptado en revisión manual."
                t = self._asignar_ticket(pago, tickets)
                msg = f"Pago {recibo} validado para {est['nombre']}" + (f" · ticket {t}." if t else " (sin tickets disponibles).")
            else:
                pago["estado"] = afd_pago("REVISION", "rechazar")
                pago["motivo"] = "Rechazado en revisión manual."
                msg = f"Pago {recibo} rechazado."
            pago["actualizado"] = _ahora()
            self._escribir(self.f_pagos, pagos, COL_PAGOS)
            self._escribir(self.f_tickets, tickets, COL_TICKETS)
            return True, msg

    def solicitar_boleto(self, carnet, recibo):
        """Primer momento desde el lado del estudiante. Devuelve dict(estado, msg, ticket, nombre)."""
        carnet, recibo = str(carnet).strip(), str(recibo).strip()
        out = {"estado": "", "msg": "", "ticket": "", "nombre": "", "carnet": carnet, "recibo": recibo}
        if not carnet or not recibo:
            out.update(estado="INCOMPLETA", msg="Información incompleta: escribe tu carnet y tu número de recibo.")
            return out
        with self.bloqueo():
            pagos, tickets, est = self.pagos(), self.tickets(), self.participantes()
            persona = next((e for e in est if e["carnet"].lower() == carnet.lower()), None)
            pago = next((p for p in pagos if p["recibo"] == recibo), None)
            if persona is None or pago is None:
                out.update(estado="NO_LOCALIZADO", msg="Pago no localizado: revisa que tu carnet y tu recibo estén bien escritos.")
                return out
            carnet = out["carnet"] = persona["carnet"]
            out["nombre"] = persona["nombre"]
            if pago["estado"] == "PENDIENTE":
                self._procesar(AFND(est), pago, pagos, tickets, carnet_ingresado=carnet)
            cambio = pago["estado"] != "PENDIENTE"
            if pago["estado"] == "VALIDADO":
                if pago["carnet"] != carnet:
                    out.update(estado="DUPLICADO", msg="Este recibo ya está asociado a otro estudiante.")
                else:
                    if not pago["ticket"]:
                        self._asignar_ticket(pago, tickets)
                    if pago["ticket"]:
                        out.update(estado="VALIDADO", ticket=pago["ticket"], msg="Pago validado. Este es tu boleto de ingreso.")
                    else:
                        out.update(estado="SIN_TICKET", msg="Pago validado, pero Coordinación aún no tiene tickets disponibles. Intenta más tarde.")
            else:
                msgs = {"REVISION": "Más de una posible coincidencia o datos por confirmar: tu registro fue enviado a revisión manual.",
                        "DUPLICADO": "Pago duplicado: este recibo o estudiante ya tiene un pago registrado.",
                        "INCOMPLETO": "Información incompleta en el reporte de Tesorería: enviado a revisión.",
                        "MONTO_INCORRECTO": "El monto del recibo no es el correcto: contacta a Coordinación.",
                        "RECHAZADO": "Este pago fue rechazado en la revisión manual: contacta a Coordinación."}
                out.update(estado=pago["estado"], msg=msgs.get(pago["estado"], pago["estado"]))
            if cambio:
                self._escribir(self.f_pagos, pagos, COL_PAGOS)
                self._escribir(self.f_tickets, tickets, COL_TICKETS)
            return out

    # --- 3.er momento: ingreso ---
    def validar_ingreso(self, codigo):
        codigo = str(codigo).strip()
        r = {"autorizado": False, "estado": "", "nombre": "", "carnet": "", "estado_pago": "", "ticket": codigo,
             "mensaje": "", "traza": []}
        if not codigo:
            r.update(estado="VACIO", mensaje="Escribe o escanea un ticket.")
            return r
        with self.bloqueo():
            tickets, pagos = self.tickets(), self.pagos()
            t = next((x for x in tickets if x["ticket"] == codigo), None) or \
                next((x for x in tickets if x["ticket"].lower() == codigo.lower()), None)
            if t is None:
                r.update(estado="INEXISTENTE", mensaje="Ticket inexistente. Ingreso rechazado.")
                return r
            pago = next((p for p in pagos if p["recibo"] == t["recibo"]), None) if t["recibo"] else None
            persona = self.buscar_participante(t["carnet"]) if t["carnet"] else None
            r.update(ticket=t["ticket"], carnet=t["carnet"], nombre=persona["nombre"] if persona else "",
                     estado_pago=pago["estado"] if pago else "SIN PAGO")
            antes = t["estado"]
            if antes == "DISPONIBLE":
                r.update(estado="NO_ASIGNADO", mensaje="Ticket no asignado a ningún estudiante. Ingreso rechazado.")
            elif antes == "UTILIZADO":
                r.update(estado="YA_UTILIZADO", mensaje=f"Ticket ya utilizado el {t['fecha_uso']}. Ingreso rechazado.")
            elif pago is None or pago["estado"] != "VALIDADO":
                r.update(estado="PAGO_NO_VALIDO", mensaje="El pago de este ticket no está validado. Ingreso rechazado.")
            else:
                t["estado"] = afd_ticket(antes, "ingresar")
                t["fecha_uso"] = _ahora()
                self._escribir(self.f_tickets, tickets, COL_TICKETS)
                r.update(autorizado=True, estado="AUTORIZADO", mensaje="Ingreso autorizado. El ticket pasó a UTILIZADO.")
            r["traza"] = [antes, "ingresar", afd_ticket(antes, "ingresar") if r["autorizado"] else antes]
            return r

    # --- correo ---
    def boletos_pendientes_correo(self, solo=None):
        """Tickets ASIGNADOS cuyo boleto aún no se envió y cuyo estudiante tiene correo."""
        personas = {p["carnet"]: p for p in self.participantes()}
        salida = []
        for t in self.tickets():
            p = personas.get(t["carnet"])
            if t["estado"] == "ASIGNADO" and not t["correo_enviado"] and p and "@" in p["correo"] \
                    and (solo is None or t["ticket"] in solo):
                salida.append({"ticket": t["ticket"], "carnet": t["carnet"], "nombre": p["nombre"], "correo": p["correo"].strip()})
        return salida

    def marcar_correo(self, ticket):
        with self.bloqueo():
            tickets = self.tickets()
            for t in tickets:
                if t["ticket"] == ticket:
                    t["correo_enviado"] = _ahora()
            self._escribir(self.f_tickets, tickets, COL_TICKETS)

    # --- resúmenes ---
    def conteos(self):
        pagos, tickets = self.pagos(), self.tickets()
        c = {"participantes": len(self.participantes()), "pagos": len(pagos)}
        for e in ESTADOS_PAGO:
            c[e] = sum(1 for p in pagos if p["estado"] == e)
        for e in ("DISPONIBLE", "ASIGNADO", "UTILIZADO"):
            c["t_" + e] = sum(1 for t in tickets if t["estado"] == e)
        c["tickets"] = len(tickets)
        c["correos_enviados"] = sum(1 for t in tickets if t["correo_enviado"])
        return c

    def ultimos_ingresos(self, limite=50):
        usados = [t for t in self.tickets() if t["estado"] == "UTILIZADO"]
        usados.sort(key=lambda t: t["fecha_uso"], reverse=True)
        nombres = {p["carnet"]: p["nombre"] for p in self.participantes()}
        return [{**t, "nombre": nombres.get(t["carnet"], "")} for t in usados[:limite]]
