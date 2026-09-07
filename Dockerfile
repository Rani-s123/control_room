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

# uv is installed with pip rather than the curl script so it lands in
# /usr/local/bin. The curl installer puts it under /root/.local, which UID 1000
# cannot read once USER switches below, and `uvx` is how mcp_client starts the
# official ClickHouse MCP server.
RUN pip install --no-cache-dir uv \
    && pip install --no-cache-dir -r requirements.txt

COPY controlroom/ ./controlroom/
COPY sql/ ./sql/
COPY web/ ./web/
COPY data/ ./data/

RUN chown -R user:user /app
USER user

# uvicorn binds the port before the embedded engine seeds itself, so the health
# check passes while the first request is still generating its dataset.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn controlroom.server:app --host 0.0.0.0 --port ${PORT}
