import hashlib
import hmac
import io
import json
import os
import secrets
import time
import unicodedata
from datetime import datetime

import threading

import pandas as pd
import qrcode

try:  # lee SENDGRID_API_KEY, MAIL_USER, ADMIN_PASSWORD… de un archivo .env si existe
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import correo

from simposio import (Almacen, leer_archivo, traza_texto, MONTO_CORRECTO, UMBRAL_ALTO, UMBRAL_MIN, ESTADOS_PAGO)
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, abort, session)
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle

app = Flask(__name__)

DATA_DIR = os.getenv("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _cargar_secreto():
    """Clave para firmar los QR. Sale de SECRET_KEY o se genera una vez y se guarda."""
    env = os.getenv("SECRET_KEY")
    if env:
        return env
    ruta = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(ruta):
        with open(ruta, "w") as f:
            f.write(secrets.token_hex(32))
    with open(ruta) as f:
        return f.read().strip()


SECRETO = _cargar_secreto()
app.secret_key = SECRETO
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.getenv("RENDER") or os.getenv("COOKIE_SECURE")))


def _cargar_clave_admin():
    """ADMIN_PASSWORD del entorno; si no existe se genera una al azar (nunca queda abierto)."""
    env = os.getenv("ADMIN_PASSWORD")
    if env:
        return env
    ruta = os.path.join(DATA_DIR, "admin_password.txt")
    if not os.path.exists(ruta):
        with open(ruta, "w") as f:
            f.write(secrets.token_urlsafe(9))
    with open(ruta) as f:
        clave = f.read().strip()
    print(f"\n[ADMIN] No hay ADMIN_PASSWORD definida. Clave temporal: {clave}  (guardada en {ruta})\n")
    return clave


CLAVE_ADMIN = _cargar_clave_admin()
RUTAS_PUBLICAS = {"index", "boleto", "boleto_qr", "boleto_pdf", "login", "logout", "static"}
ALM = Almacen(DATA_DIR)
app.jinja_env.filters["traza"] = traza_texto


@app.before_request
def proteger():
    """Son públicos la portada (cronograma) y «Mi boleto»; todo lo demás exige iniciar sesión como administrador."""
    ep = request.endpoint
    if ep in RUTAS_PUBLICAS or session.get("admin"):
        return None
    if ep is None:
        return redirect(url_for("boleto"))
    if request.path.startswith("/api/"):
        return jsonify(ok=False, msg="Sesión de administrador requerida."), 401
    return redirect(url_for("login", next=request.path))


