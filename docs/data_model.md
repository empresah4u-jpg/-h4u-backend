# Modelo de Datos H4U

## Objetivo

El modelo de datos de H4U centraliza información turística de múltiples destinos y fuentes.

Paracas es el destino piloto, pero el diseño es multidestino desde el inicio.

## Entidad principal: destinations

Cada entidad turística se relaciona con un destino mediante `destination_id`.

Ejemplos de destinos:

- Paracas
- Cusco
- Lima
- Arequipa

## Entidades principales

### hotels

Contiene información de hoteles, hostales y alojamientos.

Campos principales:

- id
- destination_id
- code
- name
- category
- address
- phone
- website
- rating
- reviews_count
- price_observed
- currency
- latitude
- longitude
- verification_status
- status

## restaurants

Contiene restaurantes, cafés, bares y restobares.

Campos principales:

- id
- destination_id
- code
- name
- place_type
- category
- address
- rating
- reviews_count
- price_range
- currency
- latitude
- longitude
- verification_status
- status

## tour_operators

Contiene operadores turísticos.

Relación principal:

tour_operators
↓
tours

Un operador puede tener múltiples tours.

## tours

Contiene experiencias y actividades turísticas.

Campos principales:

- id
- destination_id
- tour_operator_id
- code
- name
- description
- destination_label
- duration_minutes
- price_from
- currency
- languages
- meeting_point
- booking_url
- verification_status
- status

## attractions

Contiene atractivos turísticos y lugares de interés.

Campos principales:

- id
- destination_id
- code
- name
- category
- zone
- description
- opening_hours
- adult_price
- child_price
- currency
- latitude
- longitude
- verification_status
- status

## transport_providers

Contiene proveedores de transporte.

Relación:

transport_providers
↓
transport_routes

## transport_routes

Contiene rutas de transporte disponibles.

Campos principales:

- id
- provider_id
- origin_name
- destination_name
- transport_type
- price
- currency
- schedule
- status

## emergency_services

Contiene servicios de emergencia y asistencia.

Ejemplos:

- policía
- bomberos
- centros médicos
- emergencias

## general_services

Contiene servicios complementarios.

Ejemplos:

- farmacias
- cajeros
- tiendas
- bodegas
- servicios generales

## entity_embeddings

Almacena embeddings para búsqueda semántica.

Campos principales:

- id
- destination_id
- entity_type
- entity_id
- content
- embedding
- embedding_model
- content_hash
- created_at
- updated_at

Actualmente se utiliza:

- modelo: sentence-transformers/all-MiniLM-L6-v2
- dimensión: 384

## Relaciones principales

destinations
├── hotels
├── restaurants
├── attractions
├── tour_operators
├── tours
├── transport_providers
├── emergency_services
└── general_services

tour_operators
└── tours

transport_providers
└── transport_routes

## Integridad referencial

Validaciones realizadas:

- hoteles sin destino: 0
- restaurantes sin destino: 0
- rutas sin proveedor: 0
- tours sin operador: parcialmente corregidos

## Escalabilidad

El modelo evita depender de Paracas de forma rígida.

La expansión a nuevos destinos se realiza agregando registros en `destinations` y asociando las entidades mediante `destination_id`.