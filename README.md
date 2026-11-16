# Sistema Web de Supermercado

Prototipo académico desarrollado por **Javier Vasquez y Christian Jimenez** para facilitar la consulta y localización de productos dentro de un supermercado. Permite conocer sus precios, disponibilidad y pasillo, junto con una fotografía referencial de ubicación.

El catálogo es público. El mantenimiento utiliza cuentas de Reponedor y Jefatura, con permisos verificados en el servidor.

## Tecnologías

- Python 3.14.7 y Django 6.1.1.
- MySQL 8.4 y controlador mysqlclient.
- Bootstrap 5.3.8 local, HTML, CSS y JavaScript.
- Pillow para imágenes, python-dotenv para variables de entorno y tzdata en Windows.

Las dependencias están fijadas en `requirements.txt`. La aplicación utiliza español de Chile y la zona horaria `America/Santiago`.

## Funcionalidades

| Perfil | Funciones |
| --- | --- |
| Cliente | Búsqueda por nombre o marca, hasta tres sugerencias, categorías y carruseles, ordenamiento por nombre A–Z o precio ascendente, fichas con precio en CLP y stock, pasillo, fotografía y fecha de ubicación, productos por pasillo y plano general cuando esté configurado. |
| Reponedor | Acceso por RUT y contraseña, registro de productos pendientes sin precio, actualización de stock, pasillo y fotografía, y consulta de última actualización de ubicación. |
| Jefatura | Revisión, aprobación o rechazo de pendientes; sugerencia editable de precio desde referencias CSV; actualización de precio normal y stock; desactivación lógica y consulta de última actualización de ubicación. |
| Django Admin | Gestión de cuentas y perfiles, categorías y pasillos, consulta e importación de precios de referencia. Estas operaciones están reservadas a superusuarios activos. |

Solo los productos Activos son públicos. Con stock cero permanecen visibles y muestran **Sin stock**. Las categorías y pasillos vacíos siguen disponibles.

Actualmente no incluye gestión de ofertas ni horarios de atención. Tampoco realiza ventas, compras, reservas o pagos.

## Instalación en Windows

### 1. Requisitos y clonación

Instala Python 3.14.7 de 64 bits, MySQL Community Server 8.4 y Git. Mantén el servicio MySQL en ejecución. Sustituye la URL del ejemplo por la del repositorio publicado:

```powershell
git clone https://github.com/javier1526/Supermercado-web.git
Set-Location sistema-web-supermercado
python --version
```

### 2. Entorno virtual y dependencias

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --only-binary=:all: -r requirements.txt
python -m pip check
```

Si PowerShell bloquea la activación, utiliza `.\.venv\Scripts\python.exe` en lugar de `python` en los siguientes comandos. No necesitas cambiar la política de ejecución del equipo.

### 3. MySQL

Accede localmente con una cuenta administradora. El parámetro `--password` solicita la clave sin incluirla en el comando:

```powershell
mysql --host=127.0.0.1 --user=root --password
```

Para una instalación nueva:

```sql
CREATE DATABASE IF NOT EXISTS supermercadoweb CHARACTER SET utf8mb4;
CREATE USER 'supermercado_app'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
ON supermercadoweb.* TO 'supermercado_app'@'127.0.0.1';
```

Guarda de forma privada la contraseña generada por MySQL. Django utiliza esta cuenta exclusiva; root se reserva para tareas administrativas. Si el usuario ya existe, utiliza sus credenciales vigentes en lugar de repetir `CREATE USER`.

### 4. Variables de entorno

Solo al configurar una instalación nueva:

```powershell
Copy-Item .env.example .env
python -c "from dotenv import set_key; from secrets import token_urlsafe; set_key('.env', 'DJANGO_SECRET_KEY', token_urlsafe(48))"
```

Completa `.env` localmente en UTF-8:

| Variable | Valor |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clave privada generada con el comando anterior. |
| `DJANGO_DEBUG` | `0` por defecto; `1` solo para depuración local. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` para desarrollo local. |
| `MYSQL_NAME` | `supermercadoweb`. |
| `MYSQL_USER` | Usuario exclusivo de la aplicación. |
| `MYSQL_PASSWORD` | Contraseña privada del usuario MySQL. |
| `MYSQL_HOST` | `127.0.0.1`. |
| `MYSQL_PORT` | `3306`. |

Para escribir valores con caracteres especiales sin errores de escape, puedes usar `dotenv.set_key` desde una consola Python con `getpass`, que solicita el valor sin mostrarlo:

```python
from getpass import getpass
from dotenv import set_key
set_key(".env", "MYSQL_PASSWORD", getpass("Contraseña MySQL: "))
```

Las variables del proceso tienen prioridad sobre el archivo. `.env` no se publica; `.env.example` se conserva sin secretos.

### 5. Migraciones y superusuario

```powershell
python manage.py check --database default
python manage.py migrate
python manage.py createsuperuser
```

El último comando solicita las credenciales interactivamente. El superusuario administrativo puede utilizar un nombre convencional y no necesita un perfil de tienda.

### 6. Ejecución local

```powershell
python manage.py runserver 127.0.0.1:8000 --insecure
```

