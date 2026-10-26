# Playwright base image ships Chromium + all system deps
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy
WORKDIR /app
COPY pyproject.toml README.md ./
COPY scholar2api ./scholar2api
RUN pip install --no-cache-dir .
ENV SCHOLAR2API_USER_DATA_DIR=/data/browser-profile
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "scholar2api.service:app", "--host", "0.0.0.0", "--port", "8000"]