@app.context_processor
def inyectar_sesion():
    return {"es_admin": bool(session.get("admin"))}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("clave", "").encode(), CLAVE_ADMIN.encode()):
            session.clear()
            session["admin"] = True
            destino = request.args.get("next", "")
            return redirect(destino if destino.startswith("/") and not destino.startswith("//") else url_for("index"))
        time.sleep(1)  # frena intentos masivos
        flash("Contraseña incorrecta.", "danger")
    return render_template("login.html", active="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("boleto"))

F_EVENTOS = os.path.join(DATA_DIR, "eventos.csv")
COL_EVENTOS = ["id", "nombre", "fecha", "hora", "lugar", "ponente", "descripcion", "cupos"]


# ---------- Eventos del simposio (crear, modificar, eliminar) ----------
def get_eventos():
    if os.path.exists(F_EVENTOS):
        return pd.read_csv(F_EVENTOS, dtype=str).fillna("")
    df = pd.DataFrame([
        ["1", "Inteligencia Artificial y Desarrollo Web", "2026-11-10", "09:00", "Auditorio Principal",
         "Ing. Carlos Mendoza", "Estrategias para integrar modelos de lenguaje en la nube.", "45"],
        ["2", "Ciberseguridad en Entornos Cloud", "2026-11-10", "11:00", "Sala B",
         "Dra. Sofia Ramos", "Protección de datos, autenticación segura y mitigación de riesgos.", "12"],
        ["3", "Bases de Datos de Alto Rendimiento", "2026-11-10", "14:00", "Sala C",
         "MSc. Roberto Gómez", "Optimización de consultas, indexación y escalabilidad.", "30"],
    ], columns=COL_EVENTOS)
    df.to_csv(F_EVENTOS, index=False)
    return df


def _int(valor, defecto=0):
    try:
        return max(int(float(str(valor).strip())), 0)
    except (ValueError, TypeError):
        return defecto


@app.template_filter("hora12")
def hora12(valor):
    """'14:00' -> '02:00 PM' (si no se puede interpretar, se deja tal cual)."""
    try:
        return datetime.strptime(str(valor).strip(), "%H:%M").strftime("%I:%M %p")
    except ValueError:
        return valor


@app.route("/")
def index():
    eventos = sorted(get_eventos().to_dict("records"), key=lambda e: (e["fecha"], e["hora"]))
    q = request.args.get("q", "").strip().lower()
    if q:
        eventos = [e for e in eventos if q in (e["nombre"] + e["ponente"] + e["lugar"]).lower()]
    return render_template("eventos.html", eventos=eventos, q=q, active="eventos")


@app.route("/eventos/nuevo", methods=["GET", "POST"])
def nuevo_evento():
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        if not nombre or not f.get("fecha") or not f.get("hora"):
            flash("Nombre, fecha y hora son obligatorios.", "danger")
            return render_template("crear_evento.html", active="crear", f=f)
        df = get_eventos()
        nuevo_id = str(max([_int(i) for i in df["id"]] or [0]) + 1)
        fila = {"id": nuevo_id, "nombre": nombre, "fecha": f["fecha"], "hora": f["hora"],
                "lugar": f.get("lugar", "").strip(), "ponente": f.get("ponente", "").strip(),
                "descripcion": f.get("descripcion", "").strip(), "cupos": str(_int(f.get("cupos")))}
        pd.concat([df, pd.DataFrame([fila])], ignore_index=True).to_csv(F_EVENTOS, index=False)
        flash(f"Evento «{nombre}» creado correctamente.", "success")
        return redirect(url_for("index"))
    return render_template("crear_evento.html", active="crear", f={})


@app.route("/eventos/<eid>/editar", methods=["GET", "POST"])
def editar_evento(eid):
    df = get_eventos()
    fila = df[df["id"] == eid]
    if fila.empty:
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        if not nombre or not f.get("fecha") or not f.get("hora"):
            flash("Nombre, fecha y hora son obligatorios.", "danger")
            return render_template("crear_evento.html", active="eventos", f=f, editando=eid)
        for campo in ("fecha", "hora"):
            df.loc[df["id"] == eid, campo] = f[campo]
        for campo in ("lugar", "ponente", "descripcion"):
            df.loc[df["id"] == eid, campo] = f.get(campo, "").strip()
        df.loc[df["id"] == eid, "nombre"] = nombre
        df.loc[df["id"] == eid, "cupos"] = str(_int(f.get("cupos")))
        df.to_csv(F_EVENTOS, index=False)
        flash(f"Evento «{nombre}» actualizado.", "success")
        return redirect(url_for("index"))
    return render_template("crear_evento.html", active="eventos", f=fila.iloc[0].to_dict(), editando=eid)


@app.route("/eventos/<eid>/eliminar", methods=["POST"])
def eliminar_evento(eid):
    df = get_eventos()
    df[df["id"] != eid].to_csv(F_EVENTOS, index=False)
    flash("Evento eliminado.", "info")
    return redirect(url_for("index"))


# ---------- Carga de archivos: Tesorería, listado oficial y tickets ----------
@app.route("/datos", methods=["GET", "POST"])
def datos():
    if request.method == "POST":
        archivos = [a for a in request.files.getlist("archivo") if a and a.filename]
        if not archivos:
            flash("Selecciona al menos un archivo Excel.", "danger")
            return redirect(url_for("datos"))
        estudiantes, pagos, tickets, detalle = [], [], [], []
        for a in archivos:
            try:
                tablas = leer_archivo(a)
            except Exception as e:
                flash(f"No se pudo leer {a.filename}: {e}", "danger")
                continue
            if not tablas:
                flash(f"{a.filename}: no reconocí el contenido (se esperan columnas Carnet+Nombre, "
                      f"Recibo+Nombre o Ticket).", "danger")
            for tipo, hoja, filas in tablas:
                {"estudiantes": estudiantes, "pagos": pagos, "tickets": tickets}[tipo].extend(filas)
                etiqueta = {"estudiantes": "listado oficial", "pagos": "pagos de Tesorería", "tickets": "tickets"}[tipo]
                detalle.append(f"{a.filename} → {etiqueta} (hoja «{hoja}», {len(filas)} filas)")
        with_msgs = []
        if estudiantes:
            n, total = ALM.importar_estudiantes(estudiantes, reemplazar=request.form.get("modo") != "agregar")
            with_msgs.append(f"Listado oficial: {total} estudiantes.")
        if pagos:
            n, omit = ALM.importar_pagos(pagos)
            with_msgs.append(f"Pagos: {n} nuevos" + (f", {omit} ya estaban cargados." if omit else "."))
        if tickets:
            n, rep = ALM.importar_tickets([t["ticket"] for t in tickets])
            with_msgs.append(f"Tickets: {n} nuevos" + (f", {rep} repetidos omitidos." if rep else "."))
        if with_msgs:
            flash(" ".join(with_msgs) + " Archivos: " + "; ".join(detalle), "success")
            if pagos or estudiantes:
                flash("Siguiente paso: ve a «Pagos» y pulsa «Conciliar pagos».", "info")
        return redirect(url_for("datos"))
    c = ALM.conteos()
    return render_template("datos.html", active="datos", c=c, muestra=ALM.participantes()[:200])


@app.route("/datos/vaciar", methods=["POST"])
def vaciar():
    ALM.importar_estudiantes([], reemplazar=True)
    flash("Listado oficial vaciado.", "info")
    return redirect(url_for("datos"))


# ---------- Pagos, conciliación y revisión manual ----------
@app.route("/pagos")
def pagos():
    est = request.args.get("estado", "")
    filas = ALM.pagos()
    if est in ESTADOS_PAGO:
        filas = [p for p in filas if p["estado"] == est]
    nombres = {p["carnet"]: p["nombre"] for p in ALM.participantes()}
    for p in filas:
        p["nombre_oficial"] = nombres.get(p["carnet"], "")
    return render_template("pagos.html", active="pagos", pagos=filas[:500], total=len(filas), sel=est,
                           c=ALM.conteos(), estados=ESTADOS_PAGO, monto=MONTO_CORRECTO,
                           umbral_alto=UMBRAL_ALTO, umbral_min=UMBRAL_MIN)


@app.route("/pagos/conciliar", methods=["POST"])
def conciliar():
    r = ALM.conciliar()
    if r is None:
        flash("Primero carga el listado oficial de estudiantes en «Base de datos».", "danger")
    elif not r:
        flash("No había pagos pendientes por procesar.", "info")
    else:
        flash("Conciliación terminada: " + ", ".join(f"{v} {k.lower().replace('_', ' ')}" for k, v in sorted(r.items())) + ".", "success")
    return redirect(url_for("pagos"))


@app.route("/pagos/vaciar", methods=["POST"])
def vaciar_pagos():
    if ALM.vaciar_pagos():
        flash("Pagos vaciados.", "info")
    else:
        flash("No se puede vaciar: ya hay tickets asignados o utilizados.", "danger")
    return redirect(url_for("pagos"))


@app.route("/revision")
def revision():
    cola = [p for p in ALM.pagos() if p["estado"] == "REVISION"]
    for p in cola:
        try:
            p["cands"] = json.loads(p["candidatos"] or "[]")
        except ValueError:
            p["cands"] = []
    return render_template("revision.html", active="revision", cola=cola)


@app.route("/revision/<path:recibo>/resolver", methods=["POST"])
def resolver(recibo):
    aceptar = request.form.get("accion") == "aceptar"
    ok, msg = ALM.resolver_revision(recibo, aceptar, request.form.get("carnet") or request.form.get("carnet_otro", ""))
    if ok and aceptar:
        enviar_boletos()
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("revision"))


