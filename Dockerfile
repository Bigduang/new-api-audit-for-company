FROM node:22-bookworm-slim AS admin-frontend

WORKDIR /build/frontend/admin

COPY frontend/admin/package*.json ./
RUN npm ci

COPY frontend/admin ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY token_audit ./token_audit
COPY --from=admin-frontend /build/token_audit/admin_dist ./token_audit/admin_dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "token_audit.main:app", "--host", "0.0.0.0", "--port", "8000"]
