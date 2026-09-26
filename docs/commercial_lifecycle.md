# Ciclo comercial H4U

## Decisión de producto y alcance

Happy path automático mediante configuración, revisión manual solo ante excepciones.
No se han configurado porcentajes ni plazos económicos en h4u. Sin política, regla
aplicable, horario verificable o conciliación suficiente: `requires_manual_review`.
Las políticas de los tests y demo son datos ficticios, no valores de producción.

Base anterior: 50d59ce. Migraciones 001–007 intactas. 008 se aplicó en h4u y en el
contenedor independiente h4u-demo-postgres después de dry-run y rollback verificado.
No se modificaron registros comerciales históricos de h4u.

## Matriz de auditoría inicial y resultado

| Operación | Base anterior | Resultado de esta fase | Riesgo/límite |
|---|---|---|---|
| Solicitud/asignación | Implementada | Cancelación auditada y expiración | Una solicitud con reserva se resuelve por la reserva |
| Contraoferta | Implementada con versión | Rechaza plazos vencidos antes del worker | Sin TTL configurado no se inventa vencimiento |
| Reserva/pasajeros | Parcial | Capacidad atómica y comprobación de vencimiento | Registro histórico incompleto requiere revisión |
| Pago | Implementado cash | Confirmación serializada con cancelación | Proveedor real todavía no integrado |
| Refund | Implementado | Motor reutilizado dentro de transacción; comandos externos pendientes | Un comando no prueba devolución efectiva |
| Comisión/settlement | Implementados | Races reales probadas; ajustes históricos conservados | Comisiones manuales legacy siguen permitidas por dominio |
| Cancelación | Solo reserva, bloqueaba pago cobrado | Motor configurable, snapshot, replay, resolución admin | Sin política no cancela automáticamente |
| Expiraciones | Campo de solicitud sin job | Job persistente e idempotente para cuatro entidades | Reportes/evidencia de cobro no se descartan automáticamente |
| Disponibilidad/capacidad | Producto/partner activos; slots sin uso | Salidas explícitas; cantidad de pasajeros consume cupos | No inferir total/restante de available_slots legacy |
| No-show/completion | Estados sin operación | Operación con evidencia y fecha verificable | El paso del tiempo no prueba asistencia |
| Outbox | Seis tipos de evento | Cancelación/expiración/refund/no-show/completion | Transporte depende de worker y configuración de plantillas |
| Autorización | Implementada | Nuevas capacidades explícitas y ownership revalidado | Operator no configura economía ni confirma refunds |

## Estados y transiciones

Los estados adicionales presentes en el esquema (por ejemplo disputed) no implican
que exista un endpoint público para forzarlos. No hay endpoint genérico de cambio
arbitrario de estado. Los servicios Python son un límite interno de confianza;
HTTP siempre autentica y aplica políticas.

| Entidad / origen → destino | Actor | Condiciones y efectos | Repetición |
|---|---|---|---|
| Solicitud nueva → searching/estado inicial existente | Tourist propio, operator/admin | Sesión/producto válidos, candidatos; deadline solo configurado | Nueva solicitud independiente |
| Solicitud searching → offers_received | Partner miembro, operator/admin | Contraoferta válida y no vencida | Reglas existentes |
| Solicitud searching/offers_received → partner_assigned | Partner miembro/staff; turista acepta su contraoferta | Candidato aceptado, ganador único, otros lost | Conflicto tras asignación |
| Solicitud no terminal sin reserva → cancelled | Tourist propio, partner asignado efectivo, operator/admin | Motivo, cancela candidatos, evento auditado/outbox | Devuelve estado ya aplicado |
| Solicitud abierta sin reserva → expired | Job interno | expires_at vencido; candidatos expiran | Sin segundo efecto |
| Candidato sent/delivered/viewed/counter_offered → rejected/counter_offered/accepted | Partner miembro/staff; aceptación de oferta por turista propio | Locks, propiedad, elegibilidad; turista usa versión | Estados incompatibles 409 |
| Candidato pendiente sin ganador → expired | Job | Deadline configurado vencido | Sin segundo efecto |
| Solicitud asignada → reserva awaiting_passenger_data | Actores con propiedad/capacidad existente | Una reserva por solicitud, producto/partner activos, fecha válida, cupos | Duplicado 409 |
| Reserva awaiting_passenger_data → payment_pending/confirmed | Propietario autorizado/staff | Cantidad/documentos de pasajeros; requiere pago o no | Validaciones existentes |
| Pago pendiente + confirmaciones → paid; reserva payment_pending → confirmed | Partner efectivo + tourist propio; admin autorizado | Cash, fechas válidas, locks reserva/pago, doble confirmación | Confirmación final repetida 409 sin segundo cobro |
| Reserva abierta → cancelled | Solicitante autorizado y política aplicable | Snapshot, motivo, cancelación de pagos pendientes, libera cupos; comando refund si corresponde | Misma clave/contenido devuelve resultado original |
| Reserva abierta → sin cambio + requires_manual_review/denied | Solicitante autorizado | Falta política/evidencia o regla explícita | Resultado original inmutable por clave |
| Caso requires_manual_review → nuevo caso de resolución | Admin | Versión de política explícita para ese caso; conserva el original | Misma clave devuelve resultado original |
| Reserva pendiente → expired | Job | Deadline y ausencia de dinero/reportes/confirmaciones que exijan conciliación; libera cupos | Sin segundo efecto |
| Pago pending sin confirmaciones → expired | Job | Deadline vencido | Sin segundo efecto |
| Reserva confirmed/ready → no_show/completed | Partner efectivo, operator/admin | Hora de servicio alcanzada y motivo/evidencia | Mismo destino devuelve already_applied |
| Refund nuevo → processed | Admin; confirmación interna de comando | Saldo, clave/referencia, confirmación de ejecución cuando es comando | Replay exacto; incompatibilidad 409 |
| Pago paid/partially_refunded → partially_refunded/refunded | Servicio de refund | Refund acumulado ≤ pago; ajustes en la misma transacción | Sin doble efecto |
| Comisión nueva → earned | Admin | Pago paid y regla comercial existente | Devuelve comisión existente |
| Settlement nuevo → pending_payment | Admin | Partidas no asignadas, crédito hasta cero | Devuelve existente según reglas actuales |
| Settlement abierto → waiting_verification | Partner propietario/manager o admin | Reporte de importe y método | Estado incompatible 409 |
| Settlement waiting_verification → paid; comisión earned → settled | Admin | Balance y reporte conciliados | already_verified |
| Settlement pendiente vencido → overdue | Admin/job administrativo existente | Fecha de deuda; reglas de suspensión existentes | Sin doble efecto |

