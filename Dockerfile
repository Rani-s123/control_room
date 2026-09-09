FROM python:3.12-slim

# Hugging Face Spaces runs every container as UID 1000, so the image creates
# that user and hands it the application directory. Cloud Run and Railway do
# not care who owns the process, which means one image serves all three.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controlroom/ ./controlroom/
COPY sql/ ./sql/
COPY web/ ./web/
COPY data/ ./data/

RUN chown -R user:user /app
USER user

# `uv` is deliberately NOT installed. mcp_client.server_command() prefers `uvx`,
# which would fetch mcp-clickhouse from PyPI on the first request; the package is
# already in requirements.txt, so leaving uvx off PATH makes shutil.which find the
# installed console script instead - no network round trip on the critical path.
#
# uvicorn binds the port before the read path opens, so the platform health check
# passes while the first request is still connecting to ClickHouse.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn controlroom.server:app --host 0.0.0.0 --port ${PORT}
