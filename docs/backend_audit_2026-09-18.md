# Auditoría backend H4U — 18 de septiembre de 2026

## A. Estado general

Backend funcional en el entorno local inspeccionado, con correcciones verificadas,
pero **todavía no apto para exponer operaciones comerciales en producción**.
FastAPI importa sin cargar el modelo de IA; OpenAPI contiene 25 rutas. Los endpoints
principales responden y el flujo de efectivo hasta liquidación pasa pruebas SQL reales.
No se afirma cobertura exhaustiva de todos los estados ni de concurrencia en carga.

PostgreSQL 16.15 responde; pgvector 0.8.6 y pgcrypto 1.3 están instalados.
Docker muestra `h4u-postgres` saludable y `h4u-api-test` activo. No se reconstruyeron
ni reiniciaron contenedores: la validación de código usa el workspace, no garantiza
que el contenedor API ya esté ejecutando estos cambios.

## B. Hallazgos

- Efectivo: confirmar primero como turista bloqueaba la confirmación posterior del partner.
- Respuestas de partners: rechazo/contraoferta podían sobrescribir una candidatura
  ganadora por leer estados antes de bloquear. Solo aceptación bloqueaba la solicitud.
- Pasajeros: el límite de un principal se validaba solo dentro del lote, no contra
  registros existentes. Faltaban controles de nombres/documentos vacíos, fecha futura
  y documentos repetidos entre lotes.
- Pagos: faltaba validación del importe almacenado antes de insertar; PostgreSQL admite
  valores numéricos especiales que una comparación simple no cubre.
- Reembolsos: el bloqueo del pago ya protegía el saldo acumulado, pero una referencia
  externa repetida podía registrar dos movimientos parciales.
- Comisiones: faltaba impedir porcentajes mayores de 100, valores no finitos y
  comisiones fijas superiores al pago.
- Bloqueos: cancelación y confirmación no seguían el mismo orden reserva/pago;
  verificación y vencimientos tampoco seguían el orden partner/cierre de creación.
- Búsqueda general: exponía `str(exception)` al cliente y el total podía superar
  la cantidad realmente devuelta después del límite.
- Importar la API cargaba SentenceTransformer y podía fallar/bloquear el arranque
  por acceso a Hugging Face. Se reprodujo un error de colección en pytest por DNS.
- Geocoding ejecutaba consultas y, en servicios, escrituras al importar el módulo.
- Rebuild de embeddings actualizaba el vector sin actualizar `embedding_model`.
- Solo había una migración (refunds); las otras estructuras comerciales ya existen
  en PostgreSQL pero carecían de definición versionada en el repositorio.
- `app/models` y `app/services` son paquetes vacíos: los esquemas Pydantic están en
  los routers. `pipelines` y clientes/extractores de ingesta son placeholders vacíos.
- Documentación de arquitectura y roadmap está desactualizada frente al flujo comercial.
- `components/DestinationMapClient.tsx` es un archivo frontend previo, fuera del backend;
  referencia `./DestinationMap`, ausente en este repositorio. No se modificó.

### Lectura de integridad en PostgreSQL

Nueve consultas de solo lectura, todas con resultado **0**:

| Comprobación | Casos |
|---|---:|
| Varias filas de pago activo por reserva | 0 |
| Varios pasajeros principales por solicitud | 0 |
| Reembolsos procesados superiores al pago | 0 |
| Pagos reembolsados con comisión earned/settled | 0 |
| Reserva incompatible con candidatura ganadora | 0 |
| Pago paid en reserva cancelled | 0 |
| Comisión superior a la base o NaN | 0 |
| Restricciones PostgreSQL sin validar | 0 |
| Total de liquidación distinto a sus partidas | 0 |

Se verificaron FK y restricciones de estado/importe. Existen unicidad de reserva
por solicitud, índice parcial de ganador por solicitud, índice de comisión por pago,
unicidad de comisión dentro de liquidaciones y de partner/período/moneda.
No hay índice único para pago activo por reserva ni para referencia de refund;
la API depende del bloqueo del registro padre. Escrituras externas pueden saltarlo.
Estos conteos no constituyen una certificación de toda la información histórica.

## C. Archivos intervenidos

- `.dockerignore`, `.gitignore`: excluir variantes de archivos de entorno y cachés.
- `app/db.py`: límite de 5 segundos para conectar.
- `app/routers/payments.py`: ambos órdenes de confirmación, bloqueo ordenado,
  validación de importes y protección de estados reembolsados frente a otro pago.
- `app/routers/partner_responses.py`: bloqueo y relectura de estados para todas
  las acciones; Decimal de dos decimales y validación de moneda.
