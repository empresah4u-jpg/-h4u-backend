# Arquitectura de Ingesta H4U

## Objetivo

La capa de ingesta de H4U obtiene información turística desde múltiples fuentes, la transforma a un modelo común, la valida y finalmente la carga en PostgreSQL.

El diseño debe funcionar para Paracas y posteriormente para cualquier destino incorporado a H4U.

## Principio de diseño

Cada fuente externa debe mantenerse desacoplada.

Estructura:

Fuente
↓
Extractor / Client
↓
Raw Data
↓
Mapper
↓
Validación
↓
Modelo H4U
↓
PostgreSQL
↓
Embeddings

Esto evita que cambios en una API externa afecten directamente al modelo interno.

---

## Fuentes

### Google Places

Directorio:

ingestion/google_places/

Componentes:

- client.py
- extractor.py
- mapper.py
- README.md

Responsabilidades:

client.py
- autenticación
- comunicación con la API
- requests
- paginación
- manejo de errores

extractor.py
- búsqueda de establecimientos
- extracción por destino
- almacenamiento de respuesta original

mapper.py
- convertir estructura Google → estructura H4U
- normalizar campos
- preparar registros para validación

Información esperada:

- nombre
- dirección
- coordenadas
- categoría
- rating
- cantidad de reseñas
- teléfono
- website
- horarios
- place_id

---

## OpenStreetMap

Directorio:

ingestion/osm/

Fuente principal:

OpenStreetMap / Overpass API.

Uso previsto:

- atracciones
- hoteles
- restaurantes
- farmacias
- cajeros
- hospitales
- servicios
- puntos geográficos

OSM será especialmente útil para enriquecer coordenadas y cobertura geográfica.

---

## Booking

Directorio:

ingestion/booking/

La integración se realizará cuando H4U disponga del acceso correspondiente a la API de Booking.

Información potencial:

- hoteles
- disponibilidad
- precios
- habitaciones
- información comercial
- enlaces de reserva

Las credenciales nunca deben almacenarse directamente en el código.

Se utilizarán variables de entorno y posteriormente Secret Manager en producción.

---

## Partners

Directorio:

ingestion/partners/

Esta capa permitirá incorporar información proporcionada directamente por:

- hoteles
- restaurantes
- operadores turísticos
- transportistas
- guías
- empresas locales

Los partners podrán convertirse en una fuente de información de mayor confianza que fuentes públicas para determinados campos.

---

## Capas de datos

### RAW

Directorio:

data/raw/

Contiene respuestas originales obtenidas desde las fuentes.

No deben modificarse.

Ejemplo:

data/raw/google_places/paracas/

### PROCESSED

Directorio:

data/processed/

Contiene información:

- normalizada
- limpiada
- deduplicada
- validada

### SAMPLES

Directorio:

data/samples/

Contiene muestras pequeñas utilizadas para:

- desarrollo
- pruebas
- debugging
- documentación

Los datasets completos no deben almacenarse innecesariamente en Git.

---

## Pipelines

Directorio:

pipelines/

### load_hotels.py

Carga y actualiza alojamientos.

### load_restaurants.py

Carga y actualiza restaurantes.

### load_tours.py

Carga y actualiza tours.

### validate_entities.py

Aplica reglas de calidad.

Ejemplos:

- nombre obligatorio
- destination_id válido
- coordenadas válidas
- rating dentro del rango esperado
- moneda normalizada
- URLs válidas
- detección de duplicados

---

## Estrategia de actualización

No todas las fuentes necesitan actualizarse con la misma frecuencia.

Ejemplo conceptual:

Disponibilidad y precios
→ alta frecuencia

Ratings y reviews
→ frecuencia media

Direcciones y coordenadas
→ baja frecuencia

Información institucional
→ actualización bajo demanda

La frecuencia definitiva dependerá de las condiciones, límites y costos de cada proveedor.

---

## Deduplicación

Una misma entidad puede aparecer en varias fuentes.

Ejemplo:

Google Places
+
OpenStreetMap
+
Booking
+
Partner H4U

no deben convertirse automáticamente en cuatro hoteles diferentes.

La deduplicación utilizará progresivamente:

- identificadores externos
- nombre normalizado
- coordenadas
- dirección
- teléfono
- website
- similitud textual

---

## Trazabilidad

Cada registro deberá poder indicar de dónde provino su información.

La arquitectura evolucionará hacia campos o tablas para registrar:

- source
- source_entity_id
- extracted_at
- updated_at
- verification_status
- confidence
- last_verified_at

