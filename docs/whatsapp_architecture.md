# WhatsApp MVP H4U

## Alcance y fuente de verdad

WhatsApp es transporte. Solicitudes, reservas, pagos y autorización siguen perteneciendo
a PostgreSQL y los servicios H4U. No hay IA, frontend, pagos externos, creación automática
de cuentas ni ejecución de comandos comerciales desde teléfonos.

Se inspeccionaron conversations, interactions, sessions, travelers, partners, users,
service_requests, request_partners, reservations y payments. Conversations exige una
session, que exige destination. Un remitente desconocido no permite deducir esos datos.
Por eso 007 añade transporte con `channel_threads.conversation_id` opcional: no duplica
el historial turístico ni crea sessions/destinations ficticios. Las interacciones actuales
siguen intactas. Vincular conversaciones turísticas será una operación autenticada futura.

## Persistencia y flujo

- `channel_threads`: canal/proveedor/cuenta/destinatario y vínculo opcional traveler O
  partner. El par cuenta + dirección identifica el hilo local; no autentica al usuario.
- `inbound_messages`: ID externo, tipo, texto acotado y fechas; dirección inbound implícita.
- `notification_events`: hechos comerciales mínimos, referencias y clave única.
- `message_outbox`: dirección outbound implícita, contenido mínimo, clave idempotente,
  estado, ID externo, intentos, próximo intento, timestamps y error categórico.
- `channel_binding_events`: historial append-only de consentimiento/vinculación por admin.

Triggers capturan seis tipos: request.created, partner.request, partner.response,
reservation.confirmed, payment.confirmed y reservation.cancelled. Los eventos se escriben
en la MISMA transacción del negocio: rollback comercial implica rollback del evento.
No hay llamadas HTTP desde triggers ni transacciones comerciales. El worker materializa
los eventos confirmados en mensajes y hace el envío fuera de la transacción.

007 también añade `service_requests.messaging_suppressed`: las simulaciones actuales
quedan marcadas mediante configuración transaccional desde el endpoint y se excluyen
sus notificaciones presentes y futuras. No hay backfill de registros históricos.
Una avería de Meta deja pendiente/fallida la notificación, nunca revierte un negocio confirmado.
Un fallo de PostgreSQL al capturar el evento sí revierte la transacción: no se silencian
fallos de persistencia ni se pretende garantizar outbox sin durabilidad.

## Webhook

`GET /webhooks/whatsapp`: subscribe + verify token comparado en tiempo constante,
challenge numérico devuelto como texto. El query string se retira del scope antes de
emitir la respuesta para evitar el token de verificación en access logs de Uvicorn.
También debe redactarse el query string en proxies/túneles externos.

`POST /webhooks/whatsapp`: HMAC-SHA256 del cuerpo ORIGINAL contra X-Hub-Signature-256,
límite de 256 KiB leído por streaming, máximo 100 eventos normalizados, cuenta receptora
exacta y parsing defensivo. No hace llamadas Meta, IA ni acciones comerciales.
Config ausente: 503; firma inválida: 403; JSON/evento inválido: 400; tamaño excesivo: 413.
Un error transaccional no se responde como éxito, permitiendo reentrega del proveedor.

Solo se guarda texto de mensajes text, hasta 4096 caracteres. Media e interactivos se
registran por tipo/ID, sin descargar archivos ni guardar payload completo. Fechas futuras
más allá de cinco minutos se rechazan. Cada message ID se deduplica dentro de su hilo.
La hora de un inbound duplicado no vuelve a abrir la ventana de conversación.

## Autorización, turista y partner

Un desconocido obtiene únicamente un hilo de transporte. No se busca ni vincula
automáticamente por users.email, travelers.external_id o partners.whatsapp.
Admin puede vincular explícitamente un hilo a un traveler O partner existente, certificando
verificación del destinatario y consentimiento fuera del canal, con motivo obligatorio.
No se permite reasignar un hilo ya vinculado a otra identidad. Revocar consentimiento
impide futuros envíos proactivos aún pendientes; un envío ya en curso puede completarse.
La vinculación NO concede membresías, roles H4U ni acceso a finanzas.

`parse_command` reconoce ACEPTAR/RECHAZAR + UUID de
candidatura como intención que requiere API H4U autenticada. No se ejecuta desde el webhook.
El partner debe usar el endpoint comercial existente, JWT, membresía y ownership actual.
Un teléfono o UUID conocido no es una capability. Partner A no puede actuar por B.
El futuro handoff humano/IA podrá consumir mensajes normalizados sin sustituir Auth.

