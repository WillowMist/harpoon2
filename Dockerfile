# Use Ubuntu 24.04 as base image
FROM ubuntu:24.04

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3-pip \
    python3-venv \
    redis-server \
    git \
    curl \
    wget \
    build-essential \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Create application directories
RUN mkdir -p /opt/harpoon2 /data /var/log/harpoon2

# Set working directory
WORKDIR /opt/harpoon2

# Copy requirements.txt
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Create symbolic link for settings.py from /data to the Django settings location
RUN ln -sf /data/settings.py harpoon2/settings.py && \
    chmod +x /opt/harpoon2/entrypoint.sh

# Create data directory structure
RUN mkdir -p /data && \
    chown -R nobody:nogroup /data /opt/harpoon2 /var/log/harpoon2

# Expose ports
# 8000 - Django development server
# 6379 - Redis
EXPOSE 8000 6379

# Set user to run as non-root
USER nobody

# Entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["start"]
