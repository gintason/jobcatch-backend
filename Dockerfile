FROM python:3.12-slim

# GeoDjango needs GDAL/GEOS/PROJ system libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils libproj-dev gdal-bin libgdal-dev libgeos-dev git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Production entry uses gunicorn (WSGI) or uvicorn (ASGI, for Channels).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
