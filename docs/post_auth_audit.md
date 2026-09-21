# Auditoría post-auth H4U

## Alcance y criterio de salida

Base: main, b52a252. Existe un administrador local real; no se leyó su contraseña,
hash, JWT ni el contenido de .env. No se modificó esa cuenta ni sus sesiones.
No se ejecutó bootstrap real, ni se cambió dinero existente, ni se hizo commit/push.

Se revisaron los 29 métodos/rutas de OpenAPI y las cuatro rutas de documentación
GET/HEAD (/docs, /redoc, /openapi.json, /docs/oauth2-redirect). No hay routers de
administración de partners/users/products, lecturas de reservas/pagos/settlements
ni endpoints públicos para modificar ajustes o financial_events. No se inventaron
capacidades inexistentes ni se privatizaron catálogos legítimos.

Clasificaciones: PUBLIC; OWNER con rol TOURIST/PARTNER; STAFF = OPERATOR o ADMIN;
INTERNAL para llamadas Python y bootstrap confiables, sin exposición HTTP.
El riesgo de la matriz es impacto potencial, no una vulnerabilidad abierta.

## Matriz de superficie, permisos y estados

| Método | Endpoint | Actor / recurso / ownership | Estado o condición | Riesgo |
|---|---|---|---|---|
| POST | /auth/login | PUBLIC; credenciales verificadas | user active, rate limit, Argon2 | Alto |
| GET | /auth/me | Identidad autenticada propia | JWT, sesión, versión vigentes | Medio |
| POST | /auth/logout | OWNER de sesión | Sesión vigente, revocación | Medio |
| POST | /auth/logout-all | OWNER de usuario | Incremento de versión y revocación propia | Alto |
| GET | /hotels | PUBLIC | Catálogo active, paginado | Bajo |
| GET | /restaurants | PUBLIC | Catálogo active, paginado | Bajo |
| GET | /tours | PUBLIC | Catálogo active, paginado | Bajo |
| GET | /tour-operators | PUBLIC | Catálogo active, paginado | Bajo |
| GET | /attractions | PUBLIC | Catálogo active, paginado | Bajo |
| GET | /transport/routes | PUBLIC | Ruta active, paginada | Bajo |
| GET | /services | PUBLIC | Servicios active, límite | Bajo |
| GET | /search | PUBLIC | Catálogo active, límite | Bajo |
| GET | /semantic-search | PUBLIC | Modelo/dimensión existente; entidad active | Medio |
| GET | /health | PUBLIC | SELECT 1, sin detalles de conexión | Bajo |
| GET | /destinations | PUBLIC | Metadatos de destinos | Bajo |
| POST | /service-requests | TOURIST OWNER de session o STAFF | Sesión active; producto habilitado; simulation solo STAFF | Alto |
| POST | /partner-responses | PARTNER OWNER de candidate o STAFF | searching/offers_received, no asignada; candidato respondible; accept/counter_offer revalida habilitación | Alto |
| POST | /reservations | TOURIST/PARTNER OWNER de request o STAFF | partner_assigned, ganador coherente, producto/partner/vínculo habilitados, reserva única | Alto |
| POST | /reservations/{reservation_code}/cancel | TOURIST/PARTNER OWNER o STAFF | Estado cancelable; sin pago paid/disputed/partially_refunded | Alto |
| POST | /reservations/{reservation_code}/passengers | TOURIST/PARTNER OWNER o STAFF | awaiting_passenger_data, pasajeros/documentos válidos | Alto |
| POST | /payments | TOURIST/PARTNER OWNER de reserva o STAFF | payment_pending, monto desde reserva, sin pago activo duplicado | Alto |
| POST | /payments/{payment_code}/confirm-partner | PARTNER OWNER o STAFF | Efectivo recibido por partner, pago/reserva válidos, no repetido | Alto |
| POST | /payments/{payment_code}/confirm-customer | TOURIST OWNER o STAFF | Efectivo recibido por partner, pago/reserva válidos, no repetido | Alto |
| POST | /refunds | STAFF | paid/partially_refunded, saldo suficiente, idempotencia/referencia | Alto |
| POST | /commissions/from-payment/{payment_code} | STAFF | paid; configuración válida; única por pago | Alto |
| POST | /settlements | STAFF | Partner existente; comisiones earned de misma moneda/partner no vinculadas | Alto |
| POST | /settlements/{settlement_code}/report-payment | PARTNER OWNER o STAFF | Cierre abierto/deuda; reporte no duplicado, conciliación vigente | Alto |
| POST | /settlements/{settlement_code}/verify-payment | STAFF | Reporte conciliado o saldo cero; paid idempotente | Alto |
| POST | /settlements/process-overdue | STAFF | pending_payment y fecha vencida; locks ordenados | Alto |

