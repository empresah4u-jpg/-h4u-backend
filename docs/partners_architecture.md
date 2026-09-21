# Partners H4U: arquitectura y contrato

## Decisiones sobre el modelo existente

`partners` es el negocio y contraparte comercial. No se creó otra tabla de negocios.
Tiene identidad comercial, configuración de comisión/cobro, status y permiso de reservas.
`partner_entities` lo vincula a hoteles, restaurantes, operadores turísticos, proveedores
de transporte y servicios del catálogo. `product_partners` vincula los productos que
puede prestar, con precio/moneda y configuración particular. Las solicitudes,
candidaturas, reservas, pagos, comisiones y cierres conservan sus relaciones actuales.

`users` representa personas con credenciales propias. Un negocio admite varias personas
y una persona puede pertenecer a varios negocios. No se comparten contraseñas. Los roles
globales siguen siendo tourist, partner, operator, admin. **operator es personal H4U**;
no representa a un empleado del negocio. owner/manager/staff son roles de membresía,
siempre subordinados a un usuario global partner, nunca roles administrativos H4U.

## Migración 005 y compatibilidad gradual

`005_partner_memberships.sql` agrega:

- `partner_memberships`: UUID, partner_id/user_id con FKs, membership_role, status,
  created_at/updated_at, UNIQUE(partner_id,user_id) e índice de membresías activas.
- `partner_events`: historial append-only de membresías/estado comercial, actor,
  valores anteriores/nuevos y timestamps. Sin contraseñas, hashes, emails ni tokens.
- `partners.suspension_source`: administrative/debt/legacy o NULL.
- Triggers de validación, conservación y auditoría de membresías/estados.

Se conserva users.partner_id y su FK/check como **ancla legacy/transición**, no como
permiso. El único elemento retirado es el índice único `uq_users_partner`, sustituido
por `idx_users_partner` no único: la unicidad impedía múltiples personas por negocio.
No se borran columnas, tablas, usuarios ni registros financieros. Esta relajación de
índice es necesaria para el requisito multiusuario; el resto de cambios es aditivo.

El backfill asigna owner al único usuario legacy de cada partner: active si su cuenta
está activa, suspended en caso contrario. Conserva identidades y sesiones. Las
suspensiones previas sin causa se identifican como legacy. En la base inspeccionada
había un admin, cero usuarios partner y cero partners suspendidos: no se modificó la
cuenta real ni se crearon membresías reales. El backfill se probó con un usuario
aleatorio dentro de un dry-run con rollback antes de aplicar la migración.

No hay trigger que conceda membresía por escribir users.partner_id después del backfill.
Los nuevos usuarios partner necesitan alta de identidad confiable con un ancla inicial
y una llamada administrativa explícita al servicio de membresías. La futura API Admin
podrá coordinar esa provisión; no existe registro público ni auto-vinculación.
`/auth/me.partner_id` conserva el contrato legacy; el Portal debe usar `/partners`
para obtener negocios autorizados, no asumir que ese campo concede acceso.

Ejecutor explícito, nunca durante el arranque:

```sh
.venv/bin/python -m scripts.apply_partner_memberships
.venv/bin/python -m scripts.audit_partners
```

El ejecutor usa transacción, advisory lock, timeouts y checksums de 002/003/004
(y 001 si está registrada). Repetirlo verifica 005 sin reaplicarla. No se editó 001–004.
005 ya está aplicada: **no modificar su contenido**. Cambios posteriores requieren 006.

No hay downgrade automático destructivo: restaurar la antigua unicidad sería imposible
si ya existen varios usuarios por negocio, y borrar memberships/events perdería auditoría.
Para revertir el comportamiento, preparar una migración posterior tras comprobar los
datos y conservar la historia; no ejecutar DROP de las tablas nuevas.

## Membresías y roles

Una sola fila por par negocio/persona, incluso tras revocación. Suspender, revocar,
reactivar o cambiar rol modifica esa fila y agrega un evento. No se reasigna su
user_id/partner_id ni se permite DELETE. Un cambio idéntico no duplica eventos.

| Capacidad actual | owner | manager | staff |
|---|---|---|---|
| Perfil, membresía propia, productos, solicitudes y reservas | Sí | Sí | Sí |
| Responder candidaturas y operar reservas propias | Sí | Sí | Sí |
| Registrar pasajeros, crear pago y confirmar efectivo como partner | Sí | Sí | Sí |
| Reportar pago de un settlement propio | Sí | Sí | No |
| Refunds, generar comisiones/cierres, verificar cierres, procesar vencimientos | No | No | No |
| Crear/cambiar miembros o estado administrativo del negocio | Solo H4U admin en esta fase | No | No |

