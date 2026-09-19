# Endurecimiento comercial H4U

## Decisiones aprobadas

El usuario confirmó prorratear comisiones fijas y porcentuales sobre su importe
histórico y compensar créditos solo hasta cero, arrastrando el sobrante. No se
crean settlements negativos ni transferencias automáticas de H4U al partner.

## Autorización e identidad

`app/auth.py` define Principal inmutable, IdentityProvider asíncrono, políticas
por operación, dependencia FastAPI y comprobación de propiedad. No hay usuarios,
contraseñas, claves nuevas, OAuth ni secretos compartidos de desarrollo.

El punto de conexión es `app.state.identity_provider`, un adaptador de servidor
con `async authenticate(token: str) -> Optional[Principal]`. Debe validar la
credencial real, expiración, revocación y mapear identidad, rol y UUID internos
contra una fuente confiable. Principal exige traveler_id para tourist y partner_id
para partner. El adaptador devuelve None para una credencial inválida. Debe tratar
los fallos de infraestructura como errores, nunca como una identidad por defecto.

Sin adaptador o sin Bearer: 401. Rol o propietario incorrecto: 403. No se aceptan
X-Role, X-Partner-Id, claims sin verificar ni identidades declaradas en el body.
OpenAPI documenta HTTPBearer en todas las operaciones comerciales.

| Operación | tourist | partner | operator/admin |
|---|---|---|---|
| Crear solicitud | sesión propia | No | Sí |
| Simulación | No | No | Sí |
| Responder candidatura | No | candidatura propia | Sí |
| Crear/cancelar reserva | solicitud/reserva propia | asignación/reserva propia | Sí |
| Registrar pasajeros / crear pago | reserva propia | reserva propia | Sí |
| Confirmación del partner | No | pago de reserva propia | Sí |
| Confirmación del turista | pago propio | No | Sí |
| Refund / comisión / crear cierre | No | No | Sí |
| Reportar pago de cierre | No | cierre propio | Sí |
| Verificar cierre / procesar vencimientos | No | No | Sí |

La propiedad se consulta en PostgreSQL, no se confía en UUID enviados por el
cliente. Se vuelve a comprobar dentro de la transacción después de bloquear el
recurso; un cambio entre la dependencia y la mutación no autoriza un recurso ajeno.
Los catálogos y health permanecen públicos. Operator y admin tienen los mismos
permisos comerciales por ahora; las políticas permiten separarlos después.

Los handlers Python también actúan como servicios internos. Sus llamadas directas
son un límite de confianza interno (no autentican un usuario HTTP). La auditoría
las identifica como internal-service/system. Las llamadas HTTP registran el
subject y rol verificados usando contexto aislado por solicitud. Los overrides
son exclusivamente de tests, se restauran al terminar y no se instalan al arrancar.

Pendiente para identidad real: elegir fuente de usuarios/sesiones, credenciales,
expiración/revocación, alta y vinculación de partners/travelers, y recuperación de
cuentas. Hasta conectar ese adaptador el backend **deniega todas las operaciones
comerciales HTTP**; no hay modo público de compatibilidad.

## Modelo financiero

Migración aditiva 002, aplicada sin borrar registros ni modificar importes históricos:

- refunds: idempotency_key, request_fingerprint, result, actor_subject, actor_role.
- partner_settlements: reported_amount (importe del reporte que se está verificando).
- commission_adjustments: un crédito de reversión por refund, FK a comisión y refund,
  importe, policy_version, actor y fecha. Puede ser cero por redondeo.
- settlement_adjustments: aplicaciones parciales de un crédito a cierres; un crédito
  puede repartirse en varios cierres, sin consumir más de su importe.
- financial_events: eventos append-only y snapshots de reportes/cierres para auditoría.
- schema_migrations: versión y checksum del script ejecutado.

Triggers rechazan UPDATE/DELETE de las tres tablas de libro. Validan que refund y
comisión correspondan al mismo payment/moneda, que la reversión no supere la comisión,
y que una aplicación pertenezca al mismo partner/moneda, con crédito disponible.
No se modifican commission_amount ni los importes brutos de settlement_commissions.
No hay migración automática al iniciar la aplicación.

## Política exacta de reembolsos

Para un pago original P, comisión histórica C y suma acumulada de refunds procesados R:

```
reversión_acumulada = ROUND_HALF_UP(C × R / P, 2 decimales)
nuevo_ajuste = reversión_acumulada − suma_de_ajustes_anteriores
```

Se usa Decimal. El refund total revierte exactamente C. Calcular el objetivo
acumulado evita perder céntimos entre muchos refunds parciales. No se vuelve a
consultar la tarifa comercial actual para calcular la reversión.

Se conserva el pago original y cada refund. Se bloquea primero partner y después
payment; se valida estado paid/partially_refunded, importe finito/positivo y suma
procesada <= P. Refund, ajuste, aplicación de crédito, estado agregado del pago y
eventos se escriben en una sola transacción. Cualquier fallo revierte todo.

Si aún no existe comisión: se registra el refund sin inventar una comisión.
La creación de una comisión nueva conserva su regla previa: solo desde paid.
Una comisión ya creada sí puede consultarse idempotentemente después de refunds.
Comisiones disputed/cancelled/pending y refunds históricos con ajustes ausentes
producen 409 para conciliación; no se repara historia silenciosamente.

Si la comisión está earned:

- Aún no está en un cierre: el crédito queda pendiente y se aplica al crear el cierre.
- Está en un cierre impagado: se agrega partida de crédito y se reduce su saldo neto,
  conservando partidas brutas y snapshot. Cierres disputed/cancelled no se ajustan
  automáticamente: devuelven 409 sin procesar el refund.