- `app/routers/passengers.py`: validaciones individuales y de conjunto persistido.
- `app/routers/reservations.py`: validación de precio no finito/negativo y moneda.
- `app/routers/refunds.py`: precisión/longitud y rechazo de referencias repetidas
  dentro del mismo pago. Este archivo ya existía sin seguimiento al comenzar.
- `app/routers/commissions.py`: validación de configuración monetaria.
- `app/routers/settlements.py`: moneda y orden de bloqueos.
- `app/routers/search.py`: privacidad del error y total coherente con los resultados.
- `app/routers/semantic_search.py`: carga diferida y protegida del modelo.
- `scripts/geocode_places.py`, `scripts/geocode_services.py`: ejecución explícita mediante main.
- `scripts/rebuild_embeddings.py`: carga del modelo al ejecutar y metadato coherente.
- Nuevos: `scripts/audit_database.py`, `tests/test_commercial.py`, `tests/test_smoke.py`,
  `db/schema_snapshot.sql`, `db/README.md` y este informe.

`app/main.py` ya tenía cambios para registrar refunds; se conservaron sin añadir
cambios propios. `db/migrations/001_create_refunds.sql` y `components/` también eran
previos y se conservaron. No se modificaron requirements, credenciales ni `.env`.

## D. Correcciones y comportamiento resultante

Solicitud → respuesta: cualquier respuesta bloquea primero la solicitud y relee
la candidatura; una solicitud ya asignada deja de aceptar rechazos/contraofertas.
Reserva: sigue exigiendo solicitud asignada, producto habilitado y ganador coherente;
se conserva el rechazo 409 a duplicados. Pasajeros: las validaciones consideran los
lotes anteriores y el estado avanza al completar el número esperado.

Pago: ambas partes pueden confirmar en cualquier orden; la segunda completa pago y
reserva en una transacción. Una reserva cancelada no admite confirmación. Fallar la
actualización de reserva revierte también el pago, probado mediante fallo inducido.

Refund: saldo y estado se conservan bajo bloqueo; referencia externa repetida devuelve
409. Esto evita duplicar ese movimiento, pero no devuelve la respuesta original del
primer intento ni resuelve reintentos sin referencia.

Comisión/liquidación: los importes se validan antes de insertar; se conservan respuestas
idempotentes `already_existed`/`already_verified`. Verificación y vencimientos bloquean
primero partners, en el mismo orden que creación. No se implementó política nueva de
reversión de comisiones ni de suspensión administrativa.

## E. Tests existentes

8 casos en `test_api.py` y `test_semantic_quality.py`: health, destinos, hoteles,
búsqueda semántica y consultas en español/inglés/portugués/atracciones. Requieren
PostgreSQL con dataset y modelo local o acceso a Hugging Face. Su validación de
calidad semántica es superficial: comprueban resultados y categoría, no relevancia.

## F. Tests añadidos

49 casos parametrizados adicionales:

- 37 comerciales: flujo completo de efectivo → comisión → liquidación en ambos
  órdenes de confirmación; reservas repetidas/canceladas y estados inválidos;
  pagos repetidos y cancelación de pendientes; pasajeros entre lotes; refunds
  parciales/totales, exceso y referencia duplicada; relaciones inexistentes;
  importes inválidos, NaN y precisión; comisión inválida; rollback tras fallo;
  fechas/monedas de liquidación; respuesta posterior a asignación; vencimientos.
- 12 smoke/contrato: diez endpoints de lectura, OpenAPI y privacidad del error SQL.

Fixtures comerciales usan transacción externa con `force_rollback=True`, savepoints
por operación y commits internos interceptados. Solo crean registros propios.
La prueba global de vencimientos usa tablas temporales que ocultan las reales en esa
conexión, cuya creación también se revierte. No ejecuta vencimientos sobre datos reales.
No hay pruebas de estrés con conexiones concurrentes ni pruebas de proveedores de pago.

## G. Resultado exacto

Comando final: `.venv/bin/python -m pytest -q`

```text
57 passed, 1 warning in 14.70s
```

Advertencia: urllib3 detecta LibreSSL 2.8.3 en el Python 3.9 local, en lugar de
OpenSSL compatible. Docker usa Python 3.11. No se cambió el intérprete local.

La primera ejecución terminó con **1 error de colección** por DNS de Hugging Face.
Una ejecución intermedia detectó un argumento omitido en la nueva función de carga;
se corrigió antes de validar. Las pruebas comerciales iniciales tuvieron 2 fallos
por usar fecha Python para un corte basado en la fecha de PostgreSQL; se ajustó el
test a `CURRENT_DATE`. No se ocultaron fallos con skips ni xfails.

