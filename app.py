import os
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)


# 1. Catálogo / Inicio
@app.route('/')
def eventos():
    return render_template('eventos.html')


# 2. Validación de Pagos
@app.route('/datos', methods=['GET', 'POST'])
def datos():
    if request.method == 'POST':
        carne = request.form.get('carne')
        no_recibo = request.form.get('no_recibo')
        nombre = request.form.get('nombre')

        # Redirige a la vista del boleto pasando los datos
        return render_template('boleto.html', estudiante={'nombre': nombre, 'carne': carne}, id_ticket="TCK-1001")

    return render_template('datos.html')


# 3. Muestra de Boleto
@app.route('/boleto/<id_ticket>')
def boleto(id_ticket):
    return render_template('boleto.html', id_ticket=id_ticket)


# 4. Escaneo e Ingreso (Control de Acceso)
@app.route('/ingreso', methods=['GET', 'POST'])
def ingreso():
    if request.method == 'POST':
        id_ticket = request.form.get('id_ticket')
        if id_ticket == "TCK-1001":
            return render_template('ingreso.html', estado='permitido', id_ticket=id_ticket)
        else:
            return render_template('ingreso.html', estado='rechazado', id_ticket=id_ticket)

    return render_template('ingreso.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)