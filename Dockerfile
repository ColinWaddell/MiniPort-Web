# Multi-stage build: frontend first, then the Python runtime.
# NOTE: verified only for buildability by inspection — this host has no
# Docker engine; see docs/deployment.md ("Docker (optional)").

FROM node:24-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]'
COPY --from=web /build/web/dist ./web/dist
RUN mkdir -p /app/data
VOLUME /app/data
ENV EB200_WEB_HOST=0.0.0.0
ENV EB200_DATA_DIR=/app/data
EXPOSE 8000
USER 1000
CMD ["eb200-server"]