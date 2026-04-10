FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/

RUN uv sync --no-dev

CMD ["uv", "run", "python", "-m", "solmate_optimizer"]