# ---------- Tickets de Coordinación ----------
@app.route("/tickets")
def tickets():
    lista = ALM.tickets()
    nombres = {p["carnet"]: p["nombre"] for p in ALM.participantes()}
    for t in lista:
        t["nombre"] = nombres.get(t["carnet"], "")
    return render_template("tickets.html", active="tickets", tickets=lista[:500], total=len(lista), c=ALM.conteos(),
                           correo_activo=correo.configurado(), envio=ENVIO,
                           por_enviar=len(ALM.boletos_pendientes_correo()))


@app.route("/tickets/prueba", methods=["POST"])
def tickets_prueba():
    n, _ = ALM.tickets_de_prueba(max(1, min(_int(request.form.get("cantidad"), 500), 5000)))
    flash(f"Se agregaron {n} tickets de prueba (SIM-0001…). Para producción sube el archivo real de Coordinación.", "info")
    return redirect(url_for("tickets"))


# ---------- Confirmación digital (QR) ----------
def firmar(ticket):
    """Firma corta (HMAC) del ticket: el QR no se puede inventar sin la clave."""
    return hmac.new(SECRETO.encode(), f"T|{ticket}".encode(), hashlib.sha256).hexdigest()[:16]


def payload_qr(ticket):
    return f"SIM:{ticket}:{firmar(ticket)}"


