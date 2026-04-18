FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg2 ca-certificates \
    libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2t64 libxshmfence1 libx11-xcb1 fonts-liberation \
    libatk1.0-0t64 libatspi2.0-0t64 libcups2t64 libdbus-1-3 \
    libxext6 libxfixes3 libxi6 libxrender1 libxtst6 \
    libxkbcommon0 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

RUN playwright install chromium

COPY backend/ /app/backend/

RUN mkdir -p /app/data /app/results /app/data/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
