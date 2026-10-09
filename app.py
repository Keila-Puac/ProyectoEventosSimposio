import os
from urllib.parse import quote_plus

from flask import Flask, render_template, request, redirect, url_for, flash, session
import mysql.connector

from automata import AFDTickets, AFNDValidacion, ARCHIVO_PROGRESO, Progreso

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'simposio_secret_key_2026')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGOS_EXCEL = os.getenv(
    'SIMPOSIO_EXCEL_PAGOS',
    os.path.join(BASE_DIR, 'REPORTE DE TESORERIA- SIMPOSIO 2025.xlsx')
)
ESTUDIANTES_EXCEL = os.getenv(
    'SIMPOSIO_EXCEL_ESTUDIANTES',
    os.path.join(BASE_DIR, 'Participantes_Simposio_Datos_Simulados.xlsx')
)
PROGRESO_AUTOMATA = os.getenv(
    'SIMPOSIO_PROGRESO',
    os.path.join(BASE_DIR, ARCHIVO_PROGRESO)
)


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )


def crear_validador_pagos():
    return AFNDValidacion(PAGOS_EXCEL, ESTUDIANTES_EXCEL, Progreso(PROGRESO_AUTOMATA))


def emitir_ticket(resultado_pago):
    tickets = AFDTickets()
    try:
        return tickets.emitir_para_aceptado(resultado_pago)
    finally:
        tickets.cerrar_conexion()


def validar_ticket_ingreso(codigo_qr):
    tickets = AFDTickets()
    try:
        return tickets.validar_ingreso(codigo_qr)
    finally:
        tickets.cerrar_conexion()


def datos_boleto(estudiante, codigo_qr):
    nombre = estudiante.get('Nombre') or estudiante.get('nombre_completo') or estudiante.get('nombre') or 'Estudiante Simposio'
    carnet = estudiante.get('Carnet') or estudiante.get('carnet') or ''
    return {
        'estudiante': {'nombre': nombre, 'carnet': carnet},
        'boletos': [{
            'id': 1,
            'nombre': 'Conferencia Magistral Simposio 2026',
            'fecha': '15 Octubre 2026',
            'hora': '09:00 AM',
            'lugar': 'Auditorio Central',
            'ya_ingreso': False,
            'sig': 'automata',
            'codigo_qr': codigo_qr,
            'qr_url': f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={quote_plus(codigo_qr)}"
        }]
    }


# Lista en memoria para eventos de demostración
EVENTOS_DEMO = [
    {
        'id': 1,
        'nombre': 'Inteligencia Artificial y Desarrollo Web',
        'fecha': '15/10/2026',
        'hora': '09:00 AM',
        'cupos_int': 50,
        'restantes': 45,
        'ingresados': 5,
        'lugar': 'Auditorio A',
        'ponente': 'Ing. Carlos Mendoza',
        'descripcion': 'Estrategias avanzadas para integrar modelos de lenguaje en arquitecturas en la nube.'
    },
    {
        'id': 2,
        'nombre': 'Ciberseguridad en Entornos Cloud',
        'fecha': '15/10/2026',
        'hora': '11:00 AM',
        'cupos_int': 30,
        'restantes': 12,
        'ingresados': 18,
        'lugar': 'Lab Redes 2',
        'ponente': 'Dra. Sofía Ramos',
        'descripcion': 'Principios de protección de datos, autenticación segura y mitigación de riesgos.'
    },
    {
        'id': 3,
        'nombre': 'Bases de Datos de Alto Rendimiento',
        'fecha': '15/10/2026',
        'hora': '02:00 PM',
        'cupos_int': 40,
        'restantes': 30,
        'ingresados': 10,
        'lugar': 'Auditorio Central',
        'ponente': 'MSc. Roberto Gómez',
        'descripcion': 'Optimización de consultas, indexación y escalabilidad con MySQL en producción.'
    }
]


# 1. Eventos / Catálogo Principal (Con búsqueda)
@app.route('/')
def index():
    query = request.args.get('q', '').strip().lower()
    eventos_filtrados = EVENTOS_DEMO

    if query:
        eventos_filtrados = [
            e for e in EVENTOS_DEMO
            if query in e['nombre'].lower() or query in e['ponente'].lower() or query in e['lugar'].lower()
        ]

    return render_template('eventos.html', active='eventos', eventos=eventos_filtrados, q=query)


# 2. Crear Evento
@app.route('/crear-evento', methods=['GET', 'POST'])
def nuevo_evento():
    formulario_datos = {}
    if request.method == 'POST':
        formulario_datos = request.form
        nuevo_id = len(EVENTOS_DEMO) + 1
        cupos = int(request.form.get('cupos', 50))

        nuevo = {
            'id': nuevo_id,
            'nombre': request.form.get('nombre'),
            'fecha': request.form.get('fecha'),
            'hora': request.form.get('hora'),
            'cupos_int': cupos,
            'restantes': cupos,
            'ingresados': 0,
            'lugar': request.form.get('lugar'),
            'ponente': request.form.get('ponente'),
            'descripcion': request.form.get('descripcion')
        }
        EVENTOS_DEMO.append(nuevo)
        flash(f"Evento '{nuevo['nombre']}' creado correctamente.", 'success')
        return redirect(url_for('index'))

    return render_template('crear_evento.html', active='crear', f=formulario_datos)


