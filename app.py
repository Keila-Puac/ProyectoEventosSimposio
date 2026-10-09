import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import mysql.connector
from werkzeug.utils import secure_filename

from automata import AFDTickets, AFNDValidacion, ARCHIVO_PROGRESO, Progreso

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'simposio_secret_key_2026')

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / 'uploads'
PAGOS_EXCEL_DEMO = BASE_DIR / 'REPORTE DE TESORERIA- SIMPOSIO 2025.xlsx'
ESTUDIANTES_EXCEL_DEMO = BASE_DIR / 'Participantes_Simposio_Datos_Simulados.xlsx'
PROGRESO_AUTOMATA = Path(os.getenv('SIMPOSIO_PROGRESO', BASE_DIR / ARCHIVO_PROGRESO))
EXTENSIONES_EXCEL = {'.xlsx', '.xls'}


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )


def archivo_excel_valido(archivo):
    nombre = archivo.filename or ''
    return Path(nombre).suffix.lower() in EXTENSIONES_EXCEL


def guardar_excel_subido(archivo, prefijo):
    if not archivo or not archivo.filename:
        raise ValueError('Debes seleccionar ambos archivos Excel.')
    if not archivo_excel_valido(archivo):
        raise ValueError('Solo se permiten archivos .xlsx o .xls.')

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    nombre_seguro = secure_filename(archivo.filename)
    ruta = UPLOAD_DIR / f'{prefijo}_{marca}_{nombre_seguro}'
    archivo.save(ruta)
    return ruta


def seleccionar_excels_desde_formulario():
    modo = request.form.get('modo_archivos', 'subidos')
    if modo == 'demo':
        return PAGOS_EXCEL_DEMO, ESTUDIANTES_EXCEL_DEMO, 'archivos temporales del proyecto'

    pagos = guardar_excel_subido(request.files.get('excel_pagos'), 'pagos')
    estudiantes = guardar_excel_subido(request.files.get('excel_estudiantes'), 'estudiantes')
    return pagos, estudiantes, 'archivos subidos'


def crear_validador_pagos(ruta_pagos, ruta_estudiantes):
    return AFNDValidacion(str(ruta_pagos), str(ruta_estudiantes), Progreso(PROGRESO_AUTOMATA))


def sincronizar_participantes_demo(validador):
    PARTICIPANTES_DEMO[:] = []
    for estudiante in validador.estudiantes:
        carnet = str(estudiante.get(validador.columna_carnet, '')).strip()
        nombre = str(estudiante.get(validador.columna_nombre_estudiante, '')).strip()
        correo = str(estudiante.get('Correo', estudiante.get('correo', ''))).strip()
        if carnet and nombre:
            PARTICIPANTES_DEMO.append({'carnet': carnet, 'nombre': nombre, 'correo': correo})


def nombre_estudiante(estudiante):
    return estudiante.get('Nombre') or estudiante.get('nombre_completo') or estudiante.get('nombre') or 'Estudiante Simposio'


def carnet_estudiante(estudiante):
    return estudiante.get('Carnet') or estudiante.get('carnet') or ''


def crear_boleto_visual(estudiante, codigo_qr):
    carnet = carnet_estudiante(estudiante)
    return {
        'estudiante': {'nombre': nombre_estudiante(estudiante), 'carnet': carnet},
        'boletos': [{
            'id': 1,
            'nombre': 'Conferencia Magistral Simposio 2026',
            'fecha': '15 Octubre 2026',
            'hora': '09:00 AM',
            'lugar': 'Auditorio Central',
            'ya_ingreso': False,
            'sig': 'automata',
            'codigo_qr': codigo_qr,
            'qr_url': f'https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={quote_plus(codigo_qr)}'
        }]
    }


def emitir_ticket(resultado_pago):
    tickets = AFDTickets()
    try:
        return tickets.emitir_para_aceptado(resultado_pago)
    finally:
        tickets.cerrar_conexion()


# Listas simuladas en memoria para demo
PARTICIPANTES_DEMO = [
    {'carnet': '2026-0001', 'nombre': 'Ana Lucía Gómez', 'correo': 'ana@correo.edu.gt'},
    {'carnet': '2026-0002', 'nombre': 'Carlos Eduardo López', 'correo': 'carlos@correo.edu.gt'},
    {'carnet': '2026-0003', 'nombre': 'María Fernanda Reyes', 'correo': 'maria@correo.edu.gt'}
]

INGRESOS_DEMO = []

EVENTOS_DEMO = [
    {
        'id': 1,
        'nombre': 'Inteligencia Artificial y Desarrollo Web',
        'fecha': '15/10/2026',
        'hora': '09:00 AM',
        'cupos_int': 50,
        'restantes': 50,
        'ingresados': 0,
        'lugar': 'Auditorio A',
        'ponente': 'Ing. Carlos Mendoza',
        'descripcion': 'Estrategias avanzadas para integrar modelos de lenguaje en la nube.'
    }
]


@app.route('/')
def index():
    return render_template('eventos.html', active='eventos', eventos=EVENTOS_DEMO)


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

