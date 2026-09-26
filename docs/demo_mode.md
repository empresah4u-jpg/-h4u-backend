# Modo Demo persistente y restringido H4U

## Uso local

```sh
.venv/bin/python -m scripts.setup_demo
.venv/bin/python -m scripts.run_demo --scenario accept
.venv/bin/python -m scripts.run_demo --scenario counter_offer
.venv/bin/python -m scripts.run_demo --list
.venv/bin/python -m scripts.audit_demo
```

Setup es administrativo; el runner y el auditor son runtime restringido. No frontend,
servidor público, transferencias ni llamadas Meta. Cada ejecución conserva un resumen
sin credenciales y crea datos ficticios nuevos. --list consulta los últimos 20 runs.

## Dos servidores independientes

| Uso | Contenedor | Base | Puerto local | Rol |
|---|---|---|---|---|
| Backend existente | h4u-postgres | h4u | 5432 | Configuración existente, intacta |
| Setup demo | h4u-demo-postgres | postgres/h4u_demo | 55432 | h4u_demo_setup |
| Runtime demo | h4u-demo-postgres | h4u_demo | 55432 | h4u_demo_app |

La copia legacy h4u_demo del servidor original se conserva sin modificaciones; el
runtime nuevo NO la utiliza. Se trasladaron exclusivamente sus datos ficticios, incluidos
los cinco runs fallidos, al volumen h4u_demo_pgdata_isolated. No se copian datos de h4u.

El servidor original tiene reglas trust y permisos CONNECT públicos. Un rol nuevo en
ese servidor no bastaba para aislamiento fuerte sin alterar su autenticación. El usuario
eligió un contenedor independiente: no se cambiaron HBA, roles ni permisos del servidor
original. El rol demo no existe en él y las conexiones con credenciales demo se rechazan.
La comprobación de denegación no acepta un timeout/red caída como prueba de seguridad.

## Credenciales y privilegios

Setup genera credenciales independientes, aleatorias, locales y no mostradas:

- .demo-private/admin_password: secreto de inicialización del contenedor, archivo 0600.
- .env.demo: exclusivamente coordenadas/credencial del runtime, archivo 0600.

Ambas rutas están ignoradas por Git. No se cambia .env. Runtime lee únicamente variables
DEMO_DB_HOST/PORT/NAME/USER/PASSWORD o .env.demo; elimina DB_*, JWT_*, WHATSAPP_*, PG* y POSTGRES_* heredados,
impide cargar el dotenv de producción y crea su clave JWT efímera independiente.
No conserva credenciales administrativas en su configuración runtime.

h4u_demo_app: LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS,
NOINHERIT. CONNECT únicamente a h4u_demo; USAGE en public; SELECT/INSERT/UPDATE en datos;
USAGE/SELECT en secuencias. Sin CREATE en public. Marcador, registro de migraciones e
inventario de identidades reales conocidas son de solo lectura para runtime.
No es propietario de la base ni de las tablas. Las restricciones/triggers existentes
siguen aplicando. Setup usa una identidad administrativa diferente por Docker local.

Antes de entregar cada conexión demo se verifican DB_NAME, DB_USER, current_database(),
current_user, atributos de rol y marcador. Cualquier discrepancia aborta antes de crear
registros comerciales. Las conexiones normales de la aplicación no cambian.

La protección es frente al rol y al recorrido runtime; un administrador del host con
acceso a Docker, archivos de setup o capacidad de modificar Python sigue siendo confiable.
Esta demo no se publica en Internet y no pretende aislar a un administrador del equipo.

## Setup y reconstrucción

Setup usa docker-compose.demo.yml, puerto publicado solo en 127.0.0.1, volumen propio,
imagen pgvector/pgvector:pg16 disponible localmente y etiqueta com.h4u.demo=isolated.
Verifica checksums 002–007 antes de preparar el esquema. En el primer traslado restaura
solo la base legacy marcada demo; en reconstrucción copia únicamente el esquema de h4u,
aplica db/demo/bootstrap.sql y registra sus versiones. Nunca restaura datos reales.

Por defecto conserva una demo existente válida. Un destino no marcado o con versiones
incompatibles detiene la operación. También obtiene únicamente UUIDs de usuarios/sesiones
reales como inventario de comparación de solo lectura en demo_known_real_identities;
no copia cuentas, emails, contraseñas, sesiones ni tokens, ni hardcodea IDs reales.

Reconstrucción explícita, DESTRUCTIVA SOLO PARA DEMO:

```sh
.venv/bin/python -m scripts.setup_demo --database h4u_demo --rebuild --confirm h4u_demo
```

Exige nombre exacto, confirmación exacta, etiqueta del contenedor aislado, marcador correcto
y versiones esperadas. Guarda primero un dump protegido 0600 en .demo-private. Revalida
el marcador y elimina/recrea únicamente h4u_demo en h4u-demo-postgres. No fuerza el cierre
de conexiones activas: si impiden el DROP, se detiene. h4u y nombres arbitrarios se rechazan
antes de crear archivos o invocar Docker. No elimina automáticamente backups.

El rebuild no se ejecutó sobre el historial actual durante esta fase. Se probaron las
validaciones que rechazan nombres/confirmación/marcador incorrectos. Depende del esquema
h4u disponible, Docker y la imagen local; no es un instalador autónomo offline del backend.
La restauración de un backup histórico requiere una operación administrativa explícita;
el rebuild produce una demo vacía, no reproduce sus antiguos registros.

## Escenarios y aceptación de contraoferta

