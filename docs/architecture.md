# Arquitectura H4U

## Objetivo

H4U es una plataforma de concierge turístico diseñada para integrar información de hoteles, restaurantes, tours, atracciones, transporte y servicios turísticos.

Paracas es el destino piloto, pero la arquitectura está preparada para incorporar otros destinos de Perú y posteriormente otros países.

## Arquitectura actual

Usuario
↓
Frontend / PWA
↓
FastAPI
↓
PostgreSQL + pgvector
↓
Datos turísticos + búsqueda semántica

## Backend

El backend está desarrollado con FastAPI.

Responsabilidades principales:

- Exponer endpoints REST.
- Consultar PostgreSQL.
- Ejecutar búsqueda textual.
- Ejecutar búsqueda semántica.
- Aplicar validaciones.
- Registrar logs.
- Manejar errores de forma controlada.

## Base de datos

PostgreSQL es la base transaccional principal.

Actualmente contiene entidades como:

- destinations
- hotels
- restaurants
- tour_operators
- tours
- attractions
- transport_providers
- transport_routes
- emergency_services
- general_services
- entity_embeddings

## Búsqueda semántica

Se utiliza pgvector para almacenar embeddings.

Modelo actual:

sentence-transformers/all-MiniLM-L6-v2

Dimensión:

384

Flujo:

Consulta del usuario
↓
SentenceTransformer
↓
Embedding
↓
pgvector
↓
Similitud coseno
↓
Resultados ordenados

## Acceso a datos

Actualmente se utiliza psycopg con SQL parametrizado.

No se utiliza ORM en esta etapa para mantener el backend simple y permitir control directo sobre SQL.

Un ORM podrá evaluarse cuando el sistema incorpore funcionalidades con mayor volumen de escritura, como:

- usuarios
- reservas
- pagos
- sesiones
- favoritos
- transacciones

## Ingesta

Las fuentes se separan por proveedor:

- Google Places
- OpenStreetMap
- Booking
- Partners
- fuentes oficiales
- datasets internos

Cada fuente tendrá su propio proceso de:

extracción
↓
mapeo
↓
validación
↓
normalización
↓
carga a PostgreSQL

## Escalabilidad

La arquitectura debe permitir agregar nuevos destinos sin modificar la lógica principal.

Ejemplo:

Paracas
Cusco
Lima
Arequipa
otros destinos

Todas las entidades se relacionan mediante destination_id.

## Evolución futura

Frontend PWA
↓
FastAPI
↓
PostgreSQL / pgvector

y para analítica:

FastAPI / eventos
↓
Pub/Sub
↓
BigQuery
↓
Looker Studio