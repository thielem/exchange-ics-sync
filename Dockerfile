FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Create directory for config
RUN mkdir -p /app/config

# Copy default config file
COPY config.example.yaml /app/config/config.example.yaml

# Set environment variables
ENV CONFIG_PATH=/app/config/config.yaml
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import socket; s = socket.create_connection(('localhost', 8080), timeout=5); s.close()" || exit 1

# Run the application
CMD ["python", "app.py"]
