"""Envío del boleto por correo con la API HTTP de Twilio SendGrid (misma que usaba generador_qr_pdf.py).

Variables de entorno (o archivo .env):
  SENDGRID_API_KEY  clave de la API de SendGrid
  MAIL_USER         remitente; debe estar verificado en SendGrid (Single Sender o dominio)
"""
import base64
import json
import os
import urllib.error
import urllib.request

URL = "https://api.sendgrid.com/v3/mail/send"


def configurado():
    return bool(os.getenv("SENDGRID_API_KEY") and os.getenv("MAIL_USER"))


def enviar_boleto(destino, nombre, ticket, pdf_bytes):
    """Devuelve (ok, mensaje). El PDF va adjunto."""
    if not configurado():
        return False, "Falta SENDGRID_API_KEY o MAIL_USER."
    cuerpo = f"""
    <div style="font-family: Arial, sans-serif; color: #333; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
        <h2 style="color: #1A365D;">¡Hola {nombre}!</h2>
        <p>Tu pago fue validado para el <strong>XIX Simposio de Ingeniería</strong>.</p>
        <p>Adjunto encontrarás tu confirmación de ingreso (ticket <strong>{ticket}</strong>) con tu código QR.
           Guárdala en tu teléfono o imprímela: se puede usar una sola vez.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="font-size: 12px; color: #718096;">Facultad de Ingeniería · Comité Organizador</p>
    </div>"""
    datos = {
        "personalizations": [{"to": [{"email": destino}], "subject": "Tu confirmación de ingreso al Simposio de Ingeniería"}],
        "from": {"email": os.getenv("MAIL_USER"), "name": "Simposio de Ingeniería"},
        "content": [{"type": "text/html", "value": cuerpo}],
        "attachments": [{"content": base64.b64encode(pdf_bytes).decode(), "filename": f"Boleto_{ticket}.pdf",
                         "type": "application/pdf", "disposition": "attachment"}],
    }
    req = urllib.request.Request(URL, data=json.dumps(datos).encode(), method="POST", headers={
        "Authorization": f"Bearer {os.getenv('SENDGRID_API_KEY')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return (r.status in (200, 202)), f"SendGrid respondió {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"SendGrid {e.code}: {e.read().decode(errors='ignore')[:200]}"
    except Exception as e:  # red caída, timeout…
        return False, str(e)
