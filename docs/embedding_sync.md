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
