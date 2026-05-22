FROM python:3.11-slim

# Instalar dependencias esenciales del sistema operativo
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar requerimientos usando la caché de capas de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del proyecto al contenedor
COPY . .

# Asegurar permisos de ejecución para el script de ingesta
RUN chmod +x /app/prueba5.py

# El puerto interno del contenedor que Easypanel mapeará hacia el exterior
EXPOSE 8000

# Lanzar FastAPI con Uvicorn en modo producción de alta disponibilidad
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
