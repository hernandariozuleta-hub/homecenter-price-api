# Homecenter Price API

API web que consulta precios reales de productos en Homecenter Colombia
(homecenter.com.co), reutilizando `homecenter_price.py` sin modificarlo.

- `GET /price?q=taladro percutor bosch` → precio del producto
- `GET /health` → chequeo de salud
- `GET /docs` → documentación interactiva (se genera sola)

---

## 1. Correrlo en tu computador (Visual Studio Code)

### Paso 1 — Abre la carpeta en VS Code
Abre VS Code → `Archivo > Abrir Carpeta...` → selecciona la carpeta `homecenter-api`.

### Paso 2 — Crea un entorno virtual
Abre una terminal dentro de VS Code (`Ctrl + ñ` o `Terminal > Nueva Terminal`) y ejecuta:

**Windows (PowerShell):**
```powershell
py -m venv venv
venv\Scripts\Activate.ps1
```

**Mac / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

Si ves `(venv)` al inicio de la línea de la terminal, quedó activado correctamente.

### Paso 3 — Instala las dependencias
```bash
pip install -r requirements.txt
```

### Paso 4 — Corre la API
```bash
uvicorn main:app --reload
```

Verás algo como `Uvicorn running on http://127.0.0.1:8000`. Abre en el navegador:

- http://127.0.0.1:8000/docs → interfaz interactiva para probar el endpoint
- http://127.0.0.1:8000/price?q=taladro%20percutor%20bosch → respuesta cruda en JSON

`--reload` hace que el servidor se reinicie solo cada vez que guardas un cambio.

---

## 2. Subir el proyecto a GitHub

### Paso 1 — Crea el repositorio en GitHub
Ve a [github.com/new](https://github.com/new), ponle un nombre (ej: `homecenter-price-api`), déjalo público o privado, **no** marques "Add a README" (ya tenemos uno), y dale "Create repository".

### Paso 2 — Sube tu código
En la terminal, dentro de la carpeta `homecenter-api` (con el entorno virtual desactivado o no, da igual):

```bash
git init
git add .
git commit -m "Homecenter Price API"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/homecenter-price-api.git
git push -u origin main
```

Reemplaza `TU-USUARIO` y el nombre del repo por los tuyos (GitHub te los muestra en la página del repo recién creado, en el botón "Code").

---

## 3. Desplegar en Render (la forma más fácil)

### Opción A — Un clic con el Blueprint (recomendado)
Este proyecto ya incluye `render.yaml`, así que Render puede configurar todo solo:

1. Ve a [dashboard.render.com](https://dashboard.render.com) y crea una cuenta o inicia sesión (puedes usar tu cuenta de GitHub directamente).
2. Clic en **New +** → **Blueprint**.
3. Conecta tu cuenta de GitHub si no lo has hecho, y selecciona el repositorio `homecenter-price-api`.
4. Render detecta automáticamente `render.yaml` y te muestra el servicio a crear (`homecenter-price-api`, plan `free`). Clic en **Apply**.
5. Espera unos 2-3 minutos mientras Render instala las dependencias y arranca el servicio.
6. Cuando termine, Render te da una URL pública como `https://homecenter-price-api.onrender.com`.

Prueba con: `https://homecenter-price-api.onrender.com/price?q=taladro percutor bosch`

### Opción B — Manual (si prefieres no usar el Blueprint)
1. En Render: **New +** → **Web Service**.
2. Conecta el repositorio `homecenter-price-api`.
3. Configura:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: `Free`
4. Clic en **Create Web Service**.

### Nota sobre el plan gratuito de Render
El plan free "duerme" el servicio tras ~15 minutos sin uso; la primera petición después de eso tarda unos 30-50 segundos en responder mientras despierta. Para un uso ocasional (como tool de un agente) esto normalmente es aceptable; si necesitas que responda siempre rápido, tendrías que pasar a un plan pago.

---

## 4. Actualizar el despliegue cuando cambies el código

Cada vez que hagas cambios localmente:

```bash
git add .
git commit -m "descripción del cambio"
git push
```

Render detecta el push a GitHub automáticamente y vuelve a desplegar solo (esto se llama *auto-deploy*, viene activado por defecto).

---

## 5. Habilitar Playwright (opcional, avanzado)

Por defecto el despliegue en Render **no** incluye Playwright (el tercer nivel
de respaldo con navegador real), porque requiere descargar un navegador
Chromium completo durante el build, lo cual es lento y pesado para un plan
gratuito. La API funciona igual con los primeros dos niveles (API de VTEX +
scraping HTML), que cubren la gran mayoría de los casos.

Si de verdad necesitas Playwright en producción:

1. Descomenta las líneas de `playwright` en `requirements.txt`.
2. Cambia el **Build Command** en Render a:
   ```
   pip install -r requirements.txt && playwright install --with-deps chromium
   ```
3. Cambia la variable de entorno `ENABLE_PLAYWRIGHT` a `true` (Render → tu servicio → **Environment**).
4. Considera pasar a un plan pago de Render: el navegador headless consume bastante más RAM de la que da el plan free.

---

## Estructura del proyecto

```
homecenter-api/
├── main.py                # API FastAPI (endpoints /price, /health)
├── homecenter_price.py    # script original, sin modificar
├── requirements.txt       # dependencias Python
├── render.yaml            # configuración de despliegue en Render
├── .gitignore
└── README.md
```

## Limitaciones conocidas

- No hay API oficial de Homecenter: esta usa el endpoint público de VTEX y,
  como respaldo, scraping del sitio. Si Homecenter cambia su plataforma o
  bloquea el acceso automatizado, la API puede dejar de encontrar resultados.
- Revisa los Términos de Uso de homecenter.com.co antes de un uso intensivo;
  no hagas demasiadas solicitudes por segundo.
- Este proyecto no se pudo probar en vivo contra homecenter.com.co ni contra
  Render desde el entorno donde fue generado (sin acceso de red a esos
  dominios). Pruébalo tú mismo antes de usarlo en producción.