Comprobaciones adicionales: imports de app y geocoding correctos, compilación Python
correcta, ciclo de vida FastAPI con TestClient correcto, 25 rutas OpenAPI, PostgreSQL
conectado, endpoints principales 200, `pip check` sin dependencias rotas, versiones
instaladas coincidentes con requirements, `git diff --check` limpio. `.env` no está
versionado y las cinco variables DB están presentes; no se imprimieron valores.

## H. Pendientes

1. Autenticación/autorización y permisos por actor/propietario: hoy alguien con acceso
   HTTP puede confirmar pagos, verificar cierres, reembolsar y procesar vencimientos.
   CORS no es un control de autorización. Requiere definición de identidad/roles.
2. Política de refunds con comisiones: reembolsar después de generar/liquidar comisión
   no ajusta esa cuenta; se solicitó decisión sobre bloquear temporalmente. No se
   impuso el bloqueo ni se alteraron comisiones existentes sin esa decisión.
3. Alinear embeddings: 267 filas declaran `all-MiniLM-L6-v2`; API/generador actual usan
   `paraphrase-multilingual-MiniLM-L12-v2`. Como el rebuild antiguo no actualizaba el
   metadato, no puede asegurarse qué modelo produjo cada vector. No se regeneraron.
4. Completar el flujo de métodos de pago distintos a efectivo/partner: se aceptan,
   pero no hay endpoint de verificación ni webhook que los lleve a paid.
5. Versionado real de migraciones y prueba de restauración limpia. La instantánea
   exportada es evidencia, no una migración para ejecutar sobre la base existente.
6. Tests de concurrencia real, carga, autenticación, calidad semántica y CI reproducible.
7. Suspensión: verificación reactiva cualquier partner suspended sin guardar motivo;
   podría reactivar una suspensión administrativa. Hace falta política/registro de causa.
8. Solicitud y reserva conservan estados independientes; cancelar reserva no cancela
   solicitud/candidaturas ni permite crear otra por la unicidad. Definir reapertura.

## I. Riesgos técnicos restantes

- `simulation=true` es público y permite omitir habilitación comercial al crear
  solicitudes, sin separar los datos simulados de los reales.
- Contraoferta aceptada por el partner puede fijar precio sin aceptación explícita
  del turista; política de consentimiento y precio requiere definición.
- Configuración de partner, precio, comisión y requires_payment se lee en diferentes
  momentos; no existe snapshot de condiciones comerciales ni revalidación completa
  de suspensión del partner al reservar.
- Datos personales y motivos de error SQL pueden aparecer en logs de excepciones;
  falta política de redacción/retención. Se corrigió la exposición HTTP en búsqueda.
- Dinero se calcula con Decimal en puntos críticos, pero las respuestas conservan
  floats para compatibilidad; `none` como tipo de comisión existe en DB y no tiene
  tratamiento comercial en el endpoint (409).
- Referencias de refund opcionales: no hay garantía de idempotencia sin referencia,
  ni validación global de referencias entre pagos/proveedores. Refund registra una
  devolución como processed sin ejecutar/verificar una transferencia externa.
- La búsqueda general/semántica no limita por destino; paginación tiene formatos
  distintos entre hoteles y otros catálogos. Se mantuvo compatibilidad.
- Ingesta aún vacía; scripts de embeddings y geocoding hacen operaciones reales al
  ejecutarse. `generate_embeddings` contiene DELETE por entidad y no se ejecutó.
- No hay pool, timeouts de consulta/bloqueo generales, rate limit ni observabilidad
  estructurada. El modelo se carga por proceso y la primera búsqueda puede ser lenta.
- Docker depende de volumen externo y del modelo descargado en build; expone
  PostgreSQL en todas las interfaces según compose. No se alteró despliegue ni se
  ejecutó build/restauración. No se hizo auditoría CVE externa de dependencias.
- Persisten faltas de restricciones compuestas para coherencia entre varias FK;
  los controles API y nueve consultas de auditoría no sustituyen esas restricciones.

## J. Siguiente paso

Aprobar las correcciones de esta auditoría. Antes de nuevas funcionalidades, decidir
identidad/permisos y política de reembolsos/comisiones, alinear embeddings con una
regeneración controlada y validar una base de pruebas reconstruida desde migraciones.
Después completar pagos no efectivos y ampliar pruebas concurrentes.

No se realizó commit, push, DROP, TRUNCATE, cambio de secretos ni modificación
persistente de datos existentes. No se enviaron mensajes ni notificaciones a terceros.

## K. Estado Git

La salida exacta final se incluye en la respuesta de entrega. Distinguir archivos
preexistentes no versionados de los añadidos durante esta auditoría según sección C.
