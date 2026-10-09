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
        # Captura de los campos requeridos en el documento de la Fase 1
        carne = request.form.get('carne')
        no_recibo = request.form.get('no_recibo')
        nombre = request.form.get('nombre')
        email = request.form.get('email')

        # Aquí irá la lógica de consulta a Alwaysdata para cruzar:
        # 1. Reporte de Tesorería vs Listado Oficial de Estudiantes.
        # 2. Asignación de Ticket disponible de la Coordinación.

        # Por ahora enviamos una respuesta de confirmación a la plantilla
        return render_template(
            'registro.html',
            mensaje_revision="Solicitud recibida. Verificando pago con el reporte de Tesorería..."
        )

    return render_template('registro.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)