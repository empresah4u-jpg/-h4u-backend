# Roadmap H4U

## Visión

H4U es una plataforma de concierge turístico digital.

Paracas funciona como destino piloto, pero la plataforma se diseña desde el inicio para incorporar múltiples destinos.

---

# ETAPA 1 — MVP Y BASE TECNOLÓGICA

Objetivo:

Construir una base de datos turística confiable, un backend funcional y una primera aplicación web capaz de consultar y recomendar información.

---

## Día 1 — Dataset turístico

Estado: COMPLETADO

Se construyó y consolidó el dataset inicial de Paracas.

Categorías trabajadas:

- hoteles
- restaurantes
- operadores turísticos
- tours
- atracciones
- transporte
- emergencias
- servicios generales

Se realizaron:

- búsquedas multifuente
- consolidación de información
- normalización
- eliminación/corrección de duplicados
- ampliación del dataset
- preparación para PostgreSQL

Resultado:

Dataset turístico inicial disponible para alimentar H4U.

---

## Día 2 — Base de datos y arquitectura

Estado: COMPLETADO

Tecnologías:

- PostgreSQL
- Docker
- pgvector
- Python

Se diseñó un modelo multidestino.

Entidades principales:

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

Se cargó el dataset en PostgreSQL.

Se habilitó pgvector.

Se generaron embeddings reales utilizando:

sentence-transformers/all-MiniLM-L6-v2

Dimensión:

384

Resultado:

PostgreSQL contiene los datos turísticos y puede realizar búsquedas vectoriales.

---

## Día 3 — Backend

Estado: COMPLETADO

Tecnología principal:

FastAPI

Endpoints disponibles:

- GET /health
- GET /destinations
- GET /hotels
- GET /restaurants
- GET /tours
- GET /attractions
- GET /transport/routes
- GET /search
- GET /semantic-search

También se implementó:

- Swagger / OpenAPI
- CORS
- psycopg
- SQL parametrizado
- búsqueda global
- búsqueda semántica
- logging
- manejo de errores
- validaciones
- Git
- README
- .env.example
- .gitignore

Decisión técnica:

Actualmente no se utiliza ORM.

Se utiliza:

FastAPI
+
psycopg
+
SQL directo

Esto permite mantener control sobre las consultas durante la etapa inicial.

---

## Organización del proyecto

Estado: EN PROGRESO

Se está reorganizando el proyecto para separar responsabilidades.

Estructura:

app/
- API
- routers
- services
- models
- logging

ingestion/
- Google Places
- OpenStreetMap
- Booking
- partners

pipelines/
- cargas
- validaciones

scripts/
- herramientas administrativas

tests/
- pruebas

docs/
- documentación técnica

data/
- raw
- processed
- samples

Docker Compose administra PostgreSQL utilizando volumen persistente.

---

# DÍA 4 — CAPA DE INGESTA

Estado: SIGUIENTE

Objetivo:

Comenzar a reemplazar cargas manuales por procesos reproducibles de extracción.

Prioridad:

1. OpenStreetMap / Overpass
2. Google Places
3. fuentes públicas/oficiales
4. partners
5. Booking cuando exista acceso autorizado

Construir:

ingestion/osm/extractor.py

ingestion/google_places/client.py
ingestion/google_places/extractor.py
ingestion/google_places/mapper.py

pipelines/validate_entities.py

Objetivos técnicos:

- extraer datos
- almacenar RAW
- normalizar
- validar
- deduplicar
- hacer UPSERT
- registrar fuente
- registrar fecha de extracción

---

# DÍA 5 — FRONTEND / PWA

Objetivo:

Construir la primera interfaz usable de H4U.

Tecnología prevista:

Next.js

Flujo:

Turista
↓
PWA
↓
FastAPI
↓
PostgreSQL / pgvector

Funciones iniciales:

- buscar hoteles
- buscar restaurantes
- explorar tours
- consultar atracciones
- consultar transporte
- búsqueda semántica

Ejemplo:

"Quiero un hotel tranquilo cerca del mar"

Frontend
↓
FastAPI
↓
Embedding
↓
pgvector
↓
Hoteles relevantes

---

# DÍA 6 — INTELIGENCIA Y CONCIERGE

Objetivo:

Convertir la búsqueda en una experiencia de concierge.

Evolución:

Usuario
↓
Consulta natural
↓
Interpretación de intención
↓
Búsqueda H4U
↓
Filtros
↓
Ranking
↓
Recomendación

Ejemplos:

- dónde comer pescado
- hotel familiar cerca del mar
- qué hacer esta tarde
- tour para ver fauna marina
- cómo llegar a Huacachina

La IA no debe inventar información operativa.

Las respuestas deberán fundamentarse principalmente en información disponible y validada por H4U.

---

# DÍA 7 — MVP INTEGRADO

Objetivo:

Integrar los componentes desarrollados.

Arquitectura MVP:

Turista
↓
Next.js / PWA
↓
FastAPI
↓
PostgreSQL + pgvector
↓
Dataset H4U

Validar:

- frontend
- backend
- base de datos
- búsqueda
- recomendaciones
- manejo de errores
- rendimiento básico
- experiencia móvil

Resultado esperado:

Primer MVP funcional de H4U en Paracas.

---

# ETAPA 2 — AUTOMATIZACIÓN Y NEGOCIO

Después del MVP:

- automatización de ingestas
- Google Places
- OpenStreetMap
- APIs oficiales
- integración con partners
- Booking cuando exista acceso
- actualización incremental
- sistema de proveedores
- panel administrativo
- sesiones
- favoritos
- analítica
- enlaces/reservas
- modelo comercial

---

# ETAPA 3 — ESCALA

Objetivo:

Convertir H4U en una plataforma multidestino.

Destinos previstos:

- Paracas
- Cusco
- Lima
- Arequipa
- otros destinos

Arquitectura cloud prevista:

Frontend
↓
Cloud Run
↓
FastAPI
↓
Cloud SQL PostgreSQL
↓
pgvector

Datos / eventos
↓
Pub/Sub
↓
BigQuery
↓
Looker Studio

Complementos:

- Secret Manager
- Cloud Storage
- Cloud Logging
- CI/CD
- observabilidad
- backups
- seguridad
- monitoreo

---

# Estado actual

Día 1: COMPLETADO
Día 2: COMPLETADO
Día 3: COMPLETADO
Organización técnica: EN PROGRESO
Día 4: SIGUIENTE

H4U ya dispone de:

Dataset
+
PostgreSQL
+
pgvector
+
Embeddings
+
FastAPI
+
REST API
+
Búsqueda semántica
+
Logging
+
Manejo de errores
+
Git
+
Docker Compose

Siguiente objetivo:

CAPA DE INGESTA REPRODUCIBLE