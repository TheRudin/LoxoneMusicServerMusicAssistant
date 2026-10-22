FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir .

# Expose Loxone ports:
# 7070 UDP (Discovery)
# 7091 TCP (appHttp)
# 7095 TCP (msHttp)
EXPOSE 7070/udp 7091/tcp 7095/tcp

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "loxone_musicserver.main"]
