# DataSentry 服务镜像（Step 26）：REST API + Web UI 一键启动
# 运行：docker compose up --build  → http://localhost:8000/ui/
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages ./packages
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/packages /app/packages
EXPOSE 8000
CMD ["datasentry-server"]