# 3. Eliminar Evento
@app.route('/eliminar-evento/<int:eid>', methods=['POST'])
def eliminar_evento(eid):
    global EVENTOS_DEMO
    EVENTOS_DEMO = [e for e in EVENTOS_DEMO if e['id'] != eid]
    flash('Evento eliminado correctamente.', 'warning')
    return redirect(url_for('index'))


# 4. Base de Datos / Validar Pago
@app.route('/datos', methods=['GET', 'POST'])
def datos():
    if request.method == 'POST':
        formulario = {
            'carne': request.form.get('carne', '').strip(),
            'carnet': request.form.get('carne', '').strip(),
            'no_recibo': request.form.get('no_recibo', '').strip(),
            'nombre': request.form.get('nombre', '').strip()
        }
        try:
            validador = crear_validador_pagos()
            resultado_pago = validador.procesar_pago(formulario)
        except Exception as exc:
            flash(f'No se pudo validar el pago: {exc}', 'danger')
            return render_template('datos.html', active='datos')

        if resultado_pago.get('estado') == 'Pago validado':
            try:
                resultado_ticket = emitir_ticket(resultado_pago)
            except Exception as exc:
                flash(f'El pago fue validado, pero la base de tickets no respondió: {exc}', 'warning')
                return redirect(url_for('boleto'))
            if resultado_ticket.get('ok'):
                boleto = datos_boleto(resultado_pago['estudiante'], resultado_ticket['codigo_qr'])
                session['ultimo_boleto'] = boleto
                flash('Pago validado y boleto generado correctamente.', 'success')
                return render_template('boleto.html', active='boleto', est=boleto['estudiante'], boletos=boleto['boletos'])
            flash(f"El pago fue validado, pero no se pudo generar el ticket: {resultado_ticket.get('mensaje')}", 'warning')
            return redirect(url_for('boleto'))

        if resultado_pago.get('estado') == 'Registro enviado a revisión manual':
            flash('El pago necesita revisión manual antes de emitir boleto.', 'warning')
        elif resultado_pago.get('estado') == 'Registro pendiente de revisión manual':
            flash('Este recibo ya está pendiente de revisión manual.', 'info')
        else:
            flash(resultado_pago.get('estado', 'Pago no validado.'), 'danger')
        return render_template('datos.html', active='datos', resultado=resultado_pago)
    return render_template('datos.html', active='datos')


# 5. Lector QR / Control de Ingreso
@app.route('/ingreso', methods=['GET', 'POST'])
def ingreso():
    estado = None
    id_ticket = None
    resultado = None
    if request.method == 'POST':
        id_ticket = request.form.get('id_ticket')
        try:
            resultado = validar_ticket_ingreso(id_ticket)
        except Exception as exc:
            resultado = {'autorizado': False, 'mensaje': f'Base de tickets no disponible: {exc}'}
        if resultado.get('autorizado'):
            estado = 'permitido'
            flash(resultado.get('mensaje', 'Acceso autorizado.'), 'success')
        else:
            estado = 'rechazado'
            flash(resultado.get('mensaje', 'Acceso denegado.'), 'danger')

    return render_template('ingreso.html', active='lector', estado=estado, id_ticket=id_ticket, resultado=resultado)


# 6. Vista de Mi Boleto
@app.route('/boleto', methods=['GET', 'POST'])
def boleto():
    estudiante = None
    boletos_lista = []

    if request.method == 'POST':
        carnet = request.form.get('carnet')
        ultimo_boleto = session.get('ultimo_boleto')
        if ultimo_boleto and ultimo_boleto.get('estudiante', {}).get('carnet') == carnet:
            estudiante = ultimo_boleto['estudiante']
            boletos_lista = ultimo_boleto['boletos']
        else:
            flash('Por seguridad el código QR solo se muestra al momento de validar el pago. Valida tu recibo para generar el boleto.', 'info')
            estudiante = {'nombre': 'Consulta de boleto', 'carnet': carnet}

    return render_template('boleto.html', active='boleto', est=estudiante, boletos=boletos_lista)


# 7. QR y PDF Helpers
@app.route('/boleto/qr/<int:eid>/<carnet>/<sig>')
def boleto_qr(eid, carnet, sig):
    return redirect(f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=TCK-{eid}-{carnet}")


@app.route('/boleto/pdf/<int:eid>/<carnet>/<sig>')
def boleto_pdf(eid, carnet, sig):
    flash('Descargando archivo PDF del ticket...', 'info')
    return redirect(url_for('boleto'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
