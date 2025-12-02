# ============================
# Imagen base
# ============================
FROM python:3.11-slim

# Evitar archivos .pyc y tener logs sin buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Crear directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema mínimas (si necesitas algo más, aquí va)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# ============================
# Dependencias de Python
# ============================

# Copiamos solo requirements primero para aprovechar cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ============================
# Copiar código de la app
# ============================

COPY . .

# ============================
# Variables de entorno por defecto
# (En producción las sobreescribes con Railway/VPS)
# ============================

ENV APP_ENV=production \
    APP_DEBUG=false

# ============================
# Comando por defecto: uvicorn
# ============================

CMD ["uvicorn", "miniWMS:app", "--host", "0.0.0.0", "--port", "8000"]
