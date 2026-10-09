import os
from flask import Flask, render_template, request, redirect, url_for
import mysql.connector

app = Flask(__name__)

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        carne = request.form.get('carne')
        no_recibo = request.form.get('no_recibo')
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        return render_template('ticket.html', nombre=nombre, carne=carne, no_recibo=no_recibo, id_ticket="TCK-1001")
    return render_template('registro.html')

@app.route('/ticket/<id_ticket>')
def ticket(id_ticket):
    return render_template('ticket.html', id_ticket=id_ticket)

# Ruta del panel de escaneo
@app.route('/admin')
def admin():
    return render_template('admin.html')

# Ruta para procesar la validacion
@app.route('/validar-ingreso', methods=['POST'])
def validar_ingreso():
    id_ticket = request.form.get('id_ticket')
    if id_ticket == "TCK-1001":
        estudiante_demo = {"nombre": "Juan Pérez", "carne": "20260123", "id_ticket": id_ticket}
        return render_template('admin.html', estado='permitido', estudiante=estudiante_demo)
    else:
        return render_template('admin.html', estado='rechazado')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)