Esto permitirá decidir qué fuente tiene prioridad cuando existan datos contradictorios.

---

## Embeddings

Después de cargar o modificar entidades relevantes se podrán regenerar sus embeddings.

Flujo:

Entidad actualizada
↓
Construcción de contenido semántico
↓
SentenceTransformer
↓
Embedding 384 dimensiones
↓
entity_embeddings
↓
pgvector

En producción se buscará actualizar únicamente embeddings de entidades nuevas o modificadas, evitando regenerar todos innecesariamente.

---

## Evolución

Etapa actual:

APIs / datasets
↓
Python
↓
PostgreSQL

Evolución:

APIs externas
↓
Ingestion Services
↓
Raw Storage
↓
Validación / Normalización
↓
PostgreSQL
↓
FastAPI

Evolución cloud:

Fuentes
↓
Cloud Run / Jobs
↓
Cloud Storage
↓
Pub/Sub
↓
Procesamiento
↓
Cloud SQL PostgreSQL
↓
BigQuery
↓
Analítica
## Saneamiento de trazabilidad — bloque 5A (2026-09-26)

Se revisaron las 11 fuentes pendientes. Se aplicaron 57 relaciones de siete
fuentes; cuatro permanecen pendientes por falta de acceso verificable a su
contenido. Una relación indica respaldo documental de la identidad de una entidad;
no certifica su operación actual, calidad, autorización o vigencia de Safe Travels.
Los directorios consultados pueden ser versiones indexadas, no capturas en vivo.

### Implementación y garantías

`scripts/reconcile_sources.py` usa el manifiesto explícito
`data/provenance/block_5a.json`: UUID de fuente y entidad, URL exacta, código,
nombre esperado, fecha de revisión y justificación por relación. No realiza
matching geográfico ni fuzzy. No descarga fuentes ni descubre candidatos al ejecutar.
Las correspondencias seleccionadas se revisaron manualmente; no es un inventario
exhaustivo de todos los prestadores mencionados por cada directorio.

```bash
# Ensaya inserciones reales dentro de BEGIN/ROLLBACK y verifica el rollback.
python -m scripts.reconcile_sources
# Repite el ensayo y sólo entonces aplica en otra transacción validada.
python -m scripts.reconcile_sources --apply
```

El modo por defecto NO es una transacción READ ONLY: ensaya las escrituras y
siempre revierte. Requiere permisos INSERT y adquiere bloqueos breves. Usa
SHARE ROW EXCLUSIVE sobre entity_sources y SHARE sobre fuentes/catálogos/embeddings,
con timeout de bloqueo de 5 segundos. Esto serializa escritores y protege la
validación de referencias polimórficas durante la operación. Compara fingerprints
internos de filas completas antes/después, confirma el rollback y rechaza cambios
entre ensayo y aplicación. Cada error aborta la transacción correspondiente.

Se preservan relaciones ya existentes sin actualizarlas. No se modifican tablas
comerciales, usuarios, auth, embeddings ni metadatos de entidades. Las nuevas
relaciones usan MANUAL_VERIFIED_SOURCE_MATCH y confidence_score=NULL: no se inventa
un score numérico ni se sustituye la confianza Google Places de las entidades.
HOT002 conserva google_places_verified y 0.950.

No hay migración ni cambio de esquema. entity_type es varchar sin CHECK/enum;
el backend y semantic search no consultan entity_sources. El registro de tipos
del script admite general_service y se probó con tablas temporales, pero no se
crearon relaciones de ese tipo sin evidencia. Esta ampliación de trazabilidad no
amplía las categorías ni el contrato vectorial de semantic search.

entity_sources tiene PK(id), FK(source_id) e índice no único sobre
(entity_type,entity_id); carece de UNIQUE(source_id,entity_type,entity_id) y de FK
polimórfica. El script previene duplicados bajo bloqueo y audita todos los tipos
conocidos; otros escritores futuros deben adoptar las mismas garantías o una
restricción adicional revisada. No se añade esa restricción en este bloque.

### Fuentes vinculadas y evidencia

Los códigos siguientes identifican las relaciones exactas aplicadas; el manifiesto
contiene UUID, nombres y notas reproducibles.

