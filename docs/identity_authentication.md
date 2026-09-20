# Identidad y autenticación H4U

## Estado inicial comprobado

Se partió del commit 6cafeb7 en main, con requirements.txt modificado y los archivos
manuales app/identity.py y db/migrations/003_identity_auth.sql sin seguimiento.
Baseline: **113 passed, 1 warning in 20.08s**. Las tablas users/auth_sessions estaban
creadas y vacías. schema_migrations registraba 002 y 003.

La migración 003 se verificó contra esquema, restricciones, índices y checksum:
f7e26523912c4fae2f98d66d5e779fc07060f0cbc632238ece112e0858088cc4.
No se editó ni se volvió a ejecutar.

El identity.py manual contenía las líneas de shell utilizadas para escribirlo.
Python aceptaba su sintaxis como expresiones, pero importarlo fallaba con NameError.
Además ejecutaba psycopg síncrono dentro de un método async, convertía claims con
coerciones permisivas y no restringía issuer/audience/uso del token. Se reemplazó
esa implementación conservando el contrato IdentityProvider y los roles existentes.

El secreto inicial no cumplía la longitud mínima. El usuario lo reemplazó localmente
y descartó el anterior. No se inspeccionó ni imprimió el valor nuevo, ni se escribió
ningún secreto o credencial real al repositorio.

## Arquitectura

- app/identity.py: JWTSettings, emisión/validación de access tokens y JWTIdentityProvider.
- app/services/passwords.py: Argon2id, verificación, rehash y verificación dummy.
- app/services/authentication.py: login, sesiones, throttling y revocación transaccional.
- app/routers/authentication.py: contrato HTTP sin registro público ni asignación de roles.
- app/main.py: inicialización mediante lifespan, respuestas no-store y errores de
  validación sin eco de credenciales.
- app/auth.py: se conserva sin modificaciones; sus políticas y verificaciones de
  ownership siguen protegiendo los endpoints comerciales.

El lifespan obtiene la configuración de ejecución, crea el servicio en un worker
y conecta JWTIdentityProvider a app.state.identity_provider. No hay usuarios ni
claves de desarrollo de respaldo. Configuración ausente/inválida deja el servicio
cerrado: login 503 y endpoints que requieren identidad 401; los catálogos siguen
accesibles y se registra un mensaje sin valores de configuración.

La API se debe ejecutar con lifespan habilitado (normal en Uvicorn). El proceso debe
reiniciarse tras cambiar la configuración. Las variables de entorno del proceso
prevalecen sobre .env, conforme al load_dotenv existente.

## Endpoints

| Método/ruta | Entrada | Resultado |
|---|---|---|
| POST /auth/login | JSON con email y password | access_token, token_type=bearer, expires_in |
| GET /auth/me | Authorization: Bearer | subject, role, traveler_id, partner_id de PostgreSQL |
| POST /auth/logout | Authorization: Bearer | 204; revoca la sesión del token presentado |
| POST /auth/logout-all | Authorization: Bearer | 204; incrementa token_version y revoca todas las sesiones del propio usuario |

No existen endpoints de registro, alta de administrador, cambio de rol ni elección
de traveler/partner. La provisión posterior debe realizarse mediante administración
confiable, validando los vínculos. No se creó el administrador real del usuario.

Un logout repetido con el token ya revocado responde 401 y no produce efectos nuevos.
Logout-all afecta únicamente al usuario autenticado, no admite IDs de otros usuarios.

Los endpoints de auth envían Cache-Control: no-store y Pragma: no-cache. No se ponen
tokens en URLs ni cookies; no se implementa frontend ni almacenamiento del token en
el navegador. El cliente debe enviar Bearer por HTTPS.

## Contraseñas y login

Se utiliza Argon2id con memory_cost=65536 KiB, time_cost=3, parallelism=4, sal aleatoria.
El helper de provisión exige al menos 12 caracteres y como máximo 1024 bytes UTF-8;
no normaliza ni recorta contraseñas. Login admite credenciales existentes y limita
la entrada a 1024 bytes. El email se normaliza con trim/lower y se limita a ASCII
para una comparación coherente con lower(email) y su índice único existente.

Email desconocido, contraseña incorrecta, hash inválido y usuario disabled/locked
responden el mismo 401. Los usuarios desconocidos verifican un hash dummy aleatorio;
usuarios deshabilitados también pasan por verificación de hash. Esto reduce la
señal temporal, sin prometer tiempo constante de red/DB/Argon2.

No se imprimen credenciales. password usa SecretStr y los errores de validación de
/auth omiten input/ctx, evitando que FastAPI devuelva la contraseña rechazada.
El hash se actualiza cuando Argon2 requiere rehash, solo después de verificar la
contraseña. Ese cambio invalida sesiones anteriores por el trigger de seguridad;
el nuevo token usa el token_version actualizado.

Login bloquea primero users, verifica credenciales y crea la sesión y last_login_at
atómicamente. La respuesta con token solo se devuelve después del commit. No se
modifican entidades comerciales ni financieras.

## JWT y sesiones

- HS256 exclusivamente; JWT_SECRET de al menos 32 bytes. La calidad aleatoria del
  secreto es responsabilidad de su provisión, no se infiere únicamente de su longitud.
