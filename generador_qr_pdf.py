import os
import base64
import requests
import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from PIL import Image as PILImage

from automata import qrDbMng  # Importamos el autómata
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN DE TWILIO SENDGRID ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
MAIL_REMITENTE = os.getenv("MAIL_USER", "applepiee056@gmail.com")


def generar_imagen_qr_personalizado(cadena_qr, ruta_salida, ruta_logo="logo_url.png"):
    """
    Genera un código QR estético con colores institucionales y el logo
    de la Universidad en el centro.
    """
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # Permite hasta 30% de daño/cobertura
        box_size=10,
        border=3,
    )
    qr.add_data(cadena_qr)
    qr.make(fit=True)

    # Generar imagen del QR con colores personalizados (Azul marino / Blanco)
    img_qr = qr.make_image(fill_color="#002B49", back_color="white").convert('RGB')

    # Insertar el escudo si existe la imagen
    if os.path.exists(ruta_logo):
        logo = PILImage.open(ruta_logo)

        # Convertir a RGBA para manejar transparencias
        if logo.mode != 'RGBA':
            logo = logo.convert('RGBA')

        # Redimensionar el logo a un 25% del tamaño total del QR
        qr_width, qr_height = img_qr.size
        logo_size = int(qr_width * 0.25)
        logo = logo.resize((logo_size, logo_size), PILImage.Resampling.LANCZOS)

        # Centrar el logo en el código QR
        pos_x = (qr_width - logo_size) // 2
        pos_y = (qr_height - logo_size) // 2

        # Superponer logo en el centro
        img_qr.paste(logo, (pos_x, pos_y), mask=logo if logo.mode == 'RGBA' else None)

    # Guardar resultado final
    img_qr.save(ruta_salida)
    return ruta_salida


def generar_pdf_ticket(estudiante, cadena_qr, ruta_pdf):
    """Genera el boleto en formato PDF con el QR personalizado y diseño académico."""
    ruta_img_qr = ruta_pdf.replace(".pdf", "_temp.png")

    # Generamos el QR personalizado con el logo
    generar_imagen_qr_personalizado(cadena_qr, ruta_img_qr, ruta_logo="logo_url.png")

    doc = SimpleDocTemplate(
        ruta_pdf,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    story = []
    styles = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle(
        'TituloEvento',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#002B49"),  # Azul institucional
        alignment=1,
        spaceAfter=6
    )

    estilo_subtitulo = ParagraphStyle(
        'Subtitulo',
        parent=styles['Normal'],
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#4A5568"),
        alignment=1,
        spaceAfter=15
    )

    estilo_etiqueta = ParagraphStyle(
        'Etiqueta', parent=styles['Normal'], fontSize=10, leading=13,
        textColor=colors.HexColor("#2D3748"), fontName="Helvetica-Bold"
    )

    estilo_valor = ParagraphStyle(
        'Valor', parent=styles['Normal'], fontSize=10, leading=13,
        textColor=colors.HexColor("#1A202C")
    )

    story.append(Paragraph("UNIVERSIDAD RAFAEL LANDÍVAR", estilo_titulo))
    story.append(Paragraph("Simposio Académico - Boleto Digital de Acceso", estilo_subtitulo))
    story.append(Spacer(1, 10))

    img_qr = Image(ruta_img_qr, width=170, height=170)

    datos_tabla = [
        [Paragraph("Estudiante:", estilo_etiqueta), Paragraph(estudiante.get('nombre_completo', ''), estilo_valor)],
        [Paragraph("Carnet:", estilo_etiqueta), Paragraph(str(estudiante.get('carnet', '')), estilo_valor)],
        [Paragraph("Carrera:", estilo_etiqueta), Paragraph(estudiante.get('carrera', 'N/A'), estilo_valor)],
        [Paragraph("Correo:", estilo_etiqueta), Paragraph(estudiante.get('correo', ''), estilo_valor)],
        [Paragraph("Estado:", estilo_etiqueta),
         Paragraph("<font color='#15803d'><b>PAGO VALIDADO</b></font>", estilo_valor)]
    ]

    tabla_datos = Table(datos_tabla, colWidths=[85, 225])
    tabla_datos.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))

    mitades = [[img_qr, tabla_datos]]
    tabla_principal = Table(mitades, colWidths=[190, 320])
    tabla_principal.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor("#002B49")),  # Borde azul
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))

    story.append(tabla_principal)
    story.append(Spacer(1, 20))

    estilo_nota = ParagraphStyle(
        'Nota', parent=styles['Normal'], fontSize=9, leading=11,
        textColor=colors.HexColor("#64748B"), alignment=1
    )
    story.append(
        Paragraph("Por favor presenta este código QR desde tu dispositivo móvil o impreso al ingresar al evento.",
                  estilo_nota))

    doc.build(story)

    # Eliminar el archivo PNG temporal del QR
    if os.path.exists(ruta_img_qr):
        os.remove(ruta_img_qr)

    return ruta_pdf


