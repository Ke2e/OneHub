# ============ OneHub Backend 镜像 ============
# 构建 → uv sync（锁文件 frozen，W1 不含 dev 组）→ CMD 拉起 uvicorn
# T023（US5 一键环境）：迁移与种子文件打进镜像（自包含），启动命令由 compose 覆盖
#   = sh 串「alembic upgrade head && seed.py（幂等，T007）&& exec uvicorn」
# build context = 仓库根（docker-compose.yml api.build.context: .），本文件位于 backend/ 下，
# 故 COPY 源路径以 backend/ 为前缀；scripts/seed.py 位于仓库根 scripts/ 下
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/uv/bin:$PATH"

WORKDIR /app

# uv 二进制来自官方镜像（与本地 uv 0.12 锁文件兼容，uv.lock 版本字段约束）
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uv/bin/uv

# 先单独拷贝依赖清单，利用 Docker 层缓存避免依赖未变时重复 sync
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 业务代码 + Alembic 迁移 + 种子脚本随后拷贝（依赖层可被上游镜像缓存复用）
COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY scripts/seed.py ./scripts/seed.py

EXPOSE 8000

# 启动命令：compose 服务级 command 覆盖此 CMD（迁移 → 幂等种子 → exec uvicorn）
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]