La opción `--insecure` sirve los recursos estáticos locales con `DEBUG=0` durante la demostración. Este servidor es para desarrollo local; publicar el código en GitHub no despliega la aplicación en Internet.

| Acceso | Dirección |
| --- | --- |
| Cliente | http://127.0.0.1:8000/ |
| Categorías | http://127.0.0.1:8000/categorias/ |
| Ubicación, plano y pasillos | http://127.0.0.1:8000/ubicacion/ |
| Personal | http://127.0.0.1:8000/personal/ |
| Django Admin | http://127.0.0.1:8000/admin/ |

## Datos iniciales

### Supermercado, categorías y pasillos

En una base recién creada, carga la fixture incluida:

```powershell
python manage.py loaddata datos_minimos_k02
```

Crea un supermercado, una categoría inicial y los pasillos 1 y 2; no crea cuentas ni productos. Úsala solo sobre una base nueva: recargar una fixture con identificadores existentes puede sobrescribir datos.

En **Admin → Catalogo → Categorias** y **Pasillos** puedes mantener estos datos. Los pasillos se asocian al primer supermercado por ID, también utilizado por las consultas públicas. Los registros utilizados están protegidos contra eliminación. Un pasillo con ubicaciones asociadas no puede renumerarse administrativamente; los traslados corresponden al Reponedor.

### Cuentas de personal

En **Admin → Usuarios → Añadir usuario**, completa el RUT en Nombre de usuario, el nombre del trabajador, su perfil y la contraseña confirmada. Repite el procedimiento con distintos RUT para Reponedor y Jefatura.

Se normalizan los RUT con o sin puntos y guion y se guardan contraseñas mediante hash. Las cuentas ficticias no exigen validación matemática del dígito verificador. Los trabajadores no reciben privilegios de Admin automáticamente; ingresan por `/personal/`.

### Precios de referencia

El [CSV ficticio de ejemplo](ejemplos/precios_referencia_ficticios.csv) contiene 12 referencias sin datos personales. Un superusuario puede importarlo desde **Admin → Catalogo → Precio referencias → Importar CSV**. El formulario admite hasta **1 MiB** y muestra las referencias nuevas y existentes procesadas.

También puedes utilizar la terminal:

```powershell
python manage.py importar_precios ejemplos/precios_referencia_ficticios.csv
```

El archivo debe estar en UTF-8, con o sin BOM, y tener exactamente el encabezado `codigo_barras,precio_referencia`. Los códigos son texto de hasta 32 caracteres; los precios, enteros positivos en CLP sin separadores ni decimales.

Se valida todo antes de guardar y la carga es atómica. Ante un error, corrige el archivo y vuelve a seleccionarlo. Los códigos nuevos crean referencias y los existentes actualizan su referencia sin duplicarse. La importación no crea productos ni modifica precios definitivos.

Jefatura puede conservar, borrar o reemplazar la sugerencia y aprobar con un precio válido. Rechazar no requiere precio ni motivo.

### Productos, fotografías y plano

Registra productos como Reponedor y apruébalos como Jefatura para incorporarlos al catálogo. Las fotografías subidas se almacenan en `media/` y deben incorporarse en cada instalación.

El plano es opcional: coloca una imagen real en `catalogo/static/catalogo/planos/` y configura `Supermercado.plano_general`, mediante el ORM en `python manage.py shell`, con una ruta estática relativa como `catalogo/planos/plano_general.png`. No admite rutas absolutas ni URLs externas. Sin recurso válido se muestra **Plano general no disponible por el momento**.

## Pruebas automatizadas

Django crea y elimina una base separada: `MYSQL_NAME` seguido de `pruebas`. Con la configuración de ejemplo será `supermercadowebpruebas`. Concede los permisos necesarios desde una cuenta administradora de MySQL:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
ON supermercadowebpruebas.* TO 'supermercado_app'@'127.0.0.1';
```

```powershell
python manage.py test --noinput
python manage.py check --database default
python manage.py makemigrations --check --dry-run
```

El esquema reservado para pruebas no debe contener información que necesites conservar. Si cambias `MYSQL_NAME`, ajusta los permisos del esquema correspondiente. Las fotografías y cargas CSV de prueba utilizan almacenamiento temporal.

## Estructura del proyecto

```text
catalogo/              Productos, categorías, ubicación, referencias y consultas
  fixtures/            Datos mínimos para una instalación nueva
  management/commands/ Importador CSV
  migrations/          Migraciones del catálogo
  static/              Recursos públicos y carpeta del plano
  templates/           Pantallas públicas, de gestión y de Admin
personal/              Autenticación por RUT, perfiles y autorización
  migrations/          Migraciones del personal
  static/              Bootstrap local y estilos compartidos
  templates/           Acceso, paneles y componentes visuales
supermercado_web/      Configuración, rutas, ASGI y WSGI
ejemplos/              CSV ficticio para probar la importación
manage.py              Comandos de Django
requirements.txt       Dependencias fijadas
.env.example           Variables de entorno sin credenciales
```

Las pruebas están en los módulos `tests.py` y `test_*.py` de las aplicaciones. Se incluyen código, migraciones, fixtures y recursos estáticos; quedan fuera las credenciales, fotografías locales, documentación interna y archivos generados.
