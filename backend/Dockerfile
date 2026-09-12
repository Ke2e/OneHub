# ============ OneHub Backend 镜像 ============
# 构建 → uv sync（锁文件 frozen，W1 不含 dev 组）→ 运行 uvicorn
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/uv/bin:$PATH"

WORKDIR /app

# uv 二进制来自官方镜像（与本地 uv 0.12 锁文件兼容，uv.lock 版本字段约束）
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uv/bin/uv

# 先单独拷贝依赖清单，利用 Docker 层缓存避免依赖未变时重复 sync
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 业务代码随后拷贝（依赖层可被上游镜像缓存复用）
COPY app ./app

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]