def enviar_correo_twilo(destinatario, nombre_asistente, ruta_pdf):
    """Envía el boleto en formato PDF adjunto mediante la API HTTP de Twilio SendGrid."""
    if not SENDGRID_API_KEY:
        print("[MAIL ERROR]: Falta configurar la API Key de Twilio SendGrid en las variables de entorno.")
        return False

    try:
        attachments_list = []

        if os.path.exists(ruta_pdf):
            with open(ruta_pdf, "rb") as archivo:
                archivo_base64 = base64.b64encode(archivo.read()).decode("utf-8")

            attachments_list.append({
                "content": archivo_base64,
                "filename": os.path.basename(ruta_pdf),
                "type": "application/pdf",
                "disposition": "attachment"
            })

        cuerpo_html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <h2 style="color: #1A365D;">¡Hola {nombre_asistente}!</h2>
            <p>Tu pago ha sido validado exitosamente para el <strong>Simposio Académico</strong>.</p>
            <p>Adjunto a este correo encontrarás tu ticket oficial en formato PDF con tu código QR de acceso.</p>
            <p>Por favor, conserva este archivo o descárgalo en tu teléfono para presentarlo al momento de ingresar.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #718096;">Comité Organizador del Simposio</p>
        </div>
        """

        datos = {
            "personalizations": [
                {
                    "to": [{"email": destinatario}],
                    "subject": "Tu Ticket de Acceso Oficial al Simposio"
                }
            ],
            "from": {
                "email": MAIL_REMITENTE,
                "name": "Simposio Académico"
            },
            "content": [
                {
                    "type": "text/html",
                    "value": cuerpo_html
                }
            ],
            "attachments": attachments_list
        }

        respuesta = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json=datos,
            timeout=10  # Si pasan 10 segundos sin respuesta, arrojará un error en lugar de congelarse
        )

        if respuesta.status_code == 202 or respuesta.ok:
            print(f"✓ [TWILIO SENDGRID]: Correo enviado exitosamente a {destinatario}")
            return True
        else:
            print(f"✖ [TWILIO SENDGRID ERROR]: {respuesta.status_code}\n{respuesta.text}")
            return False

    except Exception as e:
        print(f"✖ [TWILIO SENDGRID ERROR]: {e}")
        return False


def enviador_para_automata(destino, nombre, carnet, cadena_qr):
    """Función puente que llama el autómata para procesar cada alumno."""
    os.makedirs("tickets_pdf", exist_ok=True)
    ruta_pdf = os.path.join("tickets_pdf", f"Ticket_{carnet}.pdf")

    estudiante = {"nombre_completo": nombre, "carnet": carnet, "correo": destino}

    # 1. Crear el PDF
    generar_pdf_ticket(estudiante, cadena_qr, ruta_pdf)

    # 2. Enviar por Twilio SendGrid
    return enviar_correo_twilo(destino, nombre, ruta_pdf)


# --- BLOQUE PRINCIPAL DE EJECUCIÓN ---
if __name__ == "__main__":
    print("=== INICIANDO PROCESO DE VALIDACIÓN Y EMISIÓN DE TICKETS ===")

    # Instanciamos el gestor pasándole nuestra función emisora
    gestor = qrDbMng(sesiones=2, enviador=enviador_para_automata)

    print("\n1. Procesando recibos pendientes...")
    res_procesamiento = gestor.procesar_pendientes()
    print(res_procesamiento.get("mensaje", res_procesamiento))

    print("\n2. Generando tickets PDF y enviando vía Twilio SendGrid...")
    res_emision = gestor.emitir_qrs()
    print(res_emision)