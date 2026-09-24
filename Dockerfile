# Dashboard + scheduled polling in one container (Railway or any Docker host).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev \
 && uv run playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

EXPOSE 8501
CMD ["sh", "scripts/start_server.sh"]
