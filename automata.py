"""Sistema unificado de validación de pagos, revisión manual y tickets QR.

Base: flujo Excel/AFND y clase de tickets del código nuevo.
Se incorporan: bitácora JSON atómica, cola manual persistente, validación idempotente,
trazas completas y consumo condicional del ticket en una sola sentencia SQL.

Dependencias: pandas, openpyxl, rapidfuzz, mysql-connector-python.

IMPORTANTE: adaptar nombres de columnas/hoja y MYSQL_CONFIG al entorno real.
No se guarda el QR en texto plano en MySQL; la cadena se devuelve una sola vez al emitir.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import mysql.connector
from mysql.connector import Error as MySQLError
from rapidfuzz import process, fuzz

# ---------------------------------------------------------------------------
# CONFIGURACIÓN: revisar estos valores antes de ejecutar
# ---------------------------------------------------------------------------
UMBRAL_ALTO = 95
UMBRAL_MIN = 75
MONTO_CORRECTO = Decimal("300.00")
HOJA_PAGOS = "Pruebas Psicométricas"
COLUMNA_RECIBO = "No. recibo"
COLUMNA_NOMBRE_PAGO = "Nombre"
COLUMNA_NOMBRE_ESTUDIANTE = "Nombre"
COLUMNA_CARNET = "Carnet"
ARCHIVO_PROGRESO = "progreso_simposio.json"
REINTENTOS_DB = 3
ESPERA_REINTENTO = 1.5

# Completar con la configuración de tu servidor. No pongas credenciales en el código
# que vayas a compartir o subir a un repositorio.
MYSQL_CONFIG: dict[str, Any] = {
    "host": os.getenv("SIMPOSIO_DB_HOST", "localhost"),
    "user": os.getenv("SIMPOSIO_DB_USER", "root"),
    "password": os.getenv("SIMPOSIO_DB_PASSWORD", ""),
    "database": os.getenv("SIMPOSIO_DB_NAME", "simposio"),
    "autocommit": False,
}


class ConexionPerdida(RuntimeError):
    """La base no respondió tras los reintentos configurados."""


class YaAceptado(RuntimeError):
    """El carnet ya tiene un ticket/aceptación asociada."""


def normalizar(texto: Any) -> str:
    """Normaliza mayúsculas, tildes y espacios para comparar nombres."""
    if texto is None or (not isinstance(texto, (list, dict)) and pd.isna(texto)):
        return ""
    texto = unicodedata.normalize("NFD", str(texto).lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(texto.split())


def generar_hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _extraer_nombre(entrada: Any) -> str:
    if isinstance(entrada, dict):
        return normalizar(entrada.get(COLUMNA_NOMBRE_ESTUDIANTE, entrada.get("nombre_completo", entrada.get("Nombre", ""))))
    return normalizar(entrada)


def _decimal(valor: Any) -> Optional[Decimal]:
    try:
        texto = str(valor).strip().replace(",", "")
        return Decimal(texto).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


class Progreso:
    """Bitácora persistente con reemplazo atómico para recuperarse de interrupciones."""
    def __init__(self, ruta: str | Path = ARCHIVO_PROGRESO):
        self.ruta = Path(ruta)
        self.datos = self._cargar()

    @staticmethod
    def _vacio() -> dict[str, Any]:
        return {"version": 2, "procesados": {}, "pendientes_manual": {}, "rechazados": {}, "actualizado": None}

    def _cargar(self) -> dict[str, Any]:
        if not self.ruta.exists():
            return self._vacio()
        try:
            with self.ruta.open("r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
            base = self._vacio()
            if isinstance(datos, dict):
                base.update(datos)
            for clave in ("procesados", "pendientes_manual", "rechazados"):
                if not isinstance(base.get(clave), dict):
                    base[clave] = {}
            return base
        except (json.JSONDecodeError, OSError):
            respaldo = self.ruta.with_suffix(self.ruta.suffix + ".corrupto")
            try:
                os.replace(self.ruta, respaldo)
            except OSError:
                pass
            return self._vacio()

    def guardar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.datos["actualizado"] = datetime.now().isoformat(timespec="seconds")
        temporal = self.ruta.with_suffix(self.ruta.suffix + ".tmp")
        with temporal.open("w", encoding="utf-8") as archivo:
            json.dump(self.datos, archivo, ensure_ascii=False, indent=2, default=str)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, self.ruta)

    def registrar(self, clave: str, resultado: str, carnet: Optional[str] = None,
                  traza: Optional[list[Any]] = None, **extra: Any) -> None:
        self.datos["procesados"][str(clave)] = {
            "resultado": resultado, "carnet": carnet, "traza": traza or [],
            "fecha": datetime.now().isoformat(timespec="seconds"), **extra,
        }
        self.guardar()

    def ya_procesado(self, clave: str) -> bool:
        return str(clave) in self.datos["procesados"]

    def agregar_revision(self, recibo: str, datos: dict[str, Any]) -> None:
        # No sobrescribir una revisión existente: conserva la decisión pendiente tras reinicios.
        self.datos["pendientes_manual"].setdefault(str(recibo), datos)
        self.guardar()

    def quitar_revision(self, recibo: str) -> None:
        self.datos["pendientes_manual"].pop(str(recibo), None)
        self.guardar()


class Automata:
    """Autómata de estados finitos con traza determinista y transiciones explícitas."""
    NOMBRES = {
        "q0": "Esperando recibo", "q1": "Recibo localizado", "q2": "Nombre extraído",
        "q3": "Preparando comparación", "q4": "Comparando estudiantes",
        "q5.1": "Coincidencia única alta", "q5.2": "Varias coincidencias altas",
        "q5.3": "Sin coincidencia suficiente", "q5.4": "Coincidencia intermedia",
        "q6": "Revisión manual", "q7": "Pago aceptado", "qE": "Error/rechazado",
    }

    DELTA = {
        ("q0", "R"): {"q1"}, ("q0", "X"): {"qE"},
        ("q1", "N"): {"q2"}, ("q1", "X"): {"qE"},
        ("q2", "C"): {"q3"}, ("q3", "C"): {"q4"},
        ("q4", "S1"): {"q5.1"}, ("q4", "S2"): {"q5.2"},
        ("q4", "S3"): {"q5.3"}, ("q4", "S4"): {"q5.4"},
        ("q5.1", "V"): {"q7"},
        ("q5.2", "M"): {"q6"}, ("q5.3", "M"): {"q6"}, ("q5.4", "M"): {"q6"},
        ("q6", "V"): {"q7"}, ("q6", "X"): {"qE"},
    }

    def __init__(self, inicio: str = "q0"):
        self.inicio = inicio
        self.reiniciar()

    def reiniciar(self) -> None:
        self.actuales = {self.inicio}
        self.traza: list[Any] = [self.inicio]

    def leer(self, simbolo: str) -> set[str]:
        siguientes: set[str] = set()
        for estado in sorted(self.actuales):
            siguientes.update(self.DELTA.get((estado, simbolo), set()))
        if not siguientes:
            siguientes = {"qE"}
        self.actuales = siguientes
        # Registrar todos los estados siguientes; no elegir arbitrariamente uno de un conjunto.
        self.traza.extend([simbolo, sorted(siguientes)])
        return siguientes

    def acepta(self) -> bool:
        return "q7" in self.actuales


class AFNDValidacion(Automata):
    """Valida pagos desde Excel y persiste las revisiones manuales."""
    def __init__(self, ruta_excel_pagos: str, ruta_excel_estudiantes: str,
                 progreso: Optional[Progreso] = None):
        super().__init__("q0")
        self.progreso = progreso or Progreso()
        try:
            self.df_pagos = pd.read_excel(ruta_excel_pagos, sheet_name=HOJA_PAGOS, dtype=str).fillna("")
            self.df_estudiantes = pd.read_excel(ruta_excel_estudiantes, dtype=str).fillna("")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"No se encontró uno de los Excel: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"No se pudo leer la hoja/estructura de Excel: {exc}") from exc

        for columna in (COLUMNA_RECIBO, COLUMNA_NOMBRE_PAGO):
            if columna not in self.df_pagos.columns:
                raise ValueError(f"Falta la columna '{columna}' en el Excel de pagos.")
        for columna in (COLUMNA_NOMBRE_ESTUDIANTE, COLUMNA_CARNET):
            if columna not in self.df_estudiantes.columns:
                raise ValueError(f"Falta la columna '{columna}' en el Excel de estudiantes.")

        self.pagos = self.df_pagos.to_dict("records")
        self.estudiantes = self.df_estudiantes.to_dict("records")
        self.nombres_oficiales = [normalizar(e.get(COLUMNA_NOMBRE_ESTUDIANTE, "")) for e in self.estudiantes]
        self.pendientes_manual = self.progreso.datos["pendientes_manual"]

    def clasificar_similitud(self, candidatos: list[tuple]) -> str:
        if not candidatos:
            return "S3"
        mejor = candidatos[0][1]
        if mejor >= UMBRAL_ALTO:
            if len(candidatos) > 1 and candidatos[1][1] >= UMBRAL_ALTO:
                return "S2"
            return "S1"
        if mejor >= UMBRAL_MIN:
            return "S4"
        return "S3"

    def _buscar_pago(self, no_recibo: str) -> Optional[dict[str, Any]]:
        coincidencias = self.df_pagos[self.df_pagos[COLUMNA_RECIBO].astype(str).str.strip() == no_recibo]
        return None if coincidencias.empty else coincidencias.iloc[0].to_dict()

    def procesar_pago(self, datos_ingresados: dict[str, Any], recibos_ya_procesados: Optional[set[str]] = None) -> dict[str, Any]:
        self.reiniciar()
        no_recibo = str(datos_ingresados.get("no_recibo", "")).strip()
        procesados_memoria = {str(x) for x in (recibos_ya_procesados or set())}
        if not no_recibo:
            self.leer("X")
            return {"estado": "Información incompleta", "traza": self.traza, "estudiante": None}
        if no_recibo in procesados_memoria or self.progreso.ya_procesado(no_recibo):
            self.leer("X")
            return {"estado": "Pago duplicado o ya procesado", "traza": self.traza, "estudiante": None, "no_recibo": no_recibo}
        if no_recibo in self.pendientes_manual:
            return {"estado": "Registro pendiente de revisión manual", "traza": self.pendientes_manual[no_recibo].get("traza", []),
                    "estudiante": None, "candidatos": self.pendientes_manual[no_recibo].get("candidatos", []), "no_recibo": no_recibo}

        self.leer("R")
        pago = self._buscar_pago(no_recibo)
        if pago is None:
            self.leer("X")
            return {"estado": "Pago no localizado", "traza": self.traza, "estudiante": None, "no_recibo": no_recibo}

        # Si el Excel incluye monto, comprobarlo. Si no existe, no inventar un valor.
        if "Monto" in pago or "monto" in pago:
            monto = _decimal(pago.get("Monto", pago.get("monto")))
            if monto != MONTO_CORRECTO:
                self.leer("X")
                resultado = {"estado": "Monto incorrecto", "traza": self.traza, "estudiante": None, "no_recibo": no_recibo}
                self.progreso.registrar(no_recibo, "MONTO_INCORRECTO", traza=self.traza)
                return resultado

        self.leer("N")
        nombre_pago = normalizar(pago.get(COLUMNA_NOMBRE_PAGO, ""))
        self.leer("C")
        self.leer("C")
        resultados = process.extract(nombre_pago, self.nombres_oficiales, scorer=fuzz.WRatio, limit=3)
        # Excluir candidatos cuyo carnet ya fue aceptado por el propio sistema.
        carnets_aceptados = {str(v.get("carnet")) for v in self.progreso.datos["procesados"].values() if v.get("resultado") in {"PAGO_VALIDADO", "MANUAL_ACEPTADO"} and v.get("carnet")}
        resultados = [r for r in resultados if str(self.estudiantes[r[2]].get(COLUMNA_CARNET, "")) not in carnets_aceptados]
        simbolo = self.clasificar_similitud(resultados)
        self.leer(simbolo)

        if simbolo == "S1":
            self.leer("V")
            estudiante = self.estudiantes[resultados[0][2]]
            self.progreso.registrar(no_recibo, "PAGO_VALIDADO", str(estudiante.get(COLUMNA_CARNET, "")), self.traza,
                                    nombre=estudiante.get(COLUMNA_NOMBRE_ESTUDIANTE, ""))
            return {"estado": "Pago validado", "traza": self.traza, "estudiante": estudiante, "no_recibo": no_recibo}

        self.leer("M")
        candidatos = [{"indice": int(idx), "nombre": str(nombre), "puntaje": float(puntaje),
                       "carnet": str(self.estudiantes[idx].get(COLUMNA_CARNET, ""))}
                      for nombre, puntaje, idx in resultados]
        revision = {"no_recibo": no_recibo, "nombre_pagador": nombre_pago, "simbolo": simbolo,
                    "candidatos": candidatos, "traza": self.traza,
                    "fecha": datetime.now().isoformat(timespec="seconds")}
        self.progreso.agregar_revision(no_recibo, revision)
        return {"estado": "Registro enviado a revisión manual", "traza": self.traza,
                "estudiante": None, "candidatos": candidatos, "no_recibo": no_recibo}

    def listar_revisiones(self) -> list[dict[str, Any]]:
        return list(self.progreso.datos["pendientes_manual"].values())

    def resolver_manual(self, no_recibo: str, aceptado: bool, idx_estudiante: Optional[int] = None) -> dict[str, Any]:
        """Resuelve una revisión por recibo; no depende del estado actual del autómata."""
        clave = str(no_recibo).strip()
        revision = self.progreso.datos["pendientes_manual"].get(clave)
        if revision is None:
            return {"ok": False, "error": "No existe una revisión pendiente para ese recibo."}

        self.reiniciar()
        # Reproducir la ruta de revisión para que la traza documente la decisión.
        self.traza = list(revision.get("traza", ["q0"]))
        if aceptado:
            if idx_estudiante is None or not 0 <= idx_estudiante < len(self.estudiantes):
                return {"ok": False, "error": "Índice de estudiante inválido."}
            estudiante = self.estudiantes[idx_estudiante]
            carnet = str(estudiante.get(COLUMNA_CARNET, ""))
            if not carnet:
                return {"ok": False, "error": "El estudiante seleccionado no tiene carnet."}
            if any(str(v.get("carnet")) == carnet and v.get("resultado") in {"PAGO_VALIDADO", "MANUAL_ACEPTADO"}
                   for v in self.progreso.datos["procesados"].values()):
                return {"ok": False, "error": f"El carnet {carnet} ya está asociado a otro recibo."}
            # Una sola escritura atómica: no dejar el recibo sin revisión ni resultado si hay un corte.
            ahora = datetime.now().isoformat(timespec="seconds")
            self.progreso.datos["pendientes_manual"].pop(clave, None)
            self.progreso.datos["procesados"][clave] = {
                "resultado": "MANUAL_ACEPTADO", "carnet": carnet, "traza": self.traza,
                "fecha": ahora, "nombre": estudiante.get(COLUMNA_NOMBRE_ESTUDIANTE, "")
            }
            self.progreso.guardar()
            return {"ok": True, "estado": "MANUAL_ACEPTADO", "estudiante": estudiante, "no_recibo": clave, "traza": self.traza}

        ahora = datetime.now().isoformat(timespec="seconds")
        self.progreso.datos["pendientes_manual"].pop(clave, None)
        self.progreso.datos["rechazados"][clave] = {"motivo": "RECHAZO_MANUAL", "fecha": ahora}
        self.progreso.datos["procesados"][clave] = {
            "resultado": "MANUAL_RECHAZADO", "carnet": None, "traza": self.traza, "fecha": ahora
        }
        self.progreso.guardar()
        return {"ok": True, "estado": "MANUAL_RECHAZADO", "estudiante": None, "no_recibo": clave, "traza": self.traza}


class AFDTickets(Automata):
    """Emite tickets en MySQL y valida el primer ingreso de forma atómica."""
    DELTA = {
        ("q0", "g"): {"q1"}, ("q1", "e"): {"q2"},
        ("q2", "v"): {"q3"}, ("q2", "u"): {"qE"}, ("q3", "e"): {"qE"},
    }
    DELTA_TICKET = DELTA

    def __init__(self, db_config: Optional[dict[str, Any]] = None, asegurar_tabla: bool = True):
        super().__init__("q0")
        self.db_config = dict(db_config or MYSQL_CONFIG)
        self.db = None
        self.conectar()
        if asegurar_tabla:
            self._asegurar_tabla()

    def conectar(self) -> None:
        try:
            if self.db is not None and self.db.is_connected():
                self.db.ping(reconnect=True, attempts=REINTENTOS_DB, delay=ESPERA_REINTENTO)
                return
        except MySQLError:
            try:
                self.db.close()
            except Exception:
                pass
        ultimo = None
        for intento in range(REINTENTOS_DB):
            try:
                self.db = mysql.connector.connect(**self.db_config)
                return
            except MySQLError as exc:
                ultimo = exc
                if intento + 1 < REINTENTOS_DB:
                    time.sleep(ESPERA_REINTENTO * (intento + 1))
        raise ConexionPerdida(f"No se pudo conectar a MySQL: {ultimo}")

    def _asegurar_tabla(self) -> None:
        self.conectar()
        cursor = self.db.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS control_tickets (
                    hash_ticket CHAR(64) PRIMARY KEY,
                    carnet VARCHAR(50) NOT NULL,
                    nombre VARCHAR(150) NOT NULL,
                    estado VARCHAR(20) NOT NULL DEFAULT 'DISPONIBLE',
                    fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    fecha_uso DATETIME NULL,
                    UNIQUE KEY uq_control_tickets_carnet (carnet),
                    INDEX idx_control_tickets_estado (estado)
                ) ENGINE=InnoDB
            """)
            self.db.commit()
        except MySQLError:
            self.db.rollback()
            raise
        finally:
            cursor.close()

    def cerrar_conexion(self) -> None:
        if self.db is not None:
            try:
                if self.db.is_connected():
                    self.db.close()
            finally:
                self.db = None

    @staticmethod
    def _iniciales(nombre: str) -> str:
        partes = normalizar(nombre).split()
        return "".join(p[0] for p in partes[:2]).upper() if partes else "XX"

    def asignar_ticket(self, estudiante: dict[str, Any]) -> str:
        """Inserta hash único y devuelve el QR en texto plano una sola vez."""
        self.conectar()
        carnet = str(estudiante.get(COLUMNA_CARNET, estudiante.get("carnet", ""))).strip()
        nombre = str(estudiante.get(COLUMNA_NOMBRE_ESTUDIANTE, estudiante.get("nombre_completo", "Sin nombre"))).strip()
        if not carnet:
            raise ValueError("El estudiante no tiene carnet; no se puede emitir un ticket.")
        cursor = self.db.cursor()
        try:
            # No reemitir silenciosamente: el hash guardado no permite recuperar el QR original.
            cursor.execute("SELECT 1 FROM control_tickets WHERE carnet = %s", (carnet,))
            if cursor.fetchone():
                raise YaAceptado(f"El carnet {carnet} ya tiene un ticket registrado.")
            for _ in range(3):
                codigo = f"{self._iniciales(nombre)}{carnet}-{secrets.token_urlsafe(24)}"
                hash_codigo = generar_hash(codigo)
                try:
                    cursor.execute("INSERT INTO control_tickets (hash_ticket, carnet, nombre, estado) VALUES (%s, %s, %s, 'DISPONIBLE')",
                                   (hash_codigo, carnet, nombre))
                    self.db.commit()
                    self.leer("g")
                    return codigo
                except mysql.connector.IntegrityError:
                    self.db.rollback()
            raise RuntimeError("No fue posible generar un ticket único tras varios intentos.")
        except Exception:
            self.db.rollback()
            raise
        finally:
            cursor.close()

    def validar_ingreso(self, ticket_escaneado: str) -> dict[str, Any]:
        self.reiniciar()
        respuesta = {"nombre": None, "carnet": None, "estado_pago": None,
                     "autorizado": False, "mensaje": "", "traza": self.traza}
        if not ticket_escaneado or not str(ticket_escaneado).strip():
            self.actuales = {"qE"}
            respuesta["mensaje"] = "Ticket vacío."
            respuesta["traza"] = self.traza
            return respuesta
        try:
            self.conectar()
            hash_ingreso = generar_hash(str(ticket_escaneado).strip())
            cursor = self.db.cursor(dictionary=True)
            try:
                cursor.execute("SELECT carnet, nombre, estado FROM control_tickets WHERE hash_ticket = %s", (hash_ingreso,))
                registro = cursor.fetchone()
                if not registro:
                    self.actuales = {"qE"}
                    respuesta["mensaje"] = "Ticket inválido o inexistente."
                    respuesta["traza"] = self.traza
                    return respuesta
                respuesta.update({"nombre": registro["nombre"], "carnet": registro["carnet"], "estado_pago": "VALIDADO"})
                self.actuales = {"q1"}
                self.leer("e")
                # UPDATE condicional: dos lectores concurrentes no pueden consumir el mismo ticket.
                cursor.execute("UPDATE control_tickets SET estado='UTILIZADO', fecha_uso=CURRENT_TIMESTAMP "
                               "WHERE hash_ticket=%s AND estado='DISPONIBLE'", (hash_ingreso,))
                if cursor.rowcount == 1:
                    self.db.commit()
                    self.leer("v")
                    respuesta["autorizado"] = True
                    respuesta["mensaje"] = "Acceso autorizado. Ticket marcado como utilizado."
                else:
                    self.db.rollback()
                    self.actuales = {"q3"}
                    self.leer("e")
                    respuesta["mensaje"] = "Acceso denegado: el ticket ya fue utilizado."
                respuesta["traza"] = self.traza
                return respuesta
            finally:
                cursor.close()
        except MySQLError as exc:
            try:
                self.db.rollback()
            except Exception:
                pass
            self.actuales = {"qE"}
            respuesta["mensaje"] = f"Base de tickets no disponible: {exc}"
            respuesta["traza"] = self.traza
            return respuesta

    def emitir_para_aceptado(self, resultado_pago: dict[str, Any]) -> dict[str, Any]:
        if resultado_pago.get("estado") not in {"Pago validado", "MANUAL_ACEPTADO"}:
            return {"ok": False, "mensaje": "El pago no está aceptado; no se emitió ticket."}
        estudiante = resultado_pago.get("estudiante")
        if not estudiante:
            return {"ok": False, "mensaje": "Falta el estudiante aceptado."}
        try:
            codigo = self.asignar_ticket(estudiante)
            return {"ok": True, "carnet": str(estudiante.get(COLUMNA_CARNET, estudiante.get("carnet", ""))),
                    "codigo_qr": codigo, "mensaje": "Ticket creado. Guarda/envía el código ahora; no se almacena en texto plano."}
        except (YaAceptado, ValueError, MySQLError, ConexionPerdida, RuntimeError) as exc:
            return {"ok": False, "mensaje": str(exc)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sistema unificado del simposio")
    parser.add_argument("--pagos", help="Ruta del Excel de pagos")
    parser.add_argument("--estudiantes", help="Ruta del Excel de estudiantes")
    parser.add_argument("--progreso", default=ARCHIVO_PROGRESO, help="Archivo JSON de progreso")
    parser.add_argument("--accion", choices=["revisiones", "validar-ticket"], help="Acción administrativa simple")
    parser.add_argument("--ticket", default="", help="Código de ticket a validar")
    args = parser.parse_args()

    if args.accion == "validar-ticket":
        sistema_tickets = AFDTickets()
        try:
            print(json.dumps(sistema_tickets.validar_ingreso(args.ticket), ensure_ascii=False, indent=2))
        finally:
            sistema_tickets.cerrar_conexion()
    elif args.accion == "revisiones":
        if not args.pagos or not args.estudiantes:
            parser.error("Para consultar revisiones debes indicar --pagos y --estudiantes.")
        validacion = AFNDValidacion(args.pagos, args.estudiantes, Progreso(args.progreso))
        print(json.dumps(validacion.listar_revisiones(), ensure_ascii=False, indent=2, default=str))
    else:
        parser.print_help()

