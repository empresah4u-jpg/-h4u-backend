# Bootstrap del primer administrador H4U

## Propósito y límite de confianza

El bootstrap crea excepcionalmente el primer usuario `admin`, activo, sin vínculos
traveler/partner. No es un endpoint ni un mecanismo de gestión de empleados. Requiere
acceso autorizado al entorno de ejecución y a PostgreSQL; quien ya puede modificar
la base directamente queda fuera de la frontera de seguridad de esta herramienta.
No usa JWT ni exige una cuenta previa, porque resuelve precisamente ese primer acceso.

No se creó ninguna cuenta real durante el desarrollo. Las pruebas usan contraseñas
aleatorias y rollback de todos sus usuarios y sesiones.

## Ejecución local, después de revisar y autorizar el alta real

Desde la raíz del repositorio y usando el entorno virtual configurado:

```sh
.venv/bin/python -m scripts.create_admin
```

Se solicita email, contraseña oculta y confirmación oculta. No acepta argumentos,
stdin redirigido ni el fallback de getpass que podría mostrar la contraseña. No poner
credenciales en comandos, variables de shell, archivos, capturas ni documentación.
Evitar terminales con grabación de entrada o sesiones compartidas.

El email usa la misma normalización/validación que login: strip/lower, ASCII, un @,
límites existentes de longitud y ausencia de espacios. La política de contraseña
es exactamente la del helper existente: mínimo 12 caracteres, máximo 1024 bytes UTF-8,
sin recortar ni normalizar. Se reutiliza Argon2id; no se almacena texto plano.

Los mensajes no muestran email, contraseña, hash ni parámetros de errores SQL.
Salida 0 significa creación confirmada; 1 indica rechazo/error/cancelación; 2 indica
uso incorrecto, terminal no interactiva o confirmación diferente. No hay modo force.

## Atomicidad y ejecuciones repetidas

Una transacción READ COMMITTED toma `SHARE ROW EXCLUSIVE` sobre users antes de
comprobar admins y emails. Esto serializa bootstraps y bloquea INSERT/UPDATE/DELETE
concurrentes durante ese breve tramo, incluso si no usan advisory locks. El hash
se calcula antes del lock. Se acotan esperas a 5 segundos y sentencias a 15 segundos.
La transacción exige READ COMMITTED para leer el resultado del escritor anterior
tras esperar por el lock. Con otro aislamiento se rechaza sin insertar.

Si existe cualquier admin —active, disabled o locked— se aborta. Si el email ya
pertenece a cualquier usuario, se aborta sin promoverlo ni modificarlo. El índice
único existente añade protección. Un error revierte la inserción y solo se anuncia
éxito después de confirmar la transacción.

Ante una interrupción de red durante commit, el cliente podría no conocer el resultado:
comprobar el estado antes de reintentar. Un reintento nunca crea un segundo admin si
el primero quedó confirmado. El mecanismo comprueba administradores existentes; no
mantiene una marca irreversible si un operador de DB elimina manualmente todas las
cuentas admin. No borrar admins para volver a habilitar el bootstrap.

## Si ya existe un administrador

No alterar su estado ni borrar cuentas para forzar este comando. Usar el acceso
administrativo existente. Si se perdió, hace falta un procedimiento de recuperación
supervisado, fuera de este bootstrap; todavía no hay recuperación automática.

La futura administración normal deberá autenticar al administrador, autorizar cada
operación, auditarla y validar los vínculos de partners/operators. Podrá reutilizar
validación de email y hashing, pero no el servicio create_first_admin ni su excepción
de acceso inicial. Crear otros administradores exige esa vía autorizada futura.

## Verificación sin divulgar credenciales

Después de la ejecución real, iniciar sesión con un cliente confiable mediante
POST /auth/login y consultar GET /auth/me con Bearer. No imprimir ni registrar el
token. /auth/me debe indicar role=admin y traveler_id/partner_id nulos. El proveedor
obtiene estos datos de PostgreSQL, comprueba sesión y token_version, y las políticas
comerciales existentes habilitan los permisos administrativos.

Puede verificarse estructura e integridad sin mostrar datos personales con:

```sh
.venv/bin/python -m scripts.audit_identity
```

## Producción

Ejecutar una única vez desde una terminal administrativa autorizada, en el entorno
correcto, con conectividad y configuración de DB suministradas por la infraestructura.
Verificar antes que las migraciones 003/004 estén aplicadas; no modificarlas ni ejecutar
la herramienta como parte del arranque, despliegue automático o healthcheck.
La cuenta de DB debe tener permisos para la inserción y el lock requerido. Planificar
el breve bloqueo de escrituras de identidad. Usar TLS para el acceso posterior a la API.
No hacen falta migraciones nuevas ni cambios de JWT_SECRET para este bootstrap.

## Pruebas

```sh
.venv/bin/python -m pytest -q tests/test_admin_bootstrap.py tests/test_identity_auth.py
```

La base de pruebas debe disponer del esquema vigente. El fixture crea tablas TEMP
de identidad vacías, con restricciones e índices copiados y triggers de users;
no copia usuarios, hashes ni sesiones reales. Las inserciones permanecen dentro de
transacciones force_rollback. El lock de bootstrap se comprueba en pg_locks sobre
la tabla temporal, sin bloquear al administrador real. No se ejecuta el CLI interactivo para crear cuentas reales.
Se prueban creación, roles/vínculos, hashing, normalización, validaciones, duplicados,
admins inactivos, rollback, exclusión de escritores concurrentes, terminal insegura,
privacidad y recorrido completo de login, sesión, permisos y logout.
