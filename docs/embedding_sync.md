# Sincronización incremental de embeddings

Inspección (predeterminada, transacción REPEATABLE READ / READ ONLY):

```sh
python -m scripts.sync_embeddings
```

No carga SentenceTransformer, no genera vectores ni escribe filas. El contenido se
obtiene directamente de `scripts.generate_embeddings.load_entities()`; el hash usa
su misma función `content_hash()`. No hay una segunda implementación del contenido
canónico.

Aplicación explícita, solamente después de revisar el diagnóstico:

```sh
python -m scripts.sync_embeddings --apply
```

- CREATE: no existe el par entity_type/entity_id.
- UPDATE: cambió el hash, modelo, contenido canónico, destino o contrato del vector.
- UNCHANGED: todos esos campos son coherentes; no se toca la fila ni updated_at.
- ORPHAN: entidad de los cuatro tipos administrados ausente o inactiva. Solo reportado.
- Tipos no administrados: se reportan por separado como unsupported_records; no se borran.
- Duplicados: inspección los detalla; --apply aborta, sin elegir una fila arbitraria.

Se conserva el modelo `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
y `vector(384)`. Se comprueban dimensión, finitud y normalización (norma cuadrada
cercana a 1, tolerancia absoluta 0.001), coherente con normalize_embeddings=True del
generador actual. UPDATE conserva id y created_at; actualiza updated_at.

Cada aplicación es una sola transacción. Cualquier error, incluso de codificación,
validación o SQL después de una escritura, revierte todo. Si no hay cambios, no carga
el modelo. La segunda aplicación no realiza INSERT/UPDATE si las fuentes no cambiaron.
CREATE/UPDATE del resumen describen el plan inicial; written indica filas escritas
al completarse la transacción.

## Concurrencia y operación

--apply toma SHARE sobre las cuatro tablas fuente y SHARE ROW EXCLUSIVE sobre
entity_embeddings, antes de construir el plan. Así mantiene las fuentes estables y
serializa escrituras de embeddings incluso sin UNIQUE por entidad. Las lecturas
normales siguen permitidas. lock_timeout es 5 segundos; un conflicto aborta sin
escrituras parciales.

La generación mantiene esos locks: los escritores pueden esperar mientras carga y
codifica el modelo. Ejecutar en una ventana adecuada. No se cambian configuraciones,
credenciales, esquemas, modelos ni dimensiones. No hay DELETE ni reconstrucción global.

## Diagnóstico observado en h4u

Inspección durante la implementación, sin ejecutar --apply:

```text
active_entities: 271
existing_embeddings: 253
create: 18
update: 8
unchanged: 245
orphan: 0
written: 0
```

Las 8 actualizaciones se deben a contenido/hash distintos, no a vectores/modelos
incompatibles. No hay duplicados ni tipos no administrados.

Existe UNIQUE(entity_type, entity_id, content_hash), además de la PK. Ese índice
permite varias versiones por entidad si el hash cambia; no equivale a
UNIQUE(entity_type, entity_id). El índice de búsqueda que incluye destino tampoco
impone esa unicidad.

Recomendación: una migración separada y revisada para UNIQUE(entity_type, entity_id),
revalidando duplicados al aplicarla y planificando el bloqueo/construcción del índice.
No se creó ni aplicó esa restricción ni ninguna migración en esta tarea. La protección
del sincronizador no impide que otros escritores creen duplicados después de finalizar;
la restricción futura protegería también esas rutas.

## Pruebas

`tests/test_sync_embeddings.py` utiliza PostgreSQL/pgvector reales con tablas TEMP
privadas que ocultan los nombres públicos en esa conexión. No copia datos ni modifica
filas públicas. Un encoder determinista permite probar escrituras y rollback sin
reconstruir embeddings reales ni descargar un modelo.

```sh
python -m pytest tests/test_sync_embeddings.py -q
```

Cubre contenido canónico de los cuatro tipos, inspección READ ONLY sin carga del modelo,
CREATE, UPDATE por metadatos/vector, UNCHANGED, idempotencia incluyendo xmin/ctid y
fechas, huérfanos ausentes/inactivos, errores de encoder/dimensión/finitud/SQL,
duplicados y esquema incompatible.

Validación de esta implementación:

- Pruebas específicas: `22 passed in 3.66s`.
- Suite completa: `423 passed, 1 warning in 121.28s (0:02:01)`.
- Warning conocido: urllib3/LibreSSL.
- py_compile y git diff --check: OK.
- --apply NO ejecutado sobre h4u; no migraciones creadas/aplicadas.

## Reauditoría del checkpoint (2026-09-26)

El diagnóstico 271/253 anterior es histórico, no el estado actual. La consulta de
activos es `load_entities()` en generate_embeddings: cuatro SELECT sobre hotels,
restaurants, tours y attractions, todos con `WHERE status = 'active'`. Se cuenta la
lista combinada. existing_embeddings cuenta todas las filas de entity_embeddings
sin filtro de estado. Ninguna consulta depende de data_sources/entity_sources.

Estado actual, transacción REPEATABLE READ READ ONLY: 276 activos y 276 embeddings;
hotel=91, restaurant=98, tour=50, attraction=37. create=0, update=0, unchanged=276,
orphan=0, unsupported=0. Sin duplicados lógicos ni múltiples hashes por entidad.
Los 276 contenidos y SHA-256 coinciden con el renderizador canónico; destino,
modelo, dimensión 384, finitud y norma cumplen el contrato. No se regeneraron
vectores para esta auditoría: los metadatos correctos no demuestran por sí solos
qué ejecutable produjo cada vector.

Las fechas almacenadas permiten reconstruir el cambio numérico (UTC):

- 2026-09-26 16:57:02.645184: 18 nuevas filas (12 hotel, 4 restaurant,
  2 attraction) y 8 filas antiguas restaurant actualizadas.
- 2026-09-26 17:24:09.947565: creación de ATT033–ATT037, todas activas:
  Playa Carhuas, Playa Mendieta, Bodega Doña Juanita, Bodega La Caravedo y Bodega Tacama.
- 2026-09-26 17:25:57.104980: creación de los 5 embeddings adicionales attraction.
- Permanecen 245 embeddings con created_at=2026-08-22 y updated_at=2026-09-19.

Así, 253+18+5=276 embeddings y 271+5=276 entidades activas; las 8
actualizaciones explican la desaparición del UPDATE anterior. Son fechas de filas,
no un historial inmutable de ejecución: no prueban autor/comando ni excluyen otras
operaciones intermedias. El patrón es compatible con sincronización incremental,
pero no permite atribuirlo inequívocamente a sync_embeddings --apply.

Antes de reconcile_sources ya se midieron 276 embeddings; su ensayo y aplicación
compararon fingerprints completos y no cambiaron ninguno. No hay triggers de
usuario ni reglas sobre entity_sources que modifiquen embeddings. La conciliación
no causa esta diferencia. La consulta actual identifica h4u, OID 16384,
servidor 172.19.0.2:5432, system_identifier 7676591420387823655. Las ejecuciones
anteriores se documentaron como h4u y comparten configuración/código, pero no se
registró entonces esa identidad física. No puede certificarse retrospectivamente
que fuera exactamente la misma instancia o que nunca se restaurara.
log_statement=none, logging_collector=off y track_commit_timestamp=off en la
inspección actual; no se inspeccionaron historiales de shell ni secretos.

Recomendación UNIQUE(entity_type,entity_id): favorable para la tabla de embeddings
vigentes, previa migración separada autorizada y revalidación de duplicados.
Sync actualiza una fila por entidad; generate_embeddings elimina/reinserta por par;
rebuild actualiza por id. Ninguno necesita versiones simultáneas. Semantic search
no selecciona una versión vigente: varias filas pueden ocupar candidatos antes de
su deduplicación Python. El UNIQUE actual con content_hash permite versiones sin
un contrato funcional para seleccionarlas. Si se necesita historial/múltiples
modelos, diseñarlo explícitamente aparte antes de imponer una clave distinta.
Los tests de duplicados usan TEMP sin índices y pueden conservar sus fixtures;
una futura migración necesita tests propios de restricción real. No se aplicó DDL.

Revisión operacional: sync tiene inspección READ ONLY por defecto, --apply
explícito, transacción única, rollback por excepción y bloqueos; no DELETE.
El resumen JSON es su salida operativa, sin registro persistente de ejecuciones ni
identidad de BD. Los errores salen con código no cero/traceback; no se ocultan.
Carga/inferencia mantienen locks y no tienen límite global de ejecución; riesgo de
espera para otros escritores. generate_embeddings es un script legado distinto,
con DELETE/reinserción y sin esa misma protección; no fue ejecutado.

Validación de reauditoría: 34 pruebas específicas aprobadas en 2.71s;
435 pruebas completas aprobadas, 1 warning conocido, en 109.03s. Compilación y
`git diff --check` correctos. Sólo documentación modificada; no --apply ni migración.

## Integridad 009 aplicada (2026-09-26)

En h4u ya existe `entity_embeddings_entity_unique` sobre (entity_type,entity_id),
registrada mediante 009_catalog_integrity.sql. Se conserva el UNIQUE previo de
content_hash sin borrar índices ni datos. La tabla representa embeddings vigentes;
no permite múltiples versiones por entidad. El sincronizador incremental, el
rebuild por ID y la búsqueda son compatibles. El generador legado DELETE/INSERT
no se ejecutó y sigue sin ser la ruta recomendada.

Inspección posterior: 276 activos/embeddings, 276 unchanged, create=0, update=0,
orphan=0, unique_entity_key=true. No se ejecutó sync --apply ni se alteraron vectores,
contenido, modelo o hashes. Dry-run de DDL con rollback de esquema/ledger/datos
verificado antes de aplicar. Detalle y límites demo en [ingestion.md](ingestion.md#bloque-integridad-aplicado--migración-009-2026-09-26).

Suite del bloque: 447 passed, 1 warning en 94.98s; específicas 46 passed en 5.52s.
Compilación y git diff --check correctos. No commit ni push.
