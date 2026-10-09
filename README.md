# XIX Simposio de Ingeniería · Validación de pagos y acceso

Web (Flask) que relaciona los pagos de Tesorería con el listado oficial de estudiantes, asigna uno de los
tickets que ya tiene Coordinación, entrega una confirmación digital (QR + PDF) y valida el ingreso una sola vez.

## Flujo
1. **Cargar datos** (admin): sube a la vez el reporte de Tesorería, el listado oficial y los tickets de Coordinación.
   El sistema reconoce cada archivo por sus columnas (Recibo+Nombre · Carnet+Nombre · Ticket), aunque tenga membrete.
2. **Pagos → Conciliar**: AFND que compara cada pago con el listado (validado / revisión / duplicado / incompleto / monto incorrecto).
3. **Revisión**: el administrador resuelve los casos dudosos (acepta con un candidato u otro carnet, o rechaza).
4. **Mi boleto** (estudiante, público): carnet + número de recibo → valida el pago, asigna ticket y muestra QR/PDF.
5. **Ingreso** (personal): escanea el QR o escribe el ticket → nombre, carnet, estado del pago, ticket y autorización.
   El ticket pasa a UTILIZADO; un segundo intento se rechaza.

## Autómatas (`simposio.py`)
**AFND** (identidad): q0 –R→ q1 –N→ q2 –C→ q3 –C→ q4 –S1..S4→ q5.x; q5.1 –V→ q7 (aceptación); q5.2/5.3/5.4 –M→ q6 (revisión) –V→ q7 / –X→ qE.
Σ = {R, N, C, S1, S2, S3, S4, V, M, X}. S1 única ≥ UMBRAL_ALTO · S2 varias ≥ UMBRAL_ALTO · S3 ninguna ≥ UMBRAL_MIN · S4 intermedia.

**AFD de ticket**: DISPONIBLE –asignar→ ASIGNADO –ingresar→ UTILIZADO (en UTILIZADO, «ingresar» se rechaza sin cambiar de estado).
**AFD de pago**: PENDIENTE → VALIDADO | REVISION | DUPLICADO | INCOMPLETO | MONTO_INCORRECTO; REVISION → VALIDADO | RECHAZADO.

## Variables de entorno
| Variable | Uso | Por defecto |
|---|---|---|
| `ADMIN_PASSWORD` | contraseña de administrador | se genera y se imprime |
| `SECRET_KEY` | firma de los QR | se genera en `data/secret.key` |
| `DATA_DIR` | carpeta de los CSV | `./data` |
| `MONTO_SIMPOSIO` | monto correcto del pago | `280` |
| `UMBRAL_ALTO` / `UMBRAL_MIN` | similitud de nombres (%) | `95` / `75` |

Ejecutar: `pip install -r requirements.txt` y `python app.py`.
