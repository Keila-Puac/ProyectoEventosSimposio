import os
from flask import Flask, render_template, request, redirect, url_for, flash
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
        flash('Pago verificado correctamente.', 'success')
        return redirect(url_for('boleto'))
    return render_template('datos.html', active='datos')


# 5. Lector QR / Control de Ingreso
@app.route('/ingreso', methods=['GET', 'POST'])
def ingreso():
    estado = None
    id_ticket = None
    if request.method == 'POST':
        id_ticket = request.form.get('id_ticket')
        if id_ticket == "TCK-1001":
            estado = 'permitido'
            flash('¡Acceso Autorizado!', 'success')
        else:
            estado = 'rechazado'
            flash('Acceso Denegado: Ticket inválido o utilizado', 'danger')

    return render_template('ingreso.html', active='lector', estado=estado, id_ticket=id_ticket)


# 6. Vista de Mi Boleto
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