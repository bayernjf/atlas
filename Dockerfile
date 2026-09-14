# ---- 前端构建：Vite 产出 frontend/dist ----
FROM node:22-slim AS frontend
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- 后端运行：FastAPI 同源托管前端构建产物（17 文档静态托管，12 文档 REST） ----
FROM python:3.11-slim AS runtime
WORKDIR /app

# 依赖单独一层以利用缓存（Demo 不安装 Playwright 浏览器，退款链路无需浏览器）
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV ATLAS_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "atlas.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
