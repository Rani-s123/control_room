FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controlroom/ ./controlroom/
COPY sql/ ./sql/
COPY web/ ./web/
COPY data/ ./data/

ENV PORT=8080
CMD exec uvicorn controlroom.server:app --host 0.0.0.0 --port ${PORT}