El transporte HTTP local es TestClient con lifespan, JWT/Argon2 y autorización reales,
sin overrides de Auth. Actores ficticios nuevos: admin, tourist y partner owner.

Accept: solicitud → partner acepta → reserva → pasajero → pago cash simulado PEN 100,
confirmado por partner y turista → comisión 10% PEN 10 → settlement PEN 10 reportado por
partner/verificado por admin → siete mensajes fake entregados.

Counter_offer: partner propone PEN 120 → turista consulta y acepta la versión exacta
→ reserva a PEN 120 → doble confirmación de pago simulado → comisión PEN 12 → settlement
PEN 12 → ocho mensajes fake entregados. Los nuevos runs muestran el paso
`tourist_accepts_counter_offer`. Los runs históricos conservan su semántica anterior,
no se reescriben para aparentar una aceptación del turista que no existió.

API comercial compartido (no rutas exclusivas de demo):

- GET /partner-responses/{request_partner_id}/counter-offer
- POST /partner-responses/{request_partner_id}/accept-counter-offer
  con expected_version igual a la versión consultada.

Solo el turista propietario puede usar esas rutas. Se exige precio/moneda explícitos;
se reutiliza la transacción de asignación de ganador. Verificación de versión y estado
bajo lock impide aceptar ofertas cambiadas o procesarlas dos veces; orden de locks
partner → solicitud. Otros turistas/partners se rechazan. No se modificaron fórmulas,
estados SQL ni migraciones. El endpoint anterior de respuesta de partner conserva su
contrato; esta fase no impone una nueva política global a integraciones existentes.

## Finanzas, identidades y mensajería

Las tablas conservan reglas y estados financieros normales, pero residen exclusivamente
en el servidor demo. El resumen y referencias señalan SIMULATED; no hay bancos/pasarelas.
No se usa simulation=true de solicitudes, porque esa bandera suprime notificaciones.
Aquí toda la base es demo y ejercitamos su outbox con transporte fake persistente.

MetaProvider rechaza construcción y envío si detecta demo. FakeProvider no hace HTTP/DNS;
solo inserta un recibo idempotente. Runtime no requiere ACCESS_TOKEN, APP_SECRET ni
PHONE_NUMBER_ID de WhatsApp. El identificador de cuenta fake se genera por run.

Passwords demo se generan en memoria y solo sus derivados de autenticación quedan en
usuarios demo. JWT efímero independiente; logout al cerrar. No hay credenciales reutilizables
ni interfaz interactiva de login. El historial de cinco fallos se conserva: tres fallos por
longitud de código (dos sin causa registrada) y dos por nombre de columna incorrecto;
ambas causas fueron corregidas. No se inventan causas retroactivas ni se borran registros.

## Auditor y pruebas

Audit_demo conserva los 13 controles anteriores y añade identidad efectiva de DB/rol,
atributos restringidos, marcador, permisos CONNECT/DDL, rechazo real de conexiones al
servidor original/base administrativa, inventario dinámico de identidades conocidas,
namespace ficticio, outbox fake y coherencia de montos/propiedad del circuito finalizado.
No muestra credenciales, hashes, tokens, cuerpos de mensajes o IDs reales.

Tests específicos: tests/test_demo.py y tests/test_counter_offer.py. Regresión relacionada
incluye Auth, Admin, Partners, WhatsApp, reservas/pagos/comisiones/settlements.
Suite completa una única vez al finalizar. Los tests de integración demo conservan sus
runs; las pruebas comerciales normales usan fixtures con rollback.

## Resultado final

- Tests específicos Demo + contraoferta: **20 passed in 12.81s**.
- Prueba adicional de eliminación de variables administrativas: **1 passed in 0.71s**.
- Regresión relacionada: **230 passed in 53.62s**.
- Suite completa ÚNICA: **351 passed, 1 warning in 84.89s (0:01:24)**.
- Warning conocido urllib3/LibreSSL; no fallos ocultados.
- Auditor demo: **26 controles sin inconsistencias**, incluidas denegaciones reales.
- Compilación, import/arranque FastAPI en tests, OpenAPI y git diff --check correctos.
- CLI accept y counter_offer verificados con rol restringido; 7/8 mensajes fake.
- Datos fuente h4u: 1 usuario/1 sesión, sin marcador demo ni rol h4u_demo_app.
- Cinco runs fallidos conservados, sin reinterpretar retrospectivamente su historia.
- Rebuild implementado; sus guardas se probaron sin borrar el historial actual.

**READY_FOR_COMMERCIAL_HARDENING / APTO_PARA_COMMIT**, para el alcance local validado.
No se identificaron Critical/High abiertos en el runtime demo. No equivale a autorización
de exposición pública, acceso de usuarios no confiables al host/Docker ni conexión Meta.
Las reglas trust del servidor original no se alteraron; el runtime demo no utiliza sus
credenciales. La copia legacy permanece conservada en ese servidor.
No commit ni push. No se repitió la suite después de cambios exclusivamente documentales.


## Endurecimiento comercial 008

`python -m scripts.run_demo --scenario accept --exercise-lifecycle` usa la misma
lógica comercial para cancelar después del settlement, con una política ficticia
explícita, repetir idempotentemente y confirmar un refund mediante proveedor fake.
La devolución genera crédito de comisión sin alterar el settlement pagado.
Los nuevos mensajes se entregan con FakeProvider. No se reutilizan identidades reales.

La actualización incremental de esquema usa `upgrade_demo_schema` y la misma 008
que h4u, con dry-run, rollback y checksums. Los tests concurrentes conservan fixtures
ficticios RACE en h4u_demo; no se borran datos para limpiar resultados.