## API administrativa

| Método y ruta | Acceso | Propósito |
|---|---|---|
| GET /admin/messaging/outbox | admin/operator | Estado, intentos, error seguro; sin texto, teléfono ni credenciales |
| GET /admin/messaging/threads | admin | Identificadores de hilos y vínculos, sin contenido |
| GET /admin/messaging/threads/{id} | admin | Destinatario necesario para verificar vínculo fuera del canal |
| PUT /admin/messaging/threads/{id}/binding | admin | Vínculo verificado y consentimiento, motivo obligatorio |
| POST /admin/messaging/threads/{id}/replies | admin | Texto en outbox, idempotency_key obligatorio |

Listas limit/offset, máximo 100. Operator no vincula ni envía. Partner/tourist reciben 403.
Los endpoints administrativos reusan Auth real y validan admin activo en la transacción.
Errores administrativos no devuelven entradas sensibles; Cache-Control no-store.

## Proveedor y templates

`MessagingProvider` define send_text y send_template. MetaProvider implementa POST a
un host fijo graph.facebook.com, versión explícita, timeout de 10 segundos y sin redirects.
No hay dependencia nueva: usa httpx existente. Los tests usan fake o MockTransport.

Texto libre: se comprueba al enviar la ventana de 24 horas desde el último inbound firmado.
Template: requiere consentimiento explícito y configuración de un template aprobado.
La aplicación no registra/aprueba templates en Meta ni presume que un nombre esté aprobado;
el operador debe configurar únicamente templates aprobados antes de habilitar envíos.
Los templates MVP son estáticos, sin parámetros financieros ni datos personales.

Necesitaremos templates aprobados para solicitud creada, solicitud para partner, respuesta
de partner, reserva confirmada, pago confirmado y cancelación. Puede usarse uno genérico
aprobado para varios eventos; el vínculo exacto queda en notification_events/outbox.
Sin configuración para un tipo, el evento permanece pendiente y no bloquea otros tipos.
No se notifican hechos anteriores al opt-in; no se envía retrospectivamente a nuevos vínculos.

## Idempotencia, retries y estados

La clave única del evento evita duplicar hechos; la clave del outbox identifica mensaje
lógico. Reusar clave con otro hilo/contenido devuelve 409. No se vuelven a enviar filas
sent/delivered/read/failed. Worker reclama pending mediante FOR UPDATE SKIP LOCKED,
incrementa intentos, confirma processing y SOLO ENTONCES llama al proveedor.
Otro worker no reclama esa fila. El procesamiento es acotado por invocación.

Errores de conexión previos al envío y HTTP 429: backoff 30, 60, 120, 240 segundos,
máximo cinco intentos. Rechazo HTTP definitivo: failed. HTTP 5xx, timeout de lectura/escritura,
respuesta exitosa sin ID válido o excepción desconocida: delivery_unknown, sin retry ciego.
Una fila processing de más de cinco minutos se pone en cuarentena failed/delivery_unknown.
Esto prioriza evitar duplicados cuando el proveedor podría haber aceptado el mensaje.

No se promete exactly-once externo: no existe transacción conjunta PostgreSQL/Meta.
El callback opaco contiene solo el UUID del outbox. Webhook firmado valida cuenta,
destinatario y ID para conciliar incluso si llega antes del ACK o después de un timeout.
Sin callback, se correlaciona por ID externo ya conocido. Estados desconocidos sin
correlación se ignoran, sin almacenar un payload sensible indefinidamente.

Sent → delivered → read es monotónico. Failed de entrega puede suceder tras sent, pero
nunca degrada delivered/read. Un sent atrasado no resucita un fallo de entrega confirmado;
delivered/read sí aportan evidencia posterior. Un resultado ambiguo puede conciliarse
mediante un sent firmado. Fallos no provocan reenvío automático con clave nueva.
La revisión manual debe comprobar primero el resultado en Meta.

## Configuración sin secretos en Git

- WHATSAPP_PHONE_NUMBER_ID
- WHATSAPP_VERIFY_TOKEN
- WHATSAPP_APP_SECRET
- WHATSAPP_ACCESS_TOKEN
- WHATSAPP_API_VERSION: versión Graph API soportada, explícita; sin default implícito.
- WHATSAPP_SEND_ENABLED: solo el literal true habilita el worker real; default deshabilitado.
- WHATSAPP_TEMPLATES_JSON: mapa de tipo de evento a objeto {name, language} de templates aprobados.

