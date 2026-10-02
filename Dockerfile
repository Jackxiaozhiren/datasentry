# DataSentry 服务镜像（Step 26）：REST API + Web UI 一键启动
# 运行：docker compose up --build  → http://localhost:8000/ui/
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages ./packages
COPY src ./src
# D6-02：--locked 使 manifest/lock 不一致时构建失败而非静默通过。
RUN uv sync --locked --no-dev

FROM python:3.12-slim
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH
# D5-01：容器 netns 内须绑 0.0.0.0 才可被宿主端口映射到达；
# 对外收敛由 compose 侧 127.0.0.1:8000:8000 完成。
# 行 r3 之后这是「有条件的默认」：非回环绑定要求 DATASENTRY_API_TOKEN，
# 否则进程启动即拒绝并打印可操作的提示（不是 warning）。逃生阀
# DATASENTRY_ALLOW_INSECURE_BIND=1 明确认下面向网卡的暴露面。
ENV DATASENTRY_HOST=0.0.0.0
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/packages /app/packages
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
USER 10001
CMD ["datasentry-server"]
