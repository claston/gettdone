FROM python:3.12-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Both stages inherit the same resolved Python/Debian base. No compiler is
# required: missing binary wheels must fail the build instead of compiling.
FROM python-base AS dependencies
COPY backend/requirements-runtime.txt /build/requirements-runtime.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: \
        -r /build/requirements-runtime.txt

FROM python-base AS runtime
ENV PATH="/opt/venv/bin:$PATH"
# Production OCR uses AWS Textract. Local OCR remains a development option.
ENV PDF_OCR_ENABLED=false
ARG APP_RELEASE=staging-local
ENV APP_RELEASE=${APP_RELEASE}

WORKDIR /app

COPY --from=dependencies /opt/venv /opt/venv
COPY backend/app ./backend/app
COPY backend/alembic ./backend/alembic
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/scripts/run_alembic_migrations.py ./backend/scripts/run_alembic_migrations.py
COPY frontend ./frontend

EXPOSE 8000

WORKDIR /app/backend
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
