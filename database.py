from datetime import datetime
import getpass
import mysql.connector

# Configuración de conexión remota con Alwaysdata
DB_CONFIG = {
    'host': 'mysql-jack-vg.alwaysdata.net',
    'user': 'jack-vg',
    'password': 'Wilson2007..',  # se pone al contraseña de la base  de datos, ahi me preguntan la contra x,d
    #no voy a subir eso a esata wea
    'database': 'jack-vg_simposio_db',
    'port': 3306
}

#tablas de la base, aqui se inicializan, poque ya existen
TABLAS = {
    'estudiantes': """
        CREATE TABLE IF NOT EXISTS estudiantes (
            carnet VARCHAR(20) PRIMARY KEY,
            nombre_completo VARCHAR(150) NOT NULL,
            correo VARCHAR(100),
            carrera VARCHAR(100)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'pagos': """
        CREATE TABLE IF NOT EXISTS pagos (
            id_pago INT AUTO_INCREMENT PRIMARY KEY,
            no_recibo VARCHAR(50) UNIQUE NOT NULL,
            nombre_pagador VARCHAR(150) NOT NULL,
            monto DECIMAL(10, 2) NOT NULL,
            fecha_pago DATE,
            estado_pago VARCHAR(30) DEFAULT 'PENDIENTE'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'tickets': """
        CREATE TABLE IF NOT EXISTS tickets (
            id_ticket VARCHAR(50) PRIMARY KEY,
            codigo_qr VARCHAR(255) UNIQUE NOT NULL,
            estado VARCHAR(20) DEFAULT 'DISPONIBLE',
            id_evento INT NULL,
            INDEX idx_tickets_evento (id_evento)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'eventos': """
        CREATE TABLE IF NOT EXISTS eventos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nombre VARCHAR(180) NOT NULL,
            lema VARCHAR(255),
            lugar VARCHAR(255),
            precio DECIMAL(10,2) NOT NULL DEFAULT 0,
            cupo INT NOT NULL DEFAULT 100,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'dias_evento': """
        CREATE TABLE IF NOT EXISTS dias_evento (
            id_dia INT AUTO_INCREMENT PRIMARY KEY,
            id_evento INT NOT NULL,
            fecha DATE NOT NULL,
            hora TIME NOT NULL DEFAULT '08:00:00',
            descripcion TEXT,
            orden_dia INT NOT NULL DEFAULT 1,
            FOREIGN KEY (id_evento) REFERENCES eventos(id) ON DELETE CASCADE,
            INDEX idx_dias_evento (id_evento, orden_dia)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'validaciones': """
        CREATE TABLE IF NOT EXISTS validaciones (
            id_validacion INT AUTO_INCREMENT PRIMARY KEY,
            carnet VARCHAR(20) NOT NULL,
            id_pago INT NOT NULL,
            id_ticket VARCHAR(50) NOT NULL,
            fecha_validacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            ingreso_dia_1 DATETIME NULL,
            ingreso_dia_2 DATETIME NULL,
            FOREIGN KEY (carnet) REFERENCES estudiantes(carnet) ON DELETE CASCADE,
            FOREIGN KEY (id_pago) REFERENCES pagos(id_pago) ON DELETE CASCADE,
            FOREIGN KEY (id_ticket) REFERENCES tickets(id_ticket) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'pagos_pendientes': """
        CREATE TABLE IF NOT EXISTS pagos_pendientes (
            id_pendiente INT AUTO_INCREMENT PRIMARY KEY,
            id_pago INT NOT NULL,
            relacionados TEXT,
            FOREIGN KEY (id_pago) REFERENCES pagos(id_pago) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    'usuarios_aceptados': """
        CREATE TABLE IF NOT EXISTS usuarios_aceptados (
            id_aceptado INT AUTO_INCREMENT PRIMARY KEY,
            carnet VARCHAR(20) NOT NULL,
            FOREIGN KEY (carnet) REFERENCES estudiantes(carnet) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
}


def obtener_conexion():
    """Establece conexión activa con la base de datos remota en Alwaysdata."""
    if not DB_CONFIG['password']:
        DB_CONFIG['password'] = getpass.getpass("Ingresa la contraseña de Alwaysdata: ")
    return mysql.connector.connect(**DB_CONFIG)


def inicializar_base_datos():
    """Verifica y crea las tablas automáticamente si no existen."""
    db = obtener_conexion()
    cursor = db.cursor()
    print("Verificando e inicializando tablas en la nube...")
    for nombre_tabla, ddl_sql in TABLAS.items():
        cursor.execute(ddl_sql)
        print(f"Tabla '{nombre_tabla}' lista.")
    db.commit()
    cursor.close()
    db.close()


# ==========================================
# FUNCIONES
# ==========================================

def consultar_estudiantes():
    """Obtiene el listado completo de estudiantes registrados."""
    db = obtener_conexion()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM estudiantes")
    estudiantes = cursor.fetchall()
    cursor.close()
    db.close()
    return estudiantes


def obtener_ticket_disponible():
    """Busca el primer ticket que tenga estado 'DISPONIBLE'."""
    db = obtener_conexion()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM tickets WHERE estado = 'DISPONIBLE' LIMIT 1")
    ticket = cursor.fetchone()
    cursor.close()
    db.close()
    return ticket


def consultar_pagos():
    """Obtiene el listado completo de pagos registrados."""
    db = obtener_conexion()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM pagos")
    pagos = cursor.fetchall()
    cursor.close()
    db.close()
    return pagos

def consultar_aceptados():
    """Obtiene el listado completo de estudiantes registrados y cuyo pago ya fué procesado."""
    db = obtener_conexion()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM usuarios_aceptados")
    estudiantes = cursor.fetchall()
    cursor.close()
    db.close()
    return estudiantes

def vincular_pago_exitoso(carnet, id_pago, id_ticket):
    """
    Registra la validación oficial en la BD, pasa el pago a VALIDADO
    y asigna el ticket correspondiente.
    """
    db = obtener_conexion()
    cursor = db.cursor()

    # Crear registro de validación
    cursor.execute("""
        INSERT INTO validaciones (carnet, id_pago, id_ticket)
        VALUES (%s, %s, %s)
    """, (carnet, id_pago, id_ticket))

    # Actualizar estado del pago
    cursor.execute("UPDATE pagos SET estado_pago = 'VALIDADO' WHERE id_pago = %s", (id_pago,))

    # Actualizar estado del ticket
    cursor.execute("UPDATE tickets SET estado = 'ASIGNADO' WHERE id_ticket = %s", (id_ticket,))

    db.commit()
    cursor.close()
    db.close()


def validar_ingreso_qr(codigo_qr, dia=1):
    """
    Valida el acceso por día (dia=1 o dia=2).
    Comprueba si el QR existe, si está asignado y si ya ingresó en el día correspondiente.
    """
    if dia not in [1, 2]:
        return {"autorizado": False, "mensaje": "DÍA DE EVENTO INVÁLIDO (Debe ser 1 o 2)"}

    db = obtener_conexion()
    cursor = db.cursor(dictionary=True)

    query = """
        SELECT t.id_ticket, t.estado AS estado_ticket, 
               v.id_validacion, v.ingreso_dia_1, v.ingreso_dia_2,
               e.nombre_completo, e.carnet
        FROM tickets t
        LEFT JOIN validaciones v ON t.id_ticket = v.id_ticket
        LEFT JOIN estudiantes e ON v.carnet = e.carnet
        WHERE t.codigo_qr = %s
    """
    cursor.execute(query, (codigo_qr,))
    resultado = cursor.fetchone()

    if not resultado:
        cursor.close()
        db.close()
        return {"autorizado": False, "mensaje": "TICKET INEXISTENTE"}

    if resultado["estado_ticket"] not in ["ASIGNADO", "UTILIZADO"]:
        cursor.close()
        db.close()
        return {"autorizado": False, "mensaje": "TICKET NO ASIGNADO A NINGÚN ESTUDIANTE"}

    # Campo de la BD según el día
    columna_ingreso = f"ingreso_dia_{dia}"

    # Verificación de reingreso en el mismo día
    if resultado[columna_ingreso] is not None:
        hora_ingreso = resultado[columna_ingreso].strftime('%H:%M:%S')
        cursor.close()
        db.close()
        return {
            "autorizado": False,
            "mensaje": f"ACCESO DENEGADO: Ya ingresó el Día {dia} a las {hora_ingreso} ({resultado['nombre_completo']})"
        }

    # Registrar el ingreso del día correspondiente
    fecha_actual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Actualizar la validación con la fecha de llegada de este día
    query_update = f"UPDATE validaciones SET {columna_ingreso} = %s WHERE id_validacion = %s"
    cursor.execute(query_update, (fecha_actual, resultado["id_validacion"]))

    #Marcar el ticket como UTILIZADO globalmente si ya completó ambos días
    cursor.execute("UPDATE tickets SET estado = 'UTILIZADO' WHERE id_ticket = %s", (resultado["id_ticket"],))

    db.commit()
    cursor.close()
    db.close()

    return {
        "autorizado": True,
        "mensaje": f"ACCESO AUTORIZADO (Día {dia}): Bienvenido/a {resultado['nombre_completo']}",
        "carnet": resultado["carnet"]
    }

# ==========================================
# peuab de conexión, paver si jala la wea
# ==========================================
if __name__ == "__main__":
    try:
        inicializar_base_datos()
        estudiantes = consultar_estudiantes()
        print("Conexión exitosa a Alwaysdata. Estudiantes encontrados en BD:")
        if not estudiantes:
            print("  (Aún no hay estudiantes registrados en la tabla)")
        for est in estudiantes:
            print(f"  - {est['carnet']}: {est['nombre_completo']}")
    except Exception as e:
        print("Error al conectar con la base de datos:", e)