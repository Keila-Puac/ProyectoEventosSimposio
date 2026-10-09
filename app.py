import os
from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector

app = Flask(__name__)
# Llave secreta requerida para usar mensajes de flash()
app.secret_key = 'simposio_secret_key_2026'


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )


# 1. Eventos / Catálogo Principal (Landing Page)
@app.route('/')
def index():
    return render_template('eventos.html', active='eventos')


# 2. Crear Evento
@app.route('/crear-evento', methods=['GET', 'POST'])
def nuevo_evento():
    if request.method == 'POST':
        flash('Evento creado con éxito', 'success')
        return redirect(url_for('index'))
    return render_template('crear_evento.html', active='crear')


# 3. Base de Datos / Validar Pago
@app.route('/datos', methods=['GET', 'POST'])
def datos():
    if request.method == 'POST':
        carne = request.form.get('carne')
        no_recibo = request.form.get('no_recibo')
        nombre = request.form.get('nombre')

        flash('Pago verificado correctamente', 'success')
        # Redirige a la vista del boleto pasando los datos cargados
        return redirect(url_for('boleto', carne=carne, nombre=nombre, id_ticket="TCK-1001"))

    return render_template('datos.html', active='datos')


# 4. Lector QR / Control de Ingreso
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


# 5. Mi Boleto (Pase Digital con QR)
@app.route('/boleto')
def boleto():
    nombre = request.args.get('nombre', 'Estudiante Simposio')
    carne = request.args.get('carne', '20260000')
    id_ticket = request.args.get('id_ticket', 'TCK-1001')
    return render_template('boleto.html', active='boleto', nombre=nombre, carne=carne, id_ticket=id_ticket)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)