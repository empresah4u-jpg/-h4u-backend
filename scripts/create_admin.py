"""Run only as: python -m scripts.create_admin. Never accepts credentials as arguments."""
import getpass
import sys
import warnings

from app.services.admin_bootstrap import BootstrapRefused, create_first_admin
from app.services.email_validation import normalize_email


def main():
    if len(sys.argv) != 1:
        print('Este comando no acepta argumentos. Use python -m scripts.create_admin.', file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print('Se requiere una terminal interactiva segura.', file=sys.stderr)
        return 2
    try:
        email = normalize_email(input('Email del primer administrador: '))
        # Refuse getpass fallback: it could echo the password on an unsafe terminal.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('Contraseña: ')
            confirmation = getpass.getpass('Repita la contraseña: ')
        if password != confirmation:
            print('Las contraseñas no coinciden; operación cancelada.', file=sys.stderr)
            return 2
        create_first_admin(email, password)
    except (BootstrapRefused, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print('Entrada cancelada o terminal insegura; no se completó el bootstrap.', file=sys.stderr)
        return 1
    except Exception:
        # Database exceptions may contain SQL parameters, including hashes. No traceback.
        print('No se pudo completar el bootstrap. Verifique la base de datos y sus permisos.', file=sys.stderr)
        return 1
    finally:
        password = confirmation = None
    print('Primer administrador creado. Verifique el acceso mediante /auth/login y /auth/me.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