Reservas cancelled, expired, completed y no_show no pueden reabrirse (trigger SQL).
Refunded describe el pago, no elimina la reserva. Solicitud y reserva tienen estados
separados: no se reescribe retrospectivamente la asignación de una solicitud.

## Políticas versionadas

`cancellation_policy_versions` es append-only. Modificar una política significa
crear otra versión y cambiar su asignación. Se admite asignación por producto o
producto/partner; la activa específica tiene prioridad sobre la general activa.
Desactivar la específica permite usar la general; desactivar ambas produce revisión.

Cada regla declara actores, mínimo de anticipación en segundos, resultado
(cancel/deny/requires_manual_review), refund (none/percentage/fixed), valor y moneda
para importes fijos. No hay defaults económicos. Entre reglas aplicables gana el
mayor mínimo de anticipación; empates por actor son configuración inválida. Después
del inicio del servicio, sin regla aplicable se solicita revisión.

La devolución se calcula sobre el pago original confirmado. Se restan refunds
previos; un monto fijo se limita al pago. Se usa Decimal con redondeo monetario.
Una regla no reembolsable es explícitamente none/0, distinta de ausencia de política.
Los casos guardan versión, snapshot, resultado, actor, motivo y fecha. El replay no
recalcula con reglas nuevas. Administración puede consultar y resolver un caso
mediante una versión explícita, sin cambiar asignaciones globales ni borrar el caso.

## Contabilidad y ejecución de dinero

Una cancelación reembolsable crea `refund_commands` pendiente, sin marcar dinero
como devuelto. Su monto reserva saldo frente a otros refunds. Una confirmación
administrativa de ejecución efectiva o una confirmación fake dentro de demo ejecuta
el mismo servicio contable existente, bloqueando partner → payment → command.
Replay con referencia/tipo distintos produce 409.

El pago original, comisión original y partidas históricas permanecen. El crédito
por refund prorratea porcentajes y fijos sobre la comisión histórica, con redondeo
acumulado. Reduce cierres aún abiertos o queda disponible para próximos cierres si
la comisión ya está liquidada. El cierre nunca se hace negativo; sobra crédito para
compensar más adelante, sin transferencia automática de H4U al partner.

`financial_events` también registra nuevas transiciones sensibles para no duplicar
infraestructura de auditoría. No se utiliza para almacenar credenciales.

Proveedor real pendiente de integración: debe enviar command UUID como clave
estable, reconciliar respuestas ambiguas y verificar callbacks antes de confirmar
contabilidad. No hay webhook financiero público ni adaptador real habilitado.
`scripts.process_refund_commands --demo` solo confirma mediante fake en h4u_demo;
sin --demo rechaza ejecutar. La frontera de confirmación ya está separada de la
cancelación y de la contabilidad. La integración real requiere adaptador y validación
de firma del proveedor, no una reescritura del motor comercial.

## Expiraciones y evidencia

`commercial_product_settings` permite configurar TTL independiente para solicitud,
oferta, reserva y pago. Los triggers capturan el deadline al crear cada fila. Cambiar
la configuración no refecha registros existentes. NULL no impone vencimiento.
El job procesa lotes con transacciones por entidad y revalida bajo locks.

```
python -m scripts.process_expirations --limit 100
python -m scripts.process_expirations --demo --limit 100
```

