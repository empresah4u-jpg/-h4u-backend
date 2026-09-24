# H4U Admin: API administrativa segura

## Alcance y separación de actores

Se parte de 047a93d, con Auth y Partners existentes. Se reutilizan IdentityProvider,
Principal, Argon2id, normalización de email, memberships, commercial_eligibility,
revocación y servicios de estado. No se implementa frontend, WhatsApp, pagos externos,
super_admin ni RBAC configurable.

`admin` y `operator` son personal H4U. `owner/manager/staff` son membresías de un usuario
global partner; ninguna habilita /admin. La identidad viene del JWT y PostgreSQL,
no de headers/body. app/admin_policy.py define capacidades administrativas fijas;
app/auth.py conserva las políticas comerciales y concentra ADMIN_ONLY financiero.

## Matriz y endpoints

Todos requieren Bearer. Colecciones devuelven items/limit/offset; limit 1–100, offset>=0.

| Método | Ruta | admin | operator |
|---|---|---|---|
| GET | /admin/me | Sí | Sí |
| GET | /admin/users | Sí | No |
| GET | /admin/users/{user_id} | Sí | No |
| POST | /admin/users | operator/partner únicamente | No |
| PATCH | /admin/users/{user_id}/status | Usuarios no-admin | No |
| GET | /admin/partners | Sí | Sí |
| GET | /admin/partners/{partner_id} | Sí | Sí |
| POST | /admin/partners | Crea pending | No |
| PATCH | /admin/partners/{partner_id} | Perfil permitido | No |
| PATCH | /admin/partners/{partner_id}/status | Estado/habilitación | No |
| GET | /admin/partners/{partner_id}/members | Sí | No |
| PUT | /admin/partners/{partner_id}/members | Alta/cambio por user_id | No |
| GET | /admin/reservations | Sí | Sí |
| GET | /admin/service-requests | Sí | Sí |

No se agregaron aliases de escrituras comerciales. Se utilizan las rutas existentes.
Operator puede operar solicitudes, candidaturas, reservas, pasajeros y registrar pagos
pendientes conforme a esas políticas. Sus nuevas lecturas no incluyen credenciales,
documentos de pasajeros, contactos de turistas ni campos financieros internos.

Listas users: filtros role/status y email (búsqueda parcial). Partners: status y search
sobre business_name. Reservas y solicitudes: status y partner_id (partner asignado en
solicitudes). SQL parametrizado y columnas explícitas; ningún SELECT * de usuarios.
Los operadores no pueden evadir la política sustituyendo un UUID en /admin/users.

## Provisión de personas

POST /admin/users acepta email, password como SecretStr, role operator/partner,
reason y, para partner, partner_id y membership_role. Se exige el mismo Argon2id y
política de contraseña: 12 caracteres mínimo, 1024 bytes UTF-8 máximo. No se recorta
ni normaliza la contraseña. Email usa validación/normalización compartida.

Operator no admite partner_id ni membership_role explícito. Partner exige negocio
existente y se crea junto a su membresía en la misma transacción. Cualquier error
revierte cuenta, membresía y auditoría. Email duplicado devuelve 409. No se devuelve
contraseña, hash ni JWT; la persona puede iniciar sesión por /auth/login.

La asignación de contraseñas por un administrador es una provisión inicial controlada,
no un sistema de invitaciones ni recuperación. Transportar credenciales solo por TLS,
no incluirlas en comandos, capturas, logs o motivos. Cada persona recibe una identidad
propia; no se comparten contraseñas de negocio. Invitaciones y entrega segura definitiva
quedan para el frontend/proceso de incorporación.

**No se crean admins ni tourists desde el endpoint genérico**. Tampoco hay cambio de
rol global ni edición de estados de cuentas admin, incluida la propia: evita lockout
accidental y preserva el administrador existente. Altas/recuperación de administradores
requieren un procedimiento más restrictivo futuro; el bootstrap no se reutiliza.

## Partners y perfiles

POST /admin/partners crea pending con reservations_enabled=false. A continuación se
provisiona o vincula un owner y se activa mediante PATCH /status. Se conservan
pending/active/suspended/inactive; no se crean estados closed/disabled redundantes.
Inactive representa cierre/deshabilitación operativa sin borrar historial.

