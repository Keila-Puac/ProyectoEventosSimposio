import os
from dotenv import load_dotenv
from generador_qr_pdf import enviador_para_automata

# 1. Cargar variables de entorno desde el archivo .env
load_dotenv()

# 2. Datos de prueba
CORREO_PRUEBA = "jackelinvasquezguzman@gmail.com"
NOMBRE_PRUEBA = "Jackelin Vásquez (Prueba)"
CARNET_PRUEBA = "9999926"
CADENA_QR_PRUEBA = "URL-SIMPOSIO-2026-TEST-9999926"

if __name__ == "__main__":
    print("=== INICIANDO PRUEBA DE EMISIÓN Y ENVÍO DE TICKET ===")

    # Verificación de variables de entorno
    api_key = os.getenv("SENDGRID_API_KEY")
    remitente = os.getenv("MAIL_USER")

    if not api_key:
        print("⚠ ALERTA: No se encontró 'SENDGRID_API_KEY' en el archivo .env")
    if not remitente:
        print("⚠ ALERTA: No se encontró 'MAIL_USER' en el archivo .env")

    print(f"Enviando ticket de prueba a: {CORREO_PRUEBA}...")

    # 3. Ejecutar la función emisora
    resultado = enviador_para_automata(
        destino=CORREO_PRUEBA,
        nombre=NOMBRE_PRUEBA,
        carnet=CARNET_PRUEBA,
        cadena_qr=CADENA_QR_PRUEBA
    )

    if resultado:
        print("\n¡Prueba completada con éxito!")
        print(f"1. Se generó el PDF en la carpeta 'tickets_pdf/Ticket_{CARNET_PRUEBA}.pdf'")
        print("2. Revisa tu bandeja de entrada (y la carpeta de spam).")
    else:
        print("\nLa prueba falló al enviar el correo. Revisa las alertas arriba.")