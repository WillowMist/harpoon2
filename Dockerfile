FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh \
    && mkdir -p /data \
    && cp harpoon2/settings_template.py /data/settings.py \
    && chown -R 999:70 /data

EXPOSE 4277 6379

ENTRYPOINT ["./entrypoint.sh"]
CMD ["start"]
