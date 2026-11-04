# bookweb：bookengine + deai + exporter + 本地网页。构建上下文是仓库根目录。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/engine

RUN pip install --no-cache-dir "PyYAML>=6" "python-docx>=1"

WORKDIR /app/engine
COPY config/ /app/config/
COPY engine/bookengine/ /app/engine/bookengine/
COPY engine/deai/ /app/engine/deai/
COPY engine/exporter/ /app/engine/exporter/
COPY engine/data/ /app/engine/data/
COPY engine/tools/ /app/engine/tools/

EXPOSE 8787
CMD ["python", "-m", "tools.webui"]
