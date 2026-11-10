# ---- 前端构建：Vite 产出 frontend/dist ----
FROM node:22-slim AS frontend
WORKDIR /app/frontend
# registry.npmjs.org drops TLS connections at random from this network; corepack
# fetches pnpm itself and ignores npm_config_registry, so both switches are set.
ENV npm_config_registry=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- 后端运行：FastAPI 同源托管前端构建产物（17 文档静态托管，12 文档 REST） ----
FROM python:3.11-slim AS runtime
WORKDIR /app

# 依赖单独一层以利用缓存（Demo 不安装 Playwright 浏览器，退款链路无需浏览器）
# D79：后端依赖以 uv.lock 为唯一事实源，生产镜像同样按锁装（docs/14 D79、docs/10 §2）——
# pip install . 会抽最新，PR #152 本机/CI 双解漂移（starlette 1.6/1.8，404→405）的根治须覆盖镜像。
# ghcr.io is unreachable on this network, so uv comes from PyPI instead of being
# copied out of the astral-sh image. Same version, same binary.
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv==0.11.16
# uv.lock pins model-hub-client as a git dependency pointing at GitHub; the slim
# base image ships no git executable, so uv sync fails without this.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
# only-system：直接用基础镜像 python:3.11-slim 的解释器，不在镜像内再下载托管 Python；
# PATH 前置 venv，入口脚本的 python -m scripts.ops.* 与 CMD uvicorn 均解析到 /app/.venv/bin。
ENV UV_PYTHON_PREFERENCE=only-system \
    PATH="/app/.venv/bin:$PATH"
RUN uv sync --locked --no-editable --no-cache

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