def leer_payload(texto):
    """Devuelve el ticket si el texto es un QR válido de este sistema; si no, None."""
    t = str(texto).strip()
    if not t.startswith("SIM:"):
        return None
    ticket, _, sig = t[4:].rpartition(":")
    return ticket if ticket and hmac.compare_digest(sig, firmar(ticket)) else None


def png_qr(texto):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=3)
    qr.add_data(texto)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="#0b4f9c", back_color="white").convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf


@app.route("/boleto", methods=["GET", "POST"])
def boleto():
    """El estudiante entra con carnet + recibo; el sistema valida el pago y le entrega su ticket."""
    r = None
    if request.method == "POST":
        r = ALM.solicitar_boleto(request.form.get("carnet", ""), request.form.get("recibo", ""))
        if r["estado"] != "VALIDADO":
            time.sleep(0.5)  # frena intentos masivos
        else:
            r["sig"] = firmar(r["ticket"])
            enviar_boletos({r["ticket"]})
    return render_template("boleto.html", active="boleto", r=r, correo_activo=correo.configurado())


@app.route("/boleto/<ticket>/<sig>/qr.png")
def boleto_qr(ticket, sig):
    if not hmac.compare_digest(sig, firmar(ticket)):
        abort(404)
    return send_file(png_qr(payload_qr(ticket)), mimetype="image/png")


def pdf_boleto(ticket):
    """PDF del boleto (bytes) o None si el ticket no está asignado."""
    t = next((x for x in ALM.tickets() if x["ticket"] == ticket and x["carnet"]), None)
    p = ALM.buscar_participante(t["carnet"]) if t else None
    if not p:
        return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    st = getSampleStyleSheet()
    azul = colors.HexColor("#0b4f9c")
    t1 = ParagraphStyle("t1", parent=st["Heading1"], fontSize=20, textColor=azul, alignment=1)
    t2 = ParagraphStyle("t2", parent=st["Normal"], fontSize=12, textColor=colors.HexColor("#4A5568"), alignment=1)
    lab = ParagraphStyle("lab", parent=st["Normal"], fontName="Helvetica-Bold", fontSize=10)
    val = ParagraphStyle("val", parent=st["Normal"], fontSize=10)
    qr_img = RLImage(png_qr(payload_qr(ticket)), width=170, height=170)
    filas = [[Paragraph(k, lab), Paragraph(str(v or "—"), val)] for k, v in [
        ("Participante:", p["nombre"]), ("Carnet:", p["carnet"]), ("Ticket:", ticket),
        ("Estado del pago:", "VALIDADO"), ("Recibo:", t["recibo"])]]
    datos = Table(filas, colWidths=[100, 210])
    datos.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    caja = Table([[qr_img, datos]], colWidths=[190, 320])
    caja.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 1.5, azul),
                              ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F8FC")),
                              ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
    doc.build([Paragraph("XIX SIMPOSIO DE INGENIERÍA", t1),
               Paragraph("De la gestión inteligente a la acción en ingeniería", t2), Spacer(1, 6),
               Paragraph("Confirmación digital de acceso", t2), Spacer(1, 14), caja, Spacer(1, 16),
               Paragraph("Presenta este código QR desde tu celular o impreso al ingresar. Es de un solo uso.",
                         ParagraphStyle("n", parent=st["Normal"], fontSize=9, alignment=1,
                                        textColor=colors.HexColor("#64748B")))])
    return buf.getvalue()


