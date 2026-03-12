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
# Note: Using --break-system-packages due to PEP 668 in Ubuntu 24.04
# Skip pip upgrade as it conflicts with debian system pip
RUN pip install --break-system-packages -r requirements.txt

# Copy application code
COPY . .

# Copy and setup entrypoint script (before user change)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create data directory structure
RUN mkdir -p /data && \
    chown -R nobody:nogroup /data /opt/harpoon2 /var/log/harpoon2 /entrypoint.sh

# Expose ports
# 8000 - Django development server
# 6379 - Redis
EXPOSE 8000 6379

# Run entrypoint as root (to manage settings symlink and setup)
# The entrypoint script will drop privileges after initialization if needed

ENTRYPOINT ["/entrypoint.sh"]
CMD ["start"]