Edición de perfil limitada a business_name, legal_name, contact_name, phone, whatsapp,
email y preferred_language. Campos extra se rechazan: no pueden editar IDs, FK,
comisiones, payout, ownership histórico, created_at ni cantidades financieras.

Las mutaciones exigen reason (3–500 caracteres), guardado en auditoría. Suspender por
Admin establece suspension_source=administrative; las suspensiones debt siguen siendo
gestionadas por el motor financiero. La reactivación manual exige owner efectivo y
ninguna deuda vencida. La liquidación automática solo levanta source=debt, sin deuda
restante y con owner efectivo; nunca levanta administrative/legacy. No se borran ni
cancelan automáticamente reservas, pagos, cierres ni membresías.

commercial_eligibility continúa bloqueando nuevos compromisos para negocios no activos
o no habilitados. Las reglas de Partners para lecturas/deuda/operaciones existentes
permanecen vigentes. La publicación de entidades de catálogo es independiente y no se
modifica mediante estas rutas de negocio.

## Membresías y último owner

Se reutiliza set_membership con upsert del par user/partner, validación de rol global
y estados owner/manager/staff, active/suspended/revoked. No hay autovinculación: solo
admin H4U activo verificado desde DB puede ejecutarlo. Las variaciones se aplican a
JWTs existentes por consulta de membresía en cada operación; no se amplían sus claims.

Owner efectivo = membership owner/active y user partner/active. En un negocio activo
no se permite suspender, revocar o degradar a su último owner efectivo (409). Tampoco
puede deshabilitarse/bloquearse su cuenta sin otro owner efectivo. Owner con cuenta
locked/disabled no cuenta como respaldo. Cambios de usuario revisan todos sus negocios.

Todas las mutaciones administrativas y los servicios existentes de membresía/estado
adquieren primero un advisory lock transaccional común `h4u-admin-governance`, antes
de locks de filas. Aislamiento READ COMMITTED requerido; otro aislamiento se rechaza.
Esto serializa decisiones de membresía y cuenta entre conexiones y evita que dos bajas
simultáneas eliminen ambos owners. Es simple y deliberadamente global: limita throughput
administrativo, no las lecturas o todo el motor comercial. El timeout de locks es 5s.

Después se bloquean los partners afectados para coordinar activación/suspensión con
finanzas. Los tests verifican decisiones consecutivas y que una segunda conexión no
puede adquirir el lock mientras la primera decide. No sustituyen pruebas de carga
multiconexión a escala en una base dedicada.

Excepción explícita: un negocio pending/suspended/inactive puede quedar sin owner;
H4U Admin puede gestionarlo y su futura activación vuelve a exigir uno efectivo.
No se fuerza ni reescribe el onboarding histórico: existe **un negocio active legacy
sin owner** en la base inicial. Se reporta como advertencia para vinculación manual,
no se crea una cuenta real ni se suspende el negocio automáticamente. La API no permite
eliminar un último owner existente ni activar nuevos negocios sin owner.

## Usuarios, sesiones y privacidad

PATCH /admin/users/{id}/status admite active/disabled/locked en cuentas no-admin.
Los triggers 003/004 incrementan token_version y revocan sesiones ante cambios. Activar
una cuenta no resucita JWTs anteriores. No hay DELETE ni reset de contraseñas.

Todas las respuestas /admin usan no-store. Errores de validación omiten input/ctx para
no reflejar credenciales. Errores inesperados de Admin registran únicamente el tipo,
sin traceback/diagnóstico SQL que pueda incluir filas sensibles. Los JSON de usuario
son proyecciones explícitas, sin password_hash ni columnas de auth_sessions.

La revocación de una identidad se valida al inicio de una solicitud; una operación ya
autorizada puede terminar. Se conserva ese límite conocido de Auth. No se altera la
arquitectura para mantener el lock de sesión durante toda operación de negocio.

## Auditoría y migración 006

006_admin_audit.sql crea admin_events (actor FK, acción, tipo/UUID del target, timestamps,
old/new values y reason), índices y trigger append-only reutilizando el patrón existente.
No se mezclan estos eventos con financial_events; partner_events sigue registrando su
historia de dominio. La acción administrativa agrega contexto/motivo sin sobrescribirla.

