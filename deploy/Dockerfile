# Core app image: full pipeline (seasonal backend) + API/dashboard + MCP.
# Chronos-2 GPU forecasting is an opt-in overlay (see requirements-forecast.txt);
# it isn't baked in here to keep the image small and CPU-portable.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq5 curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-forecast.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Opt-in Chronos-2 (torch + chronos) overlay for GPU forecasting.
ARG INSTALL_FORECAST=false
RUN if [ "$INSTALL_FORECAST" = "true" ]; then \
      pip install --no-cache-dir -r requirements-forecast.txt ; fi

# code + data are bind-mounted by docker-compose (rsync'd to the host), so no COPY here.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8900 8901
ENTRYPOINT ["/entrypoint.sh"]