No se leyó/modificó .env ni se generaron credenciales Meta reales. .env sigue ignorado.
Settings oculta secretos en repr. Errores solo guardan códigos controlados, nunca response
body, headers Authorization, tokens, JWT, contraseñas ni trazas del proveedor.

## Privacidad y operaciones

Se almacenan teléfono/wa_id, texto inbound limitado, respuestas outbound, referencias y
fechas para enrutamiento y trazabilidad. No nombres de perfil, ubicaciones, adjuntos,
payloads íntegros ni errores crudos. El texto puede contener PII enviada por el remitente:
requiere retención limitada, control de acceso y cifrado operacional en producción.
Definir política de retención (por ejemplo, propuesta inicial 30 días de contenido),
conservar claves de deduplicación según horizonte de reentrega, sin borrar historia comercial.
No se ejecutó purga ni se decidió una retención legal definitiva en esta fase.

No hay scheduler/daemon automático, DLQ externa, rate limiter distribuido ni benchmark
de 1.000 concurrentes. Desplegar con TLS, body/time limits del proxy y logs redactados.
El worker debe ejecutarse solo por operador de infraestructura autorizado. Una caída
entre envío y ACK requiere conciliación, no replay masivo. El canal no es certificado
para producción hasta validar Meta real y las condiciones operativas.

## Pruebas y uso local

`.venv/bin/python -m pytest -q tests/test_whatsapp.py`

Tests de firma, challenge, tamaño, parsing, deduplicación, no escalamiento por teléfono,
IDOR, outbox, ventana/consentimiento, proveedor mock, backoff/límite de intentos,
reconciliación temprana, estados atrasados, aislamiento de simulaciones y rollback.
Credenciales efímeras; tablas temporales y rollback; nunca llamadas Meta reales.

`.venv/bin/python -m scripts.audit_whatsapp` inspecciona únicamente conteos/checksum.
`.venv/bin/python -m scripts.apply_whatsapp_messaging` verifica o aplica 007 atómicamente.
`.venv/bin/python -m scripts.process_whatsapp --limit 100` permanece deshabilitado salvo
activación explícita. NO ejecutar con send enabled antes de aprobar conexión real.

007 se validó con dry-run, segunda aplicación idempotente, rollback completo comprobado
y aplicación real registrada con checksum. NO editar 001–007. Próxima migración 008+.

## Pendientes

Meta real: cuenta/número, permisos, credenciales inyectadas fuera de Git, versión API,
HTTPS callback y suscripción messages, templates aprobados, consentimiento verificado,
prueba supervisada y operación del worker. No se realizó compra/configuración externa.
Demo: datos aislados, proveedor fake seleccionable para demo, escenarios guiados y reset
exclusivamente de fixtures autorizadas. IA/automatización y portal partner quedan fuera.

## Referencias oficiales

- https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/
  (SDK archivado, usado únicamente para verificar el contrato de firma/challenge).
- https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api
  (colección oficial Meta; confirmar versión/contrato habilitado antes de conectar producción).

## Validación final

- WhatsApp específico final: **38 passed in 6.27s**.
- Regresión WhatsApp/Auth/Admin/Partners/Commercial: **207 passed in 42.35s**.
- Suite completa, ejecutada UNA sola vez: **331 passed, 1 warning in 69.92s (0:01:09)**.
- Warning preexistente urllib3/LibreSSL; ningún fallo ocultado ni credencial real usada.
- Compilación, import/arranque FastAPI en tests, OpenAPI (7 operaciones nuevas),
  seguridad administrativa, auditor WhatsApp (10 checks), auditores Admin/Partners/Post-auth,
  checksum 007 y git diff --check correctos.
- No mensajes reales ni notificaciones residuales de pruebas; admin y sesión conservados.
- Decisión: **READY_FOR_DEMO_MODE**, para desarrollo local; Meta real y producción pendientes.
- Sin Critical/High identificados abiertos en el alcance validado. El negocio legacy sin
  owner detectado antes de esta fase continúa pendiente, sin cambios en sus datos.
- Documentación Meta consultada: contrato oficial de webhook/colección Postman. Las
  páginas actuales de developers.facebook.com devolvieron HTTP 429; compatibilidad
  con la versión habilitada y comportamiento real quedan para la prueba supervisada.
- Sin commit ni push. No se repitió la suite tras cambios exclusivamente documentales.