@app.route("/boleto/<ticket>/<sig>/boleto.pdf")
def boleto_pdf(ticket, sig):
    if not hmac.compare_digest(sig, firmar(ticket)):
        abort(404)
    pdf = pdf_boleto(ticket)
    if pdf is None:
        abort(404)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"Boleto_{ticket}.pdf")


# ---------- Envío del boleto por correo (SendGrid) ----------
ENVIO = {"activo": False, "ok": 0, "fallos": 0, "ultimo": ""}


def enviar_boletos(solo=None):
    """Envía en segundo plano los boletos pendientes (todos, o solo los tickets indicados)."""
    if not correo.configurado():
        return False
    pendientes = ALM.boletos_pendientes_correo(solo)
    if not pendientes or (solo is None and ENVIO["activo"]):
        return bool(pendientes)

    def tarea():
        if solo is None:
            ENVIO.update(activo=True, ok=0, fallos=0, ultimo="")
        for b in pendientes:
            try:
                pdf = pdf_boleto(b["ticket"])
                ok, msg = correo.enviar_boleto(b["correo"], b["nombre"], b["ticket"], pdf) if pdf else (False, "sin PDF")
            except Exception as e:
                ok, msg = False, str(e)
            if ok:
                ALM.marcar_correo(b["ticket"])
                ENVIO["ok"] += 1
            else:
                ENVIO["fallos"] += 1
                ENVIO["ultimo"] = f"{b['correo']}: {msg}"
        if solo is None:
            ENVIO["activo"] = False

    threading.Thread(target=tarea, daemon=True).start()
    return True


@app.route("/tickets/enviar-correos", methods=["POST"])
def enviar_correos():
    if not correo.configurado():
        flash("El correo no está configurado: define SENDGRID_API_KEY y MAIL_USER (ver .env.example).", "danger")
    elif ENVIO["activo"]:
        flash("Ya hay un envío en curso. Recarga esta página para ver el avance.", "info")
    elif enviar_boletos():
        flash("Envío iniciado en segundo plano. Recarga esta página para ver el avance.", "success")
    else:
        flash("No hay boletos pendientes de enviar (o los estudiantes no tienen correo en el listado).", "info")
    return redirect(url_for("tickets"))


# ---------- Ingreso al evento ----------
@app.route("/lector")
def ingreso():
    return render_template("ingreso.html", active="lector", c=ALM.conteos())


@app.route("/api/ingreso", methods=["POST"])
def api_ingreso():
    data = request.get_json(force=True, silent=True) or {}
    if data.get("qr"):
        ticket = leer_payload(data["qr"])
        if not ticket:
            return jsonify(autorizado=False, estado="QR_INVALIDO", mensaje="QR no válido o alterado.", nombre="",
                           carnet="", estado_pago="", ticket=""), 400
    else:
        ticket = str(data.get("ticket", "")).strip()
    r = ALM.validar_ingreso(ticket)
    return jsonify(r), (200 if r["autorizado"] else 409)


@app.route("/api/ingresos")
def api_ingresos():
    return jsonify(ingresos=ALM.ultimos_ingresos(), c=ALM.conteos())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
