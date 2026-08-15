FROM python:3.12-slim

# uv: fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# WeasyPrint renders the PDF: needs pango/cairo/harfbuzz at runtime, not just at build.
# gdal-bin ships ogr2ogr and postgresql-client ships psql; the layer loaders use both —
# ogr2ogr to read the shapefile, psql to normalise it into layer_features.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi8 shared-mime-info fonts-dejavu-core \
        gdal-bin postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Reliable hot-reload on macOS bind mounts
ENV WATCHFILES_FORCE_POLLING=true

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project || uv sync --no-install-project

COPY . .
RUN uv sync --frozen || uv sync

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