owner queda preparado para máxima administración interna futura; manager para gestión
limitada; staff para operación diaria. No se implementaron todavía escrituras de perfil
ni invitaciones/gestión de empleados. Ser owner no permite administrar H4U ni verificar
su propio cierre. Los permisos financieros globales de H4U operator/admin se mantienen
según las políticas anteriores, sin ampliarlos a miembros del negocio.

## Autorización central y ownership

`app/services/partner_memberships.py` contiene require_partner_member/manager/owner.
Consulta desde PostgreSQL usuario global partner activo, membresía activa, rol y estado
del negocio. Toma locks SHARE de partner y membresía durante la operación. El JWT no
contiene ni concede membresías; los claims/headers enviados por cliente no eligen roles.

`app/auth.py` resuelve el partner real del recurso (candidatura, solicitud, reserva,
pago o cierre) y consulta la membresía de la persona para ese negocio. No compara
exclusivamente con el ancla legacy. Así una membresía secundaria autoriza sus propios
recursos sin permitir IDs de negocios ajenos. Antes de bloquear reserva/pago, prelock_partner toma el partner y membresía,
respetando el orden partner → reserva → pago utilizado con las operaciones financieras
y evitando una inversión de locks frente a refunds. El check se repite dentro de la
transacción del handler después de bloquear el recurso. Las lecturas mantienen el check y consulta
en la misma transacción, y filtran por partner_id en SQL.

Sin token o con identidad/sesión inválida: 401. Sin membresía activa, rol insuficiente
o negocio ajeno/inexistente para un partner: 403. Para H4U staff, negocio inexistente:
404. Identificadores mal formados/paginación inválida: 422. Las lecturas H4U operator/admin
son globales; `/membership` expresa membresía propia y exige rol global partner.

Cada listado devuelve `{items, limit, offset}`, con limit 1–100 y orden estable. No se
exponen credenciales, documentos de pasajeros, contactos del turista, fiscalidad del
negocio ni respuestas privadas de otros candidatos. Solo se devuelven los datos
operativos mínimos de los registros vinculados al negocio autorizado.

## Endpoints nuevos

Todos requieren Bearer:

| Método | Ruta | Contenido |
|---|---|---|
| GET | /partners | Negocios de membresías activas; H4U staff ve listado global |
| GET | /partners/{partner_id} | Perfil operativo, estado y membresía propia (null para H4U staff) |
| GET | /partners/{partner_id}/membership | Membresía propia activa |
| GET | /partners/{partner_id}/products | Productos vinculados y estado/precio del vínculo |
| GET | /partners/{partner_id}/requests | Solicitudes con candidatura del negocio y su propia respuesta |
| GET | /partners/{partner_id}/reservations | Reservas asignadas al negocio |

Los listados incluyen historial operativo aunque el producto/reserva ya no esté activo.
Eso no habilita transiciones: los endpoints de escritura conservan sus validaciones.
No se agregaron lecturas de pasajeros ni interfaces financieras nuevas.

## Estados y suspensión

No se añaden estados redundantes. Se reutilizan pending/active/suspended/inactive y
reservations_enabled. inactive sirve para deshabilitación/cierre operativo sin borrar
historial; no se añadió un estado archived sin necesidad.

| Estado del negocio | Lecturas de miembros activos | Nuevas solicitudes/ventas/reservas | Operación de reservas existentes | Reportar deuda owner/manager |
|---|---|---|---|---|
| active, reservations_enabled=true | Sí | Según producto/vínculo y reglas actuales | Según estado comercial | Sí |
| active, reservations_enabled=false | Sí | No | Sí, según estado comercial | Sí |
| pending | Sí | No en flujo real | No para miembros | Sí |
| suspended, debt/legacy/sin causa | Sí | No | Sí, según estado comercial | Sí |
| suspended, administrative | Sí | No | No para miembros | Sí |
| inactive | Sí | No | No para miembros | Sí |

`commercial_eligibility` conserva la decisión sobre admisión de nuevos compromisos:
partner/producto/vínculo activos y habilitados. El servicio de membresía decide quién
puede operar, no duplica esos chequeos. Una suspensión no cancela reservas ni altera
pagos/comisiones existentes. H4U staff mantiene capacidad de resolver operaciones
históricas por sus rutas actuales; la admisión comercial sigue aplicándose a STAFF.

