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
