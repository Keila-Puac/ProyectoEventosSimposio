import hashlib
import hmac
import io
import json
import os
import secrets
import time
import unicodedata
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import mysql.connector

app = Flask(__name__)
app.secret_key = 'simposio_secret_key_2026'


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )


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
        flash('Base de datos procesada correctamente.', 'success')
        return redirect(url_for('boleto'))
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
        estudiante = {'nombre': 'Estudiante Simposio', 'carnet': carnet}
        boletos_lista = [{
            'id': 1,
            'nombre': 'Conferencia Magistral Simposio 2026',
            'fecha': '15 Octubre 2026',
            'hora': '09:00 AM',
            'lugar': 'Auditorio Central',
            'ya_ingreso': False,
            'sig': 'firma_demo'
        }]
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