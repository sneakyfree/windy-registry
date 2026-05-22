FROM python:3.12-slim

WORKDIR /app

# Build args for MF1 /version contract.
ARG COMMIT_SHA=""
ARG BUILD_TIMESTAMP=""
ENV COMMIT_SHA=${COMMIT_SHA}
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}
ENV ENVIRONMENT=production
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN uv pip install --system -e '.[db,auth,stripe,r2]'

EXPOSE 8500

CMD ["uvicorn", "windy_registry.main:app", "--host", "0.0.0.0", "--port", "8500", "--workers", "2"]
