FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

COPY hotels.json ./hotels.json

RUN mkdir -p /data && chown -R 10001:10001 /app /data
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