@app.route('/eliminar-evento/<int:eid>', methods=['POST'])
def eliminar_evento(eid):
    evento = next(
        (e for e in EVENTOS_DEMO if e['id'] == eid),
        None
    )

    if evento is None:
        flash('El evento no existe.', 'danger')
        return redirect(url_for('index'))

    # Eliminar los ingresos asociados al evento
    INGRESOS_DEMO[:] = [
        ingreso for ingreso in INGRESOS_DEMO
        if ingreso['evento_id'] != eid
    ]

    # Eliminar el evento
    EVENTOS_DEMO.remove(evento)

    flash(
        f"El evento '{evento['nombre']}' se eliminó correctamente.",
        'success'
    )

    return redirect(url_for('index'))

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
            ruta_pagos, ruta_estudiantes, origen = seleccionar_excels_desde_formulario()
            validador = crear_validador_pagos(ruta_pagos, ruta_estudiantes)
            sincronizar_participantes_demo(validador)
            resultado_pago = validador.procesar_pago(formulario)
            if resultado_pago.get('estudiante'):
                estudiante = resultado_pago['estudiante']
                estudiante.setdefault('Carnet', estudiante.get(validador.columna_carnet, ''))
                estudiante.setdefault('Nombre', estudiante.get(validador.columna_nombre_estudiante, ''))
        except Exception as exc:
            flash(f'No se pudo procesar el Excel: {exc}', 'danger')
            return render_template('datos.html', active='datos', form=formulario)

        if resultado_pago.get('estado') == 'Pago validado':
            try:
                resultado_ticket = emitir_ticket(resultado_pago)
            except Exception as exc:
                flash(f'Pago validado con {origen}, pero la base de tickets no respondió: {exc}', 'warning')
                return render_template('datos.html', active='datos', resultado=resultado_pago, form=formulario)

            if resultado_ticket.get('ok'):
                boleto = crear_boleto_visual(resultado_pago['estudiante'], resultado_ticket['codigo_qr'])
                session['ultimo_boleto'] = boleto
                flash(f'Pago validado con {origen} y boleto generado correctamente.', 'success')
                return render_template('boleto.html', active='boleto', est=boleto['estudiante'], boletos=boleto['boletos'])

            flash(f"Pago validado con {origen}, pero no se pudo generar el ticket: {resultado_ticket.get('mensaje')}", 'warning')
            return render_template('datos.html', active='datos', resultado=resultado_pago, form=formulario)

        if resultado_pago.get('estado') == 'Registro enviado a revisión manual':
            flash(f'Los Excel fueron procesados desde {origen}, pero el pago requiere revisión manual.', 'warning')
        elif resultado_pago.get('estado') == 'Registro pendiente de revisión manual':
            flash('Este recibo ya está pendiente de revisión manual.', 'info')
        else:
            flash(resultado_pago.get('estado', 'Pago no validado.'), 'danger')
        return render_template('datos.html', active='datos', resultado=resultado_pago, form=formulario)
    return render_template('datos.html', active='datos')


# Vista principal del Escáner/Lector QR
@app.route('/ingreso')
def ingreso():
    evento_sel = request.args.get('evento', type=int)
    sel = evento_sel if evento_sel else (EVENTOS_DEMO[0]['id'] if EVENTOS_DEMO else None)
    return render_template('ingreso.html', active='lector', eventos=EVENTOS_DEMO, total_part=len(PARTICIPANTES_DEMO),
                           sel=sel)


# API: Obtener lista de ingresados por evento
@app.route('/api/ingresos/<int:evento_id>')
def api_ingresos(evento_id):
    evento = next((e for e in EVENTOS_DEMO if e['id'] == evento_id), None)
    ingresos = [i for i in INGRESOS_DEMO if i['evento_id'] == evento_id]
    return jsonify({'evento': evento, 'ingresos': ingresos})


# API: Procesar ingreso por carnet o QR
@app.route('/api/ingreso', methods=['POST'])
def api_procesar_ingreso():
    data = request.get_json() or {}
    evento_id = int(data.get('evento_id', 0))
    carnet_o_qr = data.get('carnet') or data.get('qr', '')

    # Extraer carné si viene en formato TCK-id-carnet
    carnet = carnet_o_qr.split('-')[-1] if 'TCK' in carnet_o_qr else carnet_o_qr

    evento = next((e for e in EVENTOS_DEMO if e['id'] == evento_id), None)
    if not evento:
        return jsonify({'ok': False, 'msg': 'Evento no encontrado'}), 404

    # Verificar si ya ingresó
    ya_ingresado = any(i for i in INGRESOS_DEMO if i['evento_id'] == evento_id and i['carnet'] == carnet)
    if ya_ingresado:
        return jsonify({'ok': False, 'msg': f'El carné {carnet} YA fue ingresado a este evento'}), 409

    # Buscar participante
    part = next((p for p in PARTICIPANTES_DEMO if p['carnet'] == carnet or p['correo'] == carnet), None)
    nombre = part['nombre'] if part else f'Participante ({carnet})'

    # Registrar ingreso
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    INGRESOS_DEMO.insert(0, {
        'evento_id': evento_id,
        'carnet': carnet,
        'nombre': nombre,
        'fecha_hora': ahora
    })

    evento['ingresados'] += 1
    evento['restantes'] = max(0, evento['cupos_int'] - evento['ingresados'])

    return jsonify({'ok': True, 'msg': f'Acceso PERMITIDO: {nombre}'})


# API: Búsqueda dinámica / Autocompletado
@app.route('/api/buscar')
def api_buscar():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify([])
    res = [p for p in PARTICIPANTES_DEMO if q in p['carnet'].lower() or q in p['nombre'].lower()]
    return jsonify(res[:5])


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
            flash('Por seguridad el QR solo se muestra justo después de validar el pago.', 'info')
            estudiante = {'nombre': 'Consulta de boleto', 'carnet': carnet}
    return render_template('boleto.html', active='boleto', est=estudiante, boletos=boletos_lista)


@app.route('/boleto/qr/<int:eid>/<carnet>/<sig>')
def boleto_qr(eid, carnet, sig):
    return redirect(f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=TCK-{eid}-{carnet}")


@app.route('/boleto/pdf/<int:eid>/<carnet>/<sig>')
def boleto_pdf(eid, carnet, sig):
    flash('Descargando archivo PDF...', 'info')
    return redirect(url_for('boleto'))




if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
