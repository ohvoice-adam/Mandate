FROM python:3.11-slim

# Install system deps + postgresql-client-18 from the official pgdg repo
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnupg \
        curl \
        ca-certificates \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && . /etc/os-release \
    && echo "deb https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh

ENV FLASK_APP=app

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
