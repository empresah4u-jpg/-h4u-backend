# Consentimiento comercial — F2.1.11

013_offer_consent.sql es aditiva y fue aplicada a DEMO y, tras autorización, a H4U.
No realiza backfill: los consentimientos históricos no se inventan. El despliegue H4U utiliza migrator y runtime separados.

## Contrato

El partner publica `counter_offer` con importe total y moneda explícitos;
horario y condiciones forman parte de esa versión. No selecciona ganador.
GET `/partner-responses/{id}/counter-offer` devuelve `version` entera y las
condiciones de la solicitud vinculadas. El turista autenticado acepta por POST
`/{id}/accept-counter-offer` con `expected_version` entero estricto.
Los timestamps anteriores ya no son un identificador de versión válido.

`ACCEPT` directo del partner devuelve 409: no hay actualmente un canal previo de
consentimiento que autorice una asignación pendiente. Nunca hereda partner_price
ni acepta una contraoferta en nombre del turista.

PostgreSQL incrementa offer_version y captura terms_revision y el snapshot de
solicitud al publicar. Un cambio material de solicitud incrementa su revisión,
aunque después se revierta al valor original. Las ofertas antiguas quedan
inaceptables y deben publicarse de nuevo. No depende de updated_at.

## Evidencia y atomicidad

request_offer_consents almacena usuario activo/turista, traveler, solicitud,
candidatura, partner, producto, versión, revisión, importe, moneda, fecha,
horario, flexibilidad, cantidades, condiciones y accepted_at del servidor.
FK RESTRICT, unicidad por solicitud/candidatura y triggers protegen evidencia.
UPDATE, DELETE y TRUNCATE están prohibidos; el runtime DEMO tiene SELECT/INSERT.

El servidor bloquea/revalida identidad, elegibilidad, expiración, solicitud y
candidatura. Consentimiento, ganador y descarte de otras candidaturas se confirman
en una sola transacción. Dos aceptaciones concurrentes producen un solo ganador.
Un reintento de la misma identidad y versión devuelve el recibo original, sin
reactivar ni modificar estados posteriores. Otra versión no es un reintento.

Las reservas nuevas requieren consent_id: importe, moneda, fecha, horario y
pasajeros salen exclusivamente del consentimiento. Las condiciones permanecen
accesibles por esa FK, no se duplican ni se toman de precios mutables del partner.
Las propuestas no aceptadas conservan su versión actual; este bloque no crea un
archivo histórico de todas las versiones rechazadas.

## Compatibilidad y despliegue

Reservas y asignaciones históricas permanecen legibles e intactas, con
consent_id NULL donde corresponde. Solicitudes abiertas deben obtener una nueva
oferta y consentimiento. Una asignación histórica sin consentimiento no habilita
crear una reserva nueva por fallback; requiere resolución explícita.

Ejecutor oficial:
`python -m scripts.apply_offer_consent --demo` ensaya con rollback;
`--demo --apply` persiste tras verificar ledger, checksum y fingerprints históricos.
La repetición es idempotente. H4U requiere --confirm-h4u con el checksum aprobado
y --apply para persistir; véase [roles de conexión](database_roles.md).
No se modifican productos Ballestas, partners reales, frontend ni WhatsApp.

Las pruebas de consentimiento usan una base desechable y datos ficticios. Las
pruebas compartidas de identidad usan las tablas reales solamente en esa base
vacía; fuera de ella conservan el aislamiento temporal previo.

## Cierre E2E — F2.1.12

`tests/test_commercial_e2e.py` crea un destino/producto TEST, dos partners
habilitados, dos excluidos y turistas autenticados con JWT real de test. Usa
LOGIN PostgreSQL restringido y la misma política de tablas/columnas del runtime,
sin sustituir la identidad HTTP. Las ofertas PEN 90/10:00 y PEN 100/11:00 son
exclusivamente fixtures en una base desechable.

Verifica distribución, coexistencia de ofertas sin ganador, ACCEPT unilateral
rechazado, ownership, versiones incorrectas, campos económicos no admitidos en
la aceptación, solicitudes cerradas, invalidación material y republicación.
La aceptación exacta produce consentimiento/ganador atómicos; el replay conserva
el recibo. Dos aceptaciones concurrentes y dos creaciones de reserva dejan un
consentimiento, un ganador y una reserva. Se comprueba el índice único de ganador,
la referencia al consentimiento y que cambiar partner_price no recalcula la
reserva. UPDATE/DELETE/TRUNCATE de consentimiento fallan con el runtime.

`tests/test_commercial_concurrency.py` ejecuta las carreras financieras y de
capacidad con commits reales en esa misma clase de base desechable. Ya no cambia
silenciosamente a DEMO persistente. `scripts.test_ingestion_isolated` verifica
que la base y los roles generados fueron eliminados al terminar.

Ejecutar con `python -m scripts.test_ingestion_isolated -q --tb=line`.
Los tests preexistentes específicamente diseñados para DEMO mantienen su destino
DEMO y sus registros de simulación; no son datos de H4U. Las pruebas nuevas E2E
y de concurrencia se eliminan con su base desechable. No se activa ningún producto
real. La suite conserva la advertencia conocida urllib3/LibreSSL del entorno.

Clasificación del diff acumulado desde 593e2c9:

- A / F2.1.10: provision_ballestas, su prueba y neutral_ballestas.md.
- B / F2.1.11: 013, offer_consent, routers de respuestas/reservas, runner de
  migración, compatibilidad DEMO y fixtures de consentimiento/comerciales.
- C / separación de conexiones: app/db.py, setup_runtime/check_runtime,
  database_roles.md, pruebas de conexión e imports explícitos de los CLI admin.
- D / F2.1.12: E2E, concurrencia desechable, comprobación de limpieza y correcciones
  de alcance en tests (lock de users y buckets de throttling propios).

No se cambió la semántica del motor ni se creó otra migración para este cierre.