Hay que programar el comando en el scheduler del entorno; no se instaló un servicio
global en la máquina del usuario. No se ejecutó el job global sobre datos reales.
Dinero cobrado, disputas, reportes y confirmaciones parciales requieren conciliación
antes de liberar reservas. No-show/completion requieren evidencia explícita; la hora
solo establece elegibilidad de evaluación, no demuestra que hubo/no hubo servicio.

## Disponibilidad, capacidad y concurrencia

Investigación: tour_schedules tiene cero filas en h4u; available_slots no tiene
consumidores de backend ni documentación que pruebe total/restante. Se conserva
como legacy, sin reinterpretación ni doble descuento.

`commercial_slots` define capacidad total por producto/partner/fecha/hora. Productos
sin slots conservan ausencia de inventario explícito. `scheduled_only=true` exige
una salida coincidente. Además, si ese producto/partner ya tiene slots, omitir la
hora o elegir una salida inexistente no permite eludir el inventario. Un slot
inactivo rechaza reservas; pasajeros consumen cupos.
No se permite introducir capacidad retrospectiva sobre reservas activas sin slot.

La creación bloquea partner, solicitud y slot en la transacción. El trigger SQL
actualiza reserved atómicamente con límite capacity; cancelar/expirar resta una vez.
No-show/completed conservan consumo histórico. Cambiar slot, pasajeros o fecha de
una reserva persistida está bloqueado para evitar movimientos no auditados.

Pruebas en h4u_demo con dos conexiones independientes y barrera de inicio cubren:
último cupo, misma solicitud, mismo pago, confirmaciones simultáneas, exceso de
refunds, comisión duplicada, settlement duplicado, refund contra settlement y
cancelación contra confirmación. No se crean fixtures persistentes en h4u.

## Notificaciones

Triggers persistentes crean eventos de solicitud cancelada/expirada, reserva
expirada/no-show/completed y refund procesado. Se reutiliza el evento previo de
reserva cancelada. El event_key único evita notificaciones duplicadas por replay.
Outbox se envía fuera de la transacción comercial; un error de Meta no revierte el
negocio. Hay que configurar plantillas para los nuevos tipos de evento.
La demo usa FakeProvider y su protección contra Meta continúa activa.

## Demo y reconstrucción

Los escenarios accept/counter_offer siguen disponibles. `--exercise-lifecycle`
añade política ficticia, cancelación turística después del settlement, replay,
confirmación fake del refund y entrega WhatsApp fake de los eventos. No cambia las
reglas comerciales respecto de h4u.

```
python -m scripts.run_demo --scenario accept --exercise-lifecycle
python -m scripts.audit_commercial_lifecycle --demo
python -m scripts.audit_demo
```

setup_demo reconoce 008 como actualización incremental revisada. Verifica marcador,
checksums, dry-run y rollback y aplica el mismo SQL; conserva historial. El esquema
nuevo también se incluye en reconstrucción desde el schema de h4u. No se ejecutó
rebuild destructivo en esta fase. Fixtures de concurrencia quedan identificados con
código RACE y nombre Concurrency DEMO y se conservan, sin usuarios reales.

## Auditoría y hallazgo histórico pendiente

`audit_commercial_lifecycle` consulta conteos en READ ONLY y sale con error si detecta
inconsistencias. Detecta duplicados, procedencia, saldos, capacidad, comandos de refund,
reservas confirmadas sin pasajeros/pago requerido y eventos financieros huérfanos.

En h4u existe una reserva confirmed para 1 pasajero con 0 pasajeros registrados;
no tiene pago confirmado y su producto no exige pago. Se conserva intacta y se reporta
como `confirmed_passenger_mismatch=1`: necesita aclaración/conciliación humana antes
de declarar READY_FOR_DATA_PHASE. No se inventa un pasajero ni se cambia su estado.
Dos comisiones manuales históricas sin payment_id son válidas según trigger_event;
no son dos comisiones del mismo pago. El auditor distingue ese caso de una comisión
automática sin pago, que sí es inconsistencia.


## Validación de cierre

- Suite final: **401 passed, 1 warning in 102.08s (0:01:42)**.
- La primera suite había aprobado 400 tests; se repitió por una corrección real
  posterior: impedir eludir inventario explícito omitiendo/cambiando horario.
  Esa corrección aprobó antes 22 pruebas relacionadas.
- Regresión por bloques: 193 pruebas aprobadas; las validaciones posteriores de
  autorización/lifecycle y demo/concurrencia aprobaron 56 y 25 respectivamente.
  Son conjuntos solapados, no deben sumarse como tests distintos.
- 99 archivos compilados con py_compile; FastAPI lifespan, health y OpenAPI OK.
- Checksums de 002–008 verificados contra ambos registros; 001–007 sin diff.
- h4u: 25 controles, uno con hallazgo de pasajeros; auditor devuelve 1.
- h4u_demo: 25 controles comerciales y 26 de aislamiento, todos en cero.
- git diff --check OK. Sin commit ni push.

**BLOCKED_FOR_DATA_PHASE** hasta conciliar el registro histórico indicado. Ningún
control fue deshabilitado para obtener una auditoría verde. El warning conocido
urllib3/LibreSSL permanece y no es un fallo de tests.
