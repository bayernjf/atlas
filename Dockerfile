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

# 迁移 SQL、ops 脚本（入口启动时跑迁移；docs/30 §2）
COPY db ./db
COPY scripts ./scripts
RUN chmod +x ./scripts/ops/docker-entrypoint.sh

ENV ATLAS_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# --workers 1 是**显式写出来的约束**，不是性能选择：多进程会让同一条挂起审批帧在各进程
# 各自续跑一次，下游真实副作用被执行两遍（docs/34 的 2026-09-25 实测表、docs/62 §2 D-6）。
# 入口脚本 scripts/ops/docker-entrypoint.sh 会同时拒绝 ATLAS_WORKERS>1 与命令行 --workers/-w >1。
ENTRYPOINT ["./scripts/ops/docker-entrypoint.sh"]
CMD ["uvicorn", "atlas.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