La suspensión por deuda la marca process-overdue como debt; una verificación de pago
solo reactiva un partner si no queda deuda vencida **y suspension_source=debt**.
Nunca levanta administrative, legacy ni una suspensión sin causa. La suspensión manual
creada por el servicio administrativo siempre se marca administrative.

Visibilidad pública: no existe catálogo público de partners. Los catálogos hotel/tour/etc.
son entidades distintas con su propio status. Una suspensión comercial no borra esas
fichas ni cambia su publicación; gestionar esa vinculación editorial queda para Admin.

Membresía suspended/revoked: 403, incluso con JWT anterior todavía válido. Al reactivar
explícitamente una membresía, ese JWT aún vigente vuelve a permitir sus nuevos permisos:
no almacena una copia de los antiguos. Una degradación owner→staff se aplica sin emitir
token nuevo. User disabled/locked y logout/token_version invalidan identidad conforme a
003/004. Una transacción en curso conserva sus locks hasta terminar; la revocación de
membresía espera ese lock. La limitación preexistente de revocación de identidad para
solicitudes ya autorizadas se mantiene.

## Provisión y auditoría

`set_membership(actor, user_id, partner_id, membership_role, status)` verifica en DB que
el actor sea admin H4U activo y que el destino sea un usuario global partner existente.
Hace upsert del par dentro de una transacción. `set_partner_state` valida al mismo
administrador y modifica solo estado comercial/habilitación, sin tocar finanzas.
Son servicios internos para la futura API Admin, no endpoints públicos. El adaptador
HTTP futuro debe pasar el Principal obtenido de get_current_actor, nunca del body.

La auditoría usa el patrón existente de ledger append-only, en una tabla propia de
Partners para no mezclar gestión de personal con historia financiera. Triggers registran
creación, cambio de rol/estado, reactivación y cambios del estado comercial. Actor UUID
del servicio, `migration:005` para backfill, o `database:<rol DB>` para SQL interno
sin contexto. Las operaciones existentes de settlements pasan su actor al contexto de
auditoría. El acceso directo privilegiado a PostgreSQL sigue siendo una frontera confiable;
no equivale a una API pública autorizada y debe restringirse en producción.

No se guarda texto libre sensible en eventos. FKs y triggers conservan procedencia;
auth y negocio se validan también al ejecutar, no solo al provisionar.

## Pruebas, límites y próximas fases

Los fixtures de identidad aíslan users, auth_sessions, auth_login_limits,
partner_memberships y partner_events en tablas TEMP vacías. Copian índices/checks y
triggers, nunca cuentas/hashes/tokens reales. Las entidades comerciales de prueba se
crean dentro de force_rollback. No se usa ni altera el administrador local real.
La auditoría read-only revisa esquema real/FKs/checksums/conteos. Ensayos de estrés con
varias conexiones y una base de pruebas dedicada siguen pendientes.

Para **H4U Admin**: alta/invitación de personas y cuentas partner, endpoint seguro sobre
los servicios internos, política de último owner, edición de perfiles, razones operativas
más detalladas, reactivación manual y segregación financiera de operadores. No hay aún
correo de invitaciones, recuperación de contraseña ni flujo público de incorporación.

Para **Portal Partner**: selección explícita de negocio desde /partners, paginación,
interfaz según membresía, manejo de 401/403/409 y renovación de vista ante revocación.
No interpretar users.partner_id como permiso ni almacenar credenciales compartidas.

No se implementó frontend, WhatsApp, pagos externos ni despliegue. La advertencia local
urllib3/LibreSSL y la preparación TLS/proxy/retención continúan como pendientes de entorno.


## Validación de esta fase

- Dry-run de 005 con rollback: backfill, multiusuario y repetición idempotente correctos.
- 005 aplicada y registrada; repetición posterior verifica checksum sin reaplicar.
- 7 checks de Partners y 25 de identidad/comercio: cero inconsistencias.
- Cuenta real conservada: 1 admin, 1 sesión; no hay usuarios partner ni membresías reales.
- Partners, Auth y Commercial: **219 passed in 26.48s** tras corregir orden de locks.
- Compilación de 14 archivos e import/OpenAPI correctos; seis rutas GET con Bearer.
- Suite completa final, una sola ejecución: **268 passed, 1 warning in 42.09s**.
  Cero fallos; advertencia conocida de urllib3/LibreSSL. Se añadieron 28 casos Partners.
- Resultado: **READY_FOR_H4U_ADMIN**. Sin Critical/High identificados abiertos.
- Sin commit ni push. .env y CURRENT_STATE.local.md ignorados, no tracked/staged.