- Si ya había un pago reportado y cambia el saldo, se conserva reported_amount.
  Verificar devuelve 409 hasta que se presente un reporte conciliado. El snapshot
  conserva referencia/comprobante e importe previos; no se valida el reporte viejo
  como si correspondiera al importe nuevo.

Si la comisión está settled: el cierre pagado permanece intacto. El crédito queda
pendiente para un cierre posterior del mismo partner/moneda. Los créditos se aplican
por fecha/id, hasta el bruto del nuevo cierre; cualquier resto sigue pendiente.

```
saldo_neto_cierre = suma(partidas_brutas) − suma(créditos_aplicados)
saldo_neto_cierre >= 0
```

Un cierre neto cero puede ser verificado por operator/admin sin inventar un pago;
la respuesta indica settled_without_transfer=true. Si tenía un reporte de importe
distinto, exige conciliación incluso si ahora queda en cero. No se realiza ninguna
transferencia a partners. Un crédito sin nuevas comisiones permanece pendiente.
Los campos paid/paid_at del cierre siguen representando obligación saldada para
compatibilidad; en saldo cero no representan una transferencia de dinero.

Al crear/verificar se valida la ecuación del cierre. Las operaciones financieras
serializan el saldo por partner. Esta granularidad prioriza integridad; partners
distintos pueden progresar independientemente.

## Idempotencia

POST /refunds admite `idempotency_key` en JSON (máximo 200 caracteres). En HTTP
se exige esa clave o external_reference. La clave se limita al payment; un replay
con los mismos datos devuelve la respuesta original almacenada, incluso después
de refund total. Misma clave con importe/motivo/método/referencia diferentes: 409.
Índice único en PostgreSQL y bloqueo del payment evitan doble registro.

Por compatibilidad, external_reference sin idempotency_key mantiene rechazo 409
al repetirse dentro del mismo pago. No se pretende deduplicar referencias entre
pagos/proveedores. Las llamadas Python internas sin clave siguen permitidas y no
ofrecen replay; los clientes HTTP deben enviar una clave estable por operación.

Creación de comisión y de cierre conserva already_existed; verificación de cierre
conserva already_verified. Repetir operaciones no vuelve a aplicar créditos.

## Embeddings

Contrato único en app/embeddings.py: modelo
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, 384 dimensiones.
Lo comparten API, generación, rebuild y precarga del Dockerfile. La carga sigue
siendo diferida en la API; vector_to_pg rechaza tamaño incorrecto, NaN e infinito.
Search filtra por el modelo configurado para no comparar espacios diferentes.

Se compararon los 267 vectores existentes con ambos modelos, en solo lectura:

| Modelo | Vectores coincidentes | Error absoluto máximo |
|---|---:|---:|
| all-MiniLM-L6-v2 | 0 / 267 | 0.22722065448760986 |
| paraphrase-multilingual-MiniLM-L12-v2 | 267 / 267 | 1.1920928955078125e-07 |

Columna PostgreSQL: vector(384). Se corrigió únicamente embedding_model en las
267 filas, tras volver a validar todos los vectores y proteger cambios concurrentes.
**Vectores reconstruidos: 0.** No se modificó content ni se ejecutó el generador.

repair_embedding_metadata es dry-run salvo --apply. rebuild_embeddings también
requiere --apply para reconstruir y comprueba antes el contrato de almacenamiento.
No se ejecutó ninguna reconstrucción masiva ni se borraron embeddings.

## Límites y operación

No se integró un procesador de pagos: refund processed es un registro financiero
operado por personal autorizado, no prueba automática de devolución bancaria.
La conciliación externa y comprobantes continúan siendo responsabilidad operativa.
No se agregaron dependencias ni se cambiaron credenciales.

La política existente que reactiva partners suspended al saldar deuda aún no
distingue suspensiones administrativas; requiere modelar motivo antes de usarla
como control de cumplimiento. No se amplió a OAuth, frontend ni administración de
usuarios. No se desplegó ni reconstruyó el contenedor API existente.

Verificación final y salida Git se entregan junto con este documento. Las pruebas
comerciales usan rollback y no dejan registros financieros. La aplicación del
esquema y la reparación probada de metadatos son los únicos cambios persistentes
de PostgreSQL realizados en esta fase.

## Verificación de entrega

- Suite final: `.venv/bin/python -m pytest -q` → **113 passed, 1 warning in 22.19s**.
- Se conservaron los 57 casos anteriores, adaptando únicamente los tests HTTP de
  validación para proporcionar un actor de pruebas explícito; se añadieron 56:
  36 de autorización, 15 financieros y 5 de contrato de embeddings.
- Advertencia existente: urllib3 sobre LibreSSL 2.8.3 del Python 3.9 local.
- 13 consultas SQL de integridad: todos los resultados 0. Modelo declarado único:
  paraphrase-multilingual-MiniLM-L12-v2, 267 filas.
- AST, imports de app, compilación de app/scripts/tests/pipelines/ingestion y ciclo
  de vida FastAPI con TestClient: correctos.
- OpenAPI: 25 rutas, 14 POST comerciales con HTTPBearer; catálogos públicos.
- `pip check`: No broken requirements found.
- `git diff --check`: sin errores.
- Segunda ejecución de migración: Migration already applied; checksum verified.
- No se ejecutó una prueba de carga con varias conexiones concurrentes ni un
  despliegue/build de Docker. La protección se apoya en locks transaccionales,
  índices/triggers y pruebas de reintento, fallo y rollback, no en una certificación
  de rendimiento o estrés.
- No se hizo commit, push ni cambios en credenciales. Los cambios previos permanecen.