- JWT_EXPIRE_MINUTES entre 1 y 60 (valor habitual 60); sin leeway temporal.
- Issuer h4u-auth, audience h4u-api, token_use=access, cabecera typ=JWT.
- Claims requeridos: sub, jti, exp, iat, ver, iss, aud y token_use.
- sub y jti deben ser UUID; ver/iat/exp enteros auténticos, no strings, floats o bool.
- exp > iat, duración limitada, iat no futuro y exp vigente. Se valida nbf si aparece.
- Token recibido limitado a 4096 caracteres; algoritmos inesperados/none se rechazan.
- El token no contiene role, email, hashes ni IDs de propiedad; se toman de PostgreSQL.
  Incluso claims adicionales firmados que declaren privilegios no los conceden.

Cada validación requiere users.active, ver igual a token_version y una auth_session
que pertenezca al mismo usuario/jti, no revocada y no expirada según PostgreSQL.
Se comprueba nuevamente exp después de esperar locks. last_seen_at se actualiza solo
para sesiones aceptadas. El orden de bloqueo es user → session; la validación toma
SHARE en user para impedir cambios de identidad durante esa comprobación.

JWT/psycopg del proveedor se ejecutan mediante run_in_threadpool; login/logout son
handlers síncronos que FastAPI también ejecuta en workers. Argon2 tiene un límite
local de cuatro operaciones simultáneas por proceso para acotar memoria; si se
supera, responde 503 temporal. El límite de workers/recursos del despliegue debe
ser consistente con 64 MiB por operación.

## Migración aditiva 004

004_auth_security.sql agrega:

1. auth_login_limits: buckets persistentes compartidos por workers, sin emails/IPs
   en claro. Las claves son HMAC-SHA256 separadas por email normalizado y dirección.
2. Trigger que impide reducir token_version y lo incrementa al cambiar password_hash,
   role, traveler_id, partner_id o status.
3. Trigger que revoca sesiones cuando cambia token_version. Reactivar una cuenta
   no resucita tokens anteriores.

No altera 003, travelers, partners ni las tablas financieras. El ejecutor explícito
verifica primero el checksum de 003 y registra 004 con checksum en una transacción.
No aplica migraciones durante el arranque de FastAPI.

```sh
.venv/bin/python -m scripts.apply_auth_security
.venv/bin/python -m scripts.audit_identity
```

Throttling: máximo 10 intentos por email y 30 por dirección en una ventana de diez
minutos; cuentan también los logins correctos. Fallos y rechazos conservan los
contadores porque se confirman antes de comprobar credenciales. Se responde 429 con
Retry-After=600 al superar el límite. Los buckets se bloquean en orden estable.
No se confía directamente en X-Forwarded-For; el proxy/Uvicorn debe configurarse con
proxies confiables para que request.client represente al cliente real.

No se elimina información ni se purgan sesiones/buckets en esta fase. Será necesario
programar retención/limpieza controlada y límites de tráfico en el perímetro para
ataques distribuidos o alta cardinalidad de identidades intentadas.

## Refresh tokens

No se implementaron: todavía no hay un requisito de sesiones persistentes del cliente
que justifique una segunda credencial de larga duración. Al expirar el access token,
el usuario debe iniciar sesión otra vez. Si se necesitan después, usar tokens
opacos aleatorios, hashes almacenados, rotación de un solo uso, detección de replay y
revocación por familia; no reutilizar el access token como refresh.

## Pruebas y límites

Los tests usan secretos y contraseñas aleatorios generados en memoria, emails bajo
example.invalid y transacciones con rollback. No se crean cuentas reales, no se
usan contraseñas de producción ni se altera dinero existente. Se conservan las
pruebas comerciales y se añaden pruebas de tokens falsificados, tiempos, claims,
sesiones, cambios de identidad, rehash, rate limit, privacidad, roles y propiedad
mediante tokens reales.

Pendiente de una siguiente fase: provisión administrativa de cuentas, recuperación
y cambio de contraseña autenticado, MFA según necesidades, cliente seguro, TLS y
configuración de proxy/limites de ingreso, retención y pruebas de carga multiconexión.
La revocación se verifica al autenticar una solicitud: una operación ya autorizada
y en curso puede terminar. No se reescribió la arquitectura comercial para mantener
un lock de identidad durante toda operación de negocio.

## Validación final

- Suite completa: **180 passed, 1 warning in 52.32s**.
- Autenticación, autorización y regresiones comerciales/financieras:
  **155 passed in 24.68s**.
- Nuevas pruebas de identidad: **67 passed in 19.80s**.
- Advertencia preexistente: urllib3 v2 con Python compilado contra LibreSSL 2.8.3.
- Compilación en memoria de 49 archivos Python; pip check sin dependencias rotas.
- Lifespan e identidad inicializados con configuración local; health/PostgreSQL OK,
  acceso anónimo a auth/me rechazado y OpenAPI válido.
- Auditorías SQL comercial e identidad sin inconsistencias; users/auth_sessions
  siguen vacías y checksums de 003/004 coinciden.
- git diff --check sin errores. Sin commit ni push.