Conocer un código no autoriza a operar. Las dependencias verifican rol y ownership;
los handlers vuelven a comprobar ownership después de bloquear el recurso. Las
consultas parametrizadas no toman identidad de X-Role/X-Partner-Id/X-Traveler-Id.
El Principal proviene del proveedor JWT y de users en DB. STAFF omite ownership
intencionalmente, por administración global explícita en app/auth.py.

Tourists y partners no procesan refunds ni generan/verifican comisiones o cierres,
aunque el pago sea propio. No existe aún un endpoint de solicitud de refund del
turista. OPERATOR ya pertenece explícitamente a STAFF, incluyendo finanzas; no se
introdujo ni amplió esa facultad. Separar tesorería/operación requiere una decisión
para H4U Admin, no una modificación silenciosa en esta auditoría.

## Hallazgos y correcciones

### Critical

Ninguna vulnerabilidad crítica identificada en la superficie revisada.

### High — H1, corregida

Las candidaturas seleccionadas cuando un partner estaba habilitado podían aceptarse
más tarde estando suspendido o con reservations_enabled=false. Crear reserva tampoco
revalidaba partner ni vínculo comercial. Esto permitía eludir la suspensión para
adquirir nuevos compromisos.

Ahora accept/counter_offer y conversión a reserva revalidan partner active, permiso
de reservas, producto active/habilitado y product_partner active/coherente. Se toman
locks SHARE sobre esas filas hasta el fin de la transacción, impidiendo una baja
concurrente durante la comprobación/mutación. Aplica también a STAFF; la autorización
no salta reglas de negocio. No se permite convertir una simulación pendiente en
una reserva real pasando por alto la habilitación comercial.

Rechazar una candidatura sigue permitido; tampoco se bloquea reportar deuda por
estar suspendido. La identidad disabled/locked sí pierde toda autenticación conforme
al diseño existente. No se cancelan automáticamente reservas históricas.

### Medium — M1, corregida

La búsqueda semántica recuperaba entidades por id sin status=active, exponiendo fichas
retiradas que conservasen embeddings. Se filtra el estado en la recuperación real
para las cuatro categorías, con pruebas positivas/negativas por categoría. No se
reconstruyen ni alteran embeddings reales y la búsqueda continúa siendo pública.

### Medium — M2, corregida en el entorno de pruebas

Los tests de bootstrap exigían cero admins en la base compartida. Ejecutarlos tras
el alta real fallaba y generaba una presión peligrosa para resetear cuentas.
Ahora toda prueba de identidad usa tablas TEMP vacías users/auth_sessions/auth_login_limits,
con índices/checks del esquema real, FK de sesión y triggers users copiados de su
metadato. No se copia ningún usuario/hash/token real; conexiones de API se redirigen
a esa misma transacción con savepoints. El bootstrap comprueba su lock en pg_locks,
no intenta bloquear public.users ni acceder desde otra conexión a tablas TEMP.
Las FKs de users hacia travelers/partners se auditan en el esquema real; no se copian
a TEMP (PostgreSQL no permite FK de temporal a permanente). Estas pruebas no
sustituyen un ensayo de carga con varias conexiones en una base de pruebas dedicada.

### Medium — pendientes no bloqueantes para construir Partners

- Separación futura de operator/admin financiero: permisos actuales explícitos, no bypass.
- partners.status=suspended no registra causa: verificación de deuda puede reactivar
  cualquier suspended sin deuda. Antes de agregar suspensión administrativa por fraude
  u otra causa en Partners/Admin, representar motivo y restringir la reactivación.
  Actualmente no existe ese endpoint administrativo; no se inventó una migración.
- financial_events cubre refunds/comisiones/cierres, pero no todas las creaciones y
  confirmaciones de pagos; ampliar auditoría de actor en la fase administrativa.
- received_by es una declaración acotada del cliente al crear el pago. No permite
  confirmar como H4U/procesador: endpoints actuales solo confirman cash/partner.
  En Pagos deberá derivarse de la configuración comercial/webhook verificado.
- Rate limiting público, límites de cuerpo/consulta, retención de logs y políticas
  de PII requieren configuración de despliegue. La búsqueda registra el texto de
  consulta y los errores DB pueden contener contexto: revisar antes de producción.