Las escrituras y sus eventos comparten transacción. Fallar la auditoría revierte el
cambio. Un reintento sin cambio no genera otro evento de estado/membresía/perfil.
La provisión no es un endpoint de reintento por clave: email/code únicos devuelven 409.
Los snapshots se construyen con whitelist, nunca copiando requests ni filas completas.
No se guardan contraseñas, hashes, JWT o secretos. Los motivos/perfiles pueden contener
información personal legítima: requieren retención y acceso controlado; no pegar secretos.

006 fue ensayada transaccionalmente, repetida dentro del ensayo, revertida y verificada
antes de aplicar. Registrada con checksum. No se editaron 001–005 ni datos del admin real.
No editar 006 tras aplicación; cambios futuros mediante 007+. No hay downgrade que borre
el ledger administrativo.

```sh
.venv/bin/python -m scripts.apply_admin_audit
.venv/bin/python -m scripts.audit_admin
```

El auditor solo devuelve conteos y checksum; comprueba actores/targets huérfanos,
acciones desconocidas, claves de credenciales, revocación y trigger append-only. No
selecciona valores de hashes/tokens. Los índices polimórficos de target no sustituyen
una FK a múltiples tablas; el auditor comprueba procedencia y no hay endpoint de borrado.

## Política financiera restrictiva

| Acción | H4U admin | H4U operator | Actores de negocio |
|---|---|---|---|
| Crear pago pendiente | Sí | Sí | Propietario según reglas actuales |
| Confirmar efectivo como partner/customer | Sí | No | Partner miembro / tourist propietario |
| Procesar refund | Sí | No | No |
| Generar comisión o settlement | Sí | No | No |
| Reportar settlement | Sí | No | Owner/manager del partner |
| Verificar settlement | Sí | No | No |
| Procesar vencimientos/suspensión por deuda | Sí | No | No |
| Ajustes financieros arbitrarios | No hay endpoint | No | No |

Este cambio restringe los permisos anteriormente compartidos de operator/admin. No
cambia montos, prorrateo, idempotencia o fórmulas; los ajustes siguen derivados de refunds.
Los workers internos siguen siendo código confiable, no rutas públicas sin autorización.
Una futura delegación de tesorería requerirá decisión expresa del propietario.

## Pendientes y riesgos

Frontend Admin: vistas responsive, formularios con motivos, entrega/invitación de cuentas,
confirmaciones claras, mostrar 401/403/409, paginación y la advertencia de onboarding legacy.
No ocultar controles backend ni construir permisos a partir de valores del cliente.

Portal Partner: mismas membresías y selección de negocio; owner no adquiere acceso Admin.
Añadir UI sin conceder autovinculación ni escrituras administrativas por pertenencia.

Producción: TLS/proxy, retención de PII y auditoría, usuarios DB con privilegios mínimos,
límites de entrada, carga y runtime sin warning LibreSSL. No hay certificación para
1.000 concurrentes. El lock global administrativo debe medirse si aumenta el volumen.
El acceso SQL privilegiado puede saltarse servicios: debe mantenerse restringido;
esta fase protege la superficie HTTP y servicios de gobernanza, no superusuarios DB.

## Validación

Resultados finales se registran tras completar pruebas. El primer bloque integrado
registró 243 passed/1 failed: un test financiero antiguo esperaba permisos de operator
sobre refunds. Se actualizó a admin y se mantuvo prueba explícita de operator→403.
No se ocultó el fallo ni se relajó la política para hacerlo pasar.

Validación final: **293 passed, 1 warning in 59.32s**; suite completa ejecutada una
sola vez. El warning conocido corresponde a urllib3/LibreSSL del runtime local.
Tests específicos Admin/finanzas: **40 passed in 17.63s**. Se añadieron 25 casos
Admin; las regresiones Auth, Partners y Commercial están incluidas y verdes.
Compilación, import FastAPI, OpenAPI (14 operaciones autenticadas), checksum 006,
auditores Admin/Partners/Post-auth y git diff --check correctos.

Decisión: **READY_FOR_WHATSAPP** para iniciar la siguiente fase de desarrollo;
no constituye aprobación de despliegue productivo ni de carga de 1.000 concurrentes.
No se detectaron riesgos Critical/High abiertos en el alcance validado.
Existe un partner legacy activo sin owner previo a esta fase: requiere vinculación
administrativa autorizada durante onboarding. No se cambió su estado ni se creó
una identidad real. La cuenta admin y su sesión permanecen conservadas.
Sin commit ni push.