| Fuente | Evidencia | Entidades vinculadas | Total |
|---|---|---|---:|
| [Casa Andina](https://fichas.casa-andina.com/cas_paracas_en.pdf) | Página 1 identifica hotel y dirección km 18.5 | HOT002 | 1 |
| [MINCETUR circuits](https://consultasenlinea.mincetur.gob.pe/safeTravels/destinos/PLANTILLA_DESTINOS_ICA_21_03.pdf) | Página 1 nombra atractivos de los circuitos Ica/Paracas | ATT002, ATT003, ATT004, ATT005, ATT006, ATT007, ATT014, ATT024, ATT025, ATT028, ATT035, ATT036, ATT037 | 13 |
| [Tripadvisor cafés](https://www.tripadvisor.com/Restaurants-g445063-c8-Paracas_Ica_Region.html) | Establecimientos enumerados explícitamente en el listado | BAR010, BAR002, BAR012, BAR004, RES026, BAR013, BAR015, RES083, BAR014, BAR016, BAR011, RES016, RES018, RES074, BAR022, RES078 | 16 |
| [Booking B&B](https://www.booking.com/reviews/pe/city/bbs-inns/paracas.html) | Alojamientos nombrados en ranking o reseñas recientes | HOT017, HOT019, HOT030, HOT011, HOT042, HOT041, HOT063, HOT026, HOT043, HOT028 | 10 |
| [MINCETUR mayo](https://consultasenlinea.mincetur.gob.pe/safetravels/destinos/DESTINOS_ICA_mayo.pdf) | Página 5, bloque de prestadores de Paracas | HOT060, RES009, RES019, BAR010, RES027 | 5 |
| [Tripadvisor español](https://www.tripadvisor.es/Restaurants-g445063-Paracas_Ica_Region.html) | Restaurantes identificados en listado y sección comida local | RES012, RES022, RES020, RES003, RES019, RES027, RES016 | 7 |
| [Tripadvisor inglés](https://www.tripadvisor.com/Restaurants-g445063-Paracas_Ica_Region.html) | Restaurantes identificados en listado y sección local eats | RES012, RES019, RES022, RES020, RES003 | 5 |

Casa Andina también describe Pacific Room en su ficha. No existe un venue Casa
Andina en el catálogo; no se crea uno en este saneamiento. La fuente se vincula al
hotel que realmente describe, aunque authority_level tenga el valor event_venue.
No se cambia esa etiqueta ni se deduce que sea un contrato cerrado de tipos.

Se dejaron fuera correspondencias con identidades ambiguas o direcciones distintas,
por ejemplo las variantes Chalana, El Delfín Dorado, El Galeón, Intimar, Bamboo y
Los Frayles. El nombre de una fuente no obliga a consumir todas sus menciones.

### Fuentes que permanecen pendientes

| UUID | Fuente y motivo |
|---|---|
| fdae2273-95c4-413e-92fa-75e7e46fece2 | [Municipalidad Paracas](https://www.gob.pe/institucion/muniparacas/contacto-y-numeros-de-emergencias): acceso al contenido fallido. Los resultados indexados sólo acreditan el canal municipal; no permiten validar los candidatos EM/GS. |
| 588a3d9b-55f8-4eae-b372-557e3fdb4292 | [Municipalidad Pisco](https://www.gob.pe/institucion/munipisco/contacto-y-numeros-de-emergencias): acceso fallido. Una mención indexada al COEL no respalda por sí sola hospitales, comisarías ni centros de salud candidatos. |
| f65cccbe-bb0e-4890-a809-5b9f569190c8 | [PROMPERÚ/IPERÚ alimentos](https://cdn.www.gob.pe/uploads/document/file/5250103/4721575-ica-alimentos-y-bebidas.pdf?v=1735832976): PDF inaccesible, incluido intento sin query. No se sustituye por otro documento. |
| 32ba6211-72f4-4e87-9089-52052411498f | [Tripadvisor postres](https://www.tripadvisor.com/Restaurants-g445063-zfg9909-Paracas_Ica_Region.html): contenido inaccesible; no se extrapola del directorio de cafés. |

No se prueba la inexistencia de evidencia en estas fuentes: queda pendiente poder
consultarla. No se agregaron relaciones municipales por pertenencia geográfica.
Las fuentes ya vinculadas de hospitales/comisarías se conservan.

### Resultado y validación

| Conteo | Antes | Después |
|---|---:|---:|
| hotels | 91 | 91 |
| venues | 12 | 12 |
| emergency_services | 25 | 25 |
| general_services | 29 | 29 |
| data_sources | 59 | 59 |
| entity_sources | 51 | 108 |
| pending_sources | 11 | 4 |
| entity_embeddings | 276 | 276 |

También se preservaron attractions=37, restaurants=98, tours=50,
tour_operators=38 y transport_providers=13. Los fingerprints de catálogos,
fuentes y embeddings permanecieron idénticos. Rollback verificado antes de aplicar;
segunda ejecución propone cero relaciones. Duplicados y huérfanos de relaciones y
embeddings: cero; vectores no nulos de dimensión 384. No se reconstruyeron embeddings.

Pruebas nuevas: 12 casos PostgreSQL sobre tablas TEMP que cubren rollback del ensayo,
idempotencia, preservación de catálogos, general_service/emergency_service,
rechazo de drift, falta de evidencia, referencias inexistentes, tipos no admitidos,
duplicados y error posterior a una inserción en la transacción de aplicación.

Ejecución final: `pytest -q --tb=short`: **435 passed, 1 warning in 88.13s**.
La advertencia corresponde a urllib3 con LibreSSL 2.8.3 del entorno existente.
Pruebas específicas: **12 passed in 1.67s**. `git diff --check` sin errores.
Inspección `scripts.sync_embeddings`: create=0, update=0, unchanged=276,
orphan=0, written=0; modelo y contenido canónico coherentes. No commit ni push.

## Reauditoría del checkpoint de 108 relaciones (2026-09-26)

Auditoría sin ejecutar reconcile_sources ni sus inserciones de ensayo sobre h4u.
Se ejecutaron SELECT dentro de transacciones READ ONLY. El análisis de la diferencia
histórica de embeddings está en [embedding_sync.md](embedding_sync.md#reauditoría-del-checkpoint-2026-09-26).
No se modificó block_5a.json, datos ni migraciones.

Las 108 relaciones pasan la verificación de referencias por tipo y fuente,
duplicados exactos y por (source_id,entity_type,entity_id): todos cero.
Los 57 vínculos del manifiesto coinciden en URL, estado, notas y confianza NULL.
Esto verifica fidelidad a la evidencia documentada, no una nueva certificación de
las 51 relaciones heredadas ni de la vigencia comercial de sus entidades.

Metadatos: 24 AUTO_MATCH_EXACT_NAME con 0.950; 3 AUTO_MATCH_EXACT_URL con 0.980;
24 MANUAL_VERIFIED_SOURCE_MATCH con 1.000 y 57 con confianza NULL. Sin estados
vacíos ni scores fuera de [0,1]. Hay seis source_url NULL heredados: Wayki Bus (1),
Paracas Responsable (1), Hotel Paracas events (4). Sus data_sources sí tienen URL;
las restantes 102 URLs son HTTP(S) y coinciden con la fuente. No se rellenaron
silenciosamente: revisar si NULL significa heredar la URL y documentar ese contrato
antes de normalizar. No constituye un huérfano ni una relación nueva defectuosa.

Las cuatro fuentes pendientes siguen pendientes. Se reintentó el acceso:
municipalidades devuelven 418; PDF PROMPERÚ y Tripadvisor postres no son accesibles
con la herramienta. Para resolverlas falta:

- Paracas: contenido accesible de la URL exacta que nombre al servicio candidato,
  con teléfono/dirección u otro identificador que lo distinga; un canal municipal
  genérico no acredita comisarías, asociaciones ni terminales turísticos.
- Pisco: listado oficial accesible que identifique hospital, comisaría o servicio
  concreto; la existencia del COEL o pertenecer a Pisco no lo prueba.
- PROMPERÚ: PDF exacto accesible y página/fila del establecimiento, permitiendo
  contrastar nombre y ubicación/identificador con el catálogo.
- Tripadvisor postres: listado accesible de esa categoría y ficha identificable del
  establecimiento; no extrapolar desde cafés o restaurantes.

UNIQUE(source_id,entity_type,entity_id): recomendado, sin conflictos actuales y
compatible con reconcile_sources; permite varias fuentes por entidad. No hay
consumidores API que necesiten duplicar ese triple. El esquema actual carece de
historial/versionado de una relación; si se requiere, conviene diseñar revisiones
separadas. No resuelve la FK polimórfica. Una migración futura debe comprobar
nuevamente duplicados, planificar bloqueos y probar rechazo de duplicados reales;
no se creó/aplicó aquí.

Revisión reconcile_sources: idempotente, --apply explícito, sin DELETE; el modo
predeterminado inserta y revierte, por lo que no es una auditoría READ ONLY.
Bloqueos, comprobación de snapshots y rollback evitan aplicar un plan desactualizado.
Cada ejecución repetida sin cambios no inserta filas. Errores abortan y se propagan;
JSON en stdout, sin logging persistente. Límites observados: la comparación final
entre added del ensayo/aplicación sucede después del commit; no es una garantía
adicional de rollback. No hay prueba concurrente entre dos procesos, aunque los
bloqueos serializan escrituras. El verificador no compara metadatos de vínculos ya
existentes contra el manifiesto (esta auditoría sí los comparó). La comprobación
post-rollback usa READ COMMITTED y podría rechazar cambios concurrentes ajenos de
forma conservadora. Estas limitaciones no invalidan la aplicación ya comprobada.

### Resumen completo por fuente y tipo

| Fuente | Tipo | Relaciones |
|---|---|---:|
| AQUAMARINE PARACAS Beach Hostal | hotel | 1 |
| Booking - Antares Paracas | hotel | 1 |
| Booking - Paracas lodging market | hotel | 10 |
| Casa Andina Select Paracas technical sheet | hotel | 1 |
| Casa Paracas | hotel | 1 |
| Cloudbeds - Viajero Paracas | hotel | 1 |
| Cruz del Sur Paracas Terminal | transport_provider | 1 |
| El Arizal | restaurant | 1 |
| Fruzion | restaurant | 1 |
| Hospedaje Sand Rain | hotel | 1 |
| Hospedaje vista del sur paracas | hotel | 1 |
| Hospital San Juan de Dios de Pisco | emergency_service | 1 |
| Hostal Mendieta | hotel | 1 |
| Hostel Killamoon Centro | hotel | 1 |
| Hotel Paracas events | venue | 4 |
| Hotel & Restaurante El Delfin Dorado | hotel | 1 |
| Inti-Mar Hospedaje | hotel | 1 |
| Karamba Resto-Bar | restaurant | 1 |
| Killamoon House Paracas | hotel | 1 |
| Kiwitaxi Paracas | transport_provider | 1 |
| La Trattoria de Paracas | restaurant | 1 |
| Lobo Fino Restaurant | restaurant | 1 |
| Los Frayles | hotel | 1 |
| Mar Azul | hotel | 1 |
| MINCETUR - Bodega Doña Juanita | attraction | 1 |
| MINCETUR - Bodega El Catador | attraction | 1 |
| MINCETUR - Bodega La Caravedo | attraction | 1 |
| MINCETUR - Bodega Tacama | attraction | 1 |
| MINCETUR - Playa Carhuas | attraction | 1 |
| MINCETUR - Playa Lagunillas | attraction | 1 |
| MINCETUR - Playa Mendieta | attraction | 1 |
| MINCETUR Safe Travels Ica | hotel | 1 |
| MINCETUR Safe Travels Ica | restaurant | 4 |
| MINCETUR - Safe Travels Ica circuits | attraction | 13 |
| Muelle Viejo Restobar | restaurant | 1 |
| Océano Cocina de Mar | restaurant | 1 |
| Paracas Ayni Guest House | hotel | 1 |
| Paracas Backpackers House | hotel | 1 |
| Paracas Responsable | transport_provider | 1 |
| Paracas restaurant inventory | restaurant | 7 |
| Peru Hop schedule | transport_provider | 1 |
| Peru Transtur | transport_provider | 1 |
| PNP - Comisaría Huamaní / San Clemente | emergency_service | 1 |
| PNP - Comisaría Independencia D | emergency_service | 1 |
| PNP - Comisaría Pisco A | emergency_service | 1 |
| PNP - Comisaría San Andrés B | emergency_service | 1 |
| Residencial Maria Bonita | hotel | 1 |
| Restaurante El Che | restaurant | 1 |
| Restaurant Paracas | restaurant | 1 |
| Risitas Taxi | transport_provider | 1 |
| San Agustin Paracas | hotel | 1 |
| San Agustín Paracas | hotel | 1 |
| Tripadvisor - Paracas cafés | restaurant | 16 |
| Tripadvisor - Paracas restaurants | restaurant | 5 |
| Vegano Peruano | restaurant | 1 |
| Wayki Bus | transport_provider | 1 |

Totales por tipo: hotel 30, restaurant 42, attraction 20, emergency_service 5, transport_provider 7, venue 4. Total 108; 55 fuentes vinculadas y 4 pendientes.

Validación de esta reauditoría: pruebas específicas **34 passed in 2.71s**;
suite completa **435 passed, 1 warning in 109.03s** (urllib3/LibreSSL conocido).
`compileall -q app scripts tests` correcto; requirió permiso para la caché externa
de Python. `git diff --check` correcto. Sólo se extendieron documentos en esta
reauditoría; sin --apply, migraciones, commit ni push. Recomendación: checkpoint
apto para commit con estas limitaciones documentadas; UNIQUE y normalización de
URLs son trabajos posteriores separados, no motivos para alterar datos en auditoría.
