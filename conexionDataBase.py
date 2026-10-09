import mysql.connector

# Credenciales de Alwaysdata
DB_CONFIG = {
    'host': 'mysql-jack-vg.alwaysdata.net',
    'user': 'jack-vg',
    'password': 'Wilson2007..',  # Reemplaza con tu contraseña
    'database': 'jack-vg_simposio_db',
    'port': 3306
}

def obtener_conexion():
    """Establece conexión con la base de datos remota en Alwaysdata."""
    return mysql.connector.connect(**DB_CONFIG)

# Prueba de conexión
if __name__ == "__main__":
    try:
        conexion = obtener_conexion()
        if conexion.is_connected():
            print("¡Conexión exitosa a Alwaysdata!")
            cursor = conexion.cursor()
            cursor.execute("SHOW TABLES;")
            tablas = cursor.fetchall()
            print("\nTablas encontradas en la nube:")
            for t in tablas:
                print(f"  - {t[0]}")
            cursor.close()
            conexion.close()
    except Exception as e:
        print("Error de conexión:", e)