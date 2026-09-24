# Esquema y migraciones

`schema_snapshot.sql` es una exportación **solo del esquema**, obtenida durante
la auditoría del 18 de septiembre de 2026 mediante pg_dump 16.15. Incluye
extensiones, índices, claves foráneas y triggers, sin datos, propietarios ni permisos.
No es una migración incremental y **no debe ejecutarse sobre la base existente**.
Para reconstruir una base vacía se necesita un usuario autorizado para crear las
extensiones pgvector/pgcrypto. La restauración en una base nueva queda por validar.

`migrations/001_create_refunds.sql` es el script previo de creación de refunds.
La tabla ya existe en la base inspeccionada; no se volvió a ejecutar. No hay
registro de versiones ni un ejecutor de migraciones. La instantánea ya incluye
refunds; no ejecutar además 001 sobre una restauración de esta instantánea.

Auditoría sin escrituras ni información personal:

```sh
.venv/bin/python -m scripts.audit_database
```

Pruebas:

```sh
.venv/bin/python -m pytest -q
```

Los tests comerciales insertan exclusivamente fixtures identificadas aleatoriamente
bajo una transacción `force_rollback=True`. Cada llamada usa un savepoint y los
commits de los endpoints no salen de la transacción de prueba. No realizan DROP,
TRUNCATE ni limpieza de datos existentes. Requieren el esquema actual; es preferible
usar una base dedicada. Los tests originales dependen de datos turísticos y del
modelo de Hugging Face. No se ejecutan scripts de ingesta, geocoding o embeddings.

## Endurecimiento comercial: migración 002

`migrations/002_financial_adjustments.sql` es aditiva y fue aplicada durante la
segunda fase de endurecimiento. Agrega campos de idempotencia/auditoría a refunds,
reported_amount a partner_settlements y tres tablas: commission_adjustments,
settlement_adjustments, financial_events. Sus partidas son append-only; triggers
impiden alterarlas y validan procedencia, moneda, partner y crédito disponible.

Ejecutor explícito (no se ejecuta al arrancar FastAPI):

```sh
.venv/bin/python -m scripts.apply_financial_migration
```

Usa transacción, lock de migraciones y `schema_migrations` con SHA-256. Repetirlo
verifica el checksum y no reaplica cambios. No cubre retroactivamente la migración
001, que ya estaba incorporada en la instantánea anterior. No modificar una
migración aplicada; crear una nueva para cambios posteriores.

`schema_snapshot.sql` sigue siendo la instantánea **anterior a 002**. Restaurar
esa instantánea en una base nueva exige aplicar 002 después. No restaurarla sobre
una base existente.

## Identidad: migraciones 003 y 004

`003_identity_auth.sql` ya estaba aplicada al iniciar esta fase; se conservó sin
cambios y se verificó su checksum. Define users y auth_sessions. La migración
aditiva `004_auth_security.sql` agrega límites persistentes de login y triggers
de versionado/revocación de identidad. Ambas están registradas en schema_migrations.

```sh
.venv/bin/python -m scripts.apply_auth_security
.venv/bin/python -m scripts.audit_identity
```

El ejecutor de 004 exige 003 aplicada y verifica checksums; no crea usuarios ni
reaplica 003. La instantánea anterior tampoco incluye estas migraciones. Véase
[Identidad y autenticación](../docs/identity_authentication.md) para los contratos,
la configuración y los límites operativos.


## Partners: migración 005

`005_partner_memberships.sql` incorpora membresías multiusuario, eventos de auditoría
append-only y causa de suspensión. Conserva users.partner_id como ancla legacy y
sustituye únicamente su índice único por uno no único, sin borrar datos.

```sh
.venv/bin/python -m scripts.apply_partner_memberships
.venv/bin/python -m scripts.audit_partners
```

005 ya está aplicada y registrada con checksum. No editar 001–005; cambios nuevos
requieren otra migración. No hay downgrade destructivo automático: conservar auditoría
y comprobar duplicados antes de considerar restaurar la unicidad anterior.
Véase [Arquitectura Partners](../docs/partners_architecture.md). La instantánea antigua
no incluye estas migraciones; no restaurarla sobre la base existente.


## H4U Admin: migración 006

`006_admin_audit.sql` agrega el ledger administrativo append-only. Se ensayó con
rollback antes de aplicarse y registrarse con checksum. No modifica cuentas reales,
finanzas ni migraciones previas. Ejecutores: `scripts.apply_admin_audit` y
`scripts.audit_admin`. No editar 001–006; usar 007+ para cambios posteriores.
Véase [H4U Admin](../docs/h4u_admin_architecture.md) para política, servicios y límites.

## 007 — WhatsApp messaging

`007_whatsapp_messaging.sql` añade transporte de canal, inbound, outbox, eventos
comerciales y auditoría de vinculaciones/consentimiento. También añade la marca
`service_requests.messaging_suppressed` para excluir simulaciones y sus eventos futuros.
Captura eventos comerciales por triggers; nunca realiza HTTP en la base de datos.

Aplicación explícita/checksum: `.venv/bin/python -m scripts.apply_whatsapp_messaging`.
Auditoría de conteos: `.venv/bin/python -m scripts.audit_whatsapp`.
007 ya aplicada tras dry-run y rollback verificado: NO editar 001–007; próxima 008+.
Ver `docs/whatsapp_architecture.md` para operación del worker, privacidad y Meta pendiente.
