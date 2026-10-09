# Publicar el Simposio en Render (paso a paso)

## 0. Antes de subir nada
- Usa un repositorio **privado** en GitHub. El proyecto contiene Excel con nombres y correos de personas.
- El archivo `REPORTE DE TESORERIA...xlsx` ya está en `.gitignore`: no lo subas.

## 1. Subir el proyecto a GitHub
1. Crea un repositorio privado en https://github.com/new
2. En la carpeta del proyecto:
   ```
   git init
   git add .
   git commit -m "Simposio"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
   git push -u origin main
   ```

## 2. Crear el servicio en Render
1. Entra a https://render.com e inicia sesión con GitHub.
2. **New +  →  Web Service**  →  elige tu repositorio.
3. Configura:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. En **Environment Variables** agrega:
   | Variable | Valor |
   |---|---|
   | `ADMIN_PASSWORD` | la contraseña de administrador que tú elijas |
   | `SECRET_KEY` | un texto largo y aleatorio (firma los QR; si cambia, los boletos ya emitidos dejan de valer) |
   | `DATA_DIR` | `/var/data` (solo si usarás disco persistente, paso 3) |
5. **Create Web Service**. Al terminar te da un enlace `https://tu-app.onrender.com` (con HTTPS, así la cámara funciona en celulares).

## 3. Que los datos no se pierdan
La app guarda eventos, participantes e ingresos en archivos CSV dentro de `DATA_DIR` (participantes, pagos, tickets).
En Render el disco normal **se borra** en cada reinicio o nuevo despliegue.
- Opción A: agregar un **Disk** al servicio (requiere plan de pago; revisa el precio vigente en Render):
  *Disks → Add Disk →* Mount Path `/var/data`, y define `DATA_DIR=/var/data`.
- Opción B (sin disco): volver a subir los Excel cada vez que el servicio reinicie.
  Los ingresos y los eventos creados se perderían.

## 4. Usarla
- Estudiantes: entran a `https://tu-app.onrender.com` → **Mi boleto** (carnet + número de recibo).
- Organizadores: **Administrador** → contraseña → Cargar datos, Pagos, Revisión, Tickets e Ingreso.
- En el celular, el Lector QR pedirá permiso de cámara: acéptalo.

## Notas
- En el plan gratuito Render “duerme” el servicio tras un rato sin visitas; la primera carga puede tardar ~1 minuto.
  Ábrelo unos minutos antes del evento.
- Sin `ADMIN_PASSWORD`, la app genera una clave temporal y la muestra en los logs. Define siempre la tuya.
- Alternativa gratuita con disco persistente: PythonAnywhere (https://www.pythonanywhere.com).
