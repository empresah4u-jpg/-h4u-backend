# H4U: migrator y runtime

La base `h4u` conserva al rol `h4u` como administrador/propietario. No se cambia
su contraseña ni se retiran sus privilegios en esta transición. Docker continúa
utilizando esa identidad. Los runners de migraciones y CLI administrativos de
catálogo/ingesta usan explícitamente `get_admin_connection()`; no se ejecutan desde
FastAPI ni reciben permisos adicionales los endpoints. Sus flags de aplicación y
controles existentes permanecen intactos. Ninguno de esos CLI de datos se ejecutó.

FastAPI usa `get_connection()`. Cuando DB_NAME=h4u exige `h4u_runtime` y falla
si faltan sus credenciales o tiene atributos elevados. DEMO mantiene su conexión
anterior y las bases desechables conservan su configuración aislada.

## Configuración privada

`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` conservan el canal
administrativo existente. `DB_RUNTIME_USER` y `DB_RUNTIME_PASSWORD` definen el
canal runtime; pueden inyectarse por entorno o mediante `.env.runtime` local,
ignorado por Git y con modo 0600. No hay fallback a DB_USER para FastAPI/H4U.
El alta prepara `.env.runtime.pending`; solo se activa tras el ensayo autorizado.
Los scripts no imprimen ni rotan secretos existentes. La identidad runtime nueva
usa una contraseña aleatoria local. No se incorpora ningún secreto al repositorio.

## Permisos explícitos

La allowlist auditable está en `scripts/setup_runtime.py`: READ, INSERT, UPDATE
y grants por columna. No hay DELETE, TRUNCATE, ownership, CREATE schema, privilegios
administrativos ni membresías. No se necesitan secuencias: las PK usan UUID.
CONNECT/USAGE habilitan acceso. Los permisos PUBLIC de TEMP en h4u y CREATE en
public se retiran; h4u conserva sus capacidades administrativas.

Los locks PostgreSQL necesitan UPDATE sobre alguna columna: products,
product_partners, sessions, travelers, cancellation_policy_versions y
commission_adjustments reciben UPDATE(id), no escritura general. Pasajeros reciben
solo las columnas del upsert existente; commissions recibe UPDATE de estado y
marcas de liquidación. Los triggers de inmutabilidad siguen vigentes.
`request_offer_consents` tiene exclusivamente SELECT/INSERT.

No se otorgan grants generales a tablas futuras. Cada migración debe extender
explícitamente la allowlist y probarla. ALTER DEFAULT PRIVILEGES del creador h4u
retira EXECUTE de PUBLIC para funciones nuevas. Funciones propias existentes no
son ejecutables directamente por PUBLIC/runtime, salvo request_consent_terms,
necesaria para el contrato. Las funciones de extensiones no se modifican.

## Operación y pruebas

Alta/reconciliación idempotente:
`python -m scripts.setup_runtime --apply --confirm-database h4u`.
No modifica datos comerciales; no cambia la contraseña de un rol existente.

`python -m scripts.check_runtime --confirm-database h4u` usa datos ficticios en
transacciones con rollback obligatorio y verifica login, lecturas FastAPI, auth,
consentimiento, reservas, pagos, refunds, comisiones, settlements y prohibiciones.
Un ensayo previo de 013 queda dentro del rollback si aún no está desplegada.

013 en H4U requiere el canal administrativo, runtime activo y
`--confirm-h4u` con el checksum aprobado; `--apply` permite persistir después del
ensayo con rollback. Los fingerprints excluyen únicamente las columnas nuevas,
para comparar todos los valores históricos. La migración SQL no se modifica.

## Límites conservados

El rol administrador continúa siendo superusuario. Las reglas trust para sockets
y loopback dentro del contenedor no se cambian; conexiones TCP externas al
contenedor usan SCRAM. Acceso al contenedor/credenciales administrativas sigue
siendo una capacidad privilegiada. Separación de procesos y retirada futura del
superusuario requieren un bloque independiente. No confundir permisos de la BD
con autorización funcional: roles/ownership de FastAPI siguen siendo obligatorios.