### Low

Advertencia preexistente urllib3/LibreSSL del Python local; renovar runtime antes
de producción. Documentación/OpenAPI públicos describen contratos, no secretos.

## Integridad, estados y finanzas

Auditoría read-only reproducible: `python -m scripts.audit_post_auth`. Reutiliza
comprobaciones financieras y de identidad, excluyendo incluso el chequeo de formato
de password_hash para no leer hashes reales. Añade coherencia session/request,
candidate/product_partner, reservation/request, commissions/payment/partner,
settlement/commission y eventos huérfanos. Solo imprime conteos/metadatos/checksums.

Se verificaron FKs de identidad y de la cadena comercial, checks e índices únicos;
25 comprobaciones devolvieron cero inconsistencias. No se requieren migraciones.
003/004 mantienen checksums. financial_events es una referencia polimórfica sin FK
única a entidad; se comprueba procedencia por tipo y no existe endpoint de escritura
arbitraria. Las tablas de ajustes y eventos conservan triggers append-only.

Refunds: lock de partner/pago, suma procesada limitada al pago, clave+fingerprint,
actor real y resultado persistido. Comisiones históricas no se borran; reversión
prorrateada acumulada y créditos sobre siguientes cierres mantienen trazabilidad.
Liquidaciones: partner primero, vínculo único de comisiones, neto verificado,
reporte no equivale a verificación y paid no acepta nuevos reportes. No se cambiaron
fórmulas, importes, políticas de cash ni estados ya existentes.

Revocación: firma/claims estrictos, usuario activo, token_version y sesión vigente.
Las pruebas existentes cubren disabled/locked, cambio de rol/propietario/contraseña,
logout y logout-all. Nueva integración confirma que un JWT revocado no puede refundear.
Una solicitud ya autorizada puede terminar después de revocación: límite conocido,
no se cambia toda la arquitectura para mantener un lock de identidad en cada negocio.

## Pruebas y resultado

Se agregaron pruebas HTTP con JWT reales de test para tourist A/B, partner A/B,
prohibición financiera, actor de auditoría e idempotencia de STAFF, candidaturas y
reservas obsoletas, pago cancelado/reembolsado, cierre pagado, rechazo/reporte de
partner suspendido y fichas semánticas retiradas. Credenciales efímeras únicamente.
Se conservan pruebas previas de acceso sin token, headers falsos, IDOR, transacciones,
ajustes y estados. El proceso global de vencimientos se prueba sobre tablas TEMP.

Validaciones específicas completadas:
- 207 passed in 28.47s: seguridad/identidad/bootstrap/autorización y regresión comercial/financiera.
- 37 passed in 10.02s: post-auth y contrato embeddings durante el bloque semántico.
- 36 passed in 10.35s: módulo post-auth final completo (36 casos nuevos).
- py_compile de 8 archivos, import de FastAPI/OpenAPI y git diff --check correctos.
- Suite completa final, ejecutada una vez: **240 passed, 1 warning in 45.86s**.
  Cero fallos; advertencia conocida urllib3/LibreSSL. La primera adaptación
del test de lock falló por invisibilidad de TEMP entre conexiones (127 passed/1 failed);
se corrigió sin relajar la protección productiva ni acceder a users real. Tres casos
posteriores fallaron por un código de fixture de 32 caracteres frente al límite
varchar(30); se corrigió la fixture a 24 y el módulo completo volvió a pasar.

## Pendientes para Partners

- Alta y vinculación de cuentas partner con propiedad derivada de DB; sin IDs confiados.
- Lecturas de candidaturas/reservas/cierres filtradas por actor desde SQL y paginadas.
- Actualizaciones administrativas con locks compatibles, motivos de suspensión y auditoría.
- Mantener posibilidad de saldar deuda sin permitir nuevos negocios estando suspendido.
- Tests de dos partners reales de fixture para cada nueva ruta; no usar datos reales.

## Pendientes para H4U Admin

- Definir permisos financieros de operadores y eventual doble aprobación.
- Provisión/recuperación de usuarios autorizada, versionado y revocación existentes.
- Auditoría completa de cambios, datos personales, retención y motivos de suspensión.
- Base de pruebas dedicada para estrés/concurrencia y despliegue TLS/proxy seguro.

## Conclusión técnica

Decisión final: **READY_FOR_PARTNERS**; suite completa y auditoría SQL correctas. No se identifican vulnerabilidades críticas/altas abiertas que impidan construir
Partners. No equivale a certificar preparación para producción o 1.000 concurrentes.
