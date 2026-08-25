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