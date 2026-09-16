# Imagen oficial de Microsoft con Playwright + Chromium + todas las
# dependencias de sistema YA instaladas. Esto evita el problema de que
# Render (en su runtime nativo de Python) no da permisos de root para que
# `playwright install --with-deps` instale librerías del sistema.
#
# IMPORTANTE: la versión de la imagen (v1.47.0-jammy) debe coincidir con la
# versión de playwright en requirements.txt (playwright==1.47.0). Si algún
# día actualizas una, actualiza también la otra.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render inyecta el puerto real en la variable de entorno PORT.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
