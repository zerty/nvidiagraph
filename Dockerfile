# Use NVIDIA CUDA base image (includes nvidia-smi)
FROM nvidia/cuda:12.5.1-runtime-ubuntu22.04

# Set working directory
WORKDIR /app

# Set default environment variables
ENV HOST=0.0.0.0
ENV PORT=5000
ENV MONITOR_PASSWORD=
ENV MAX_HISTORY=600
ENV SECRET_KEY=nvidia-gpu-monitor-secret-key
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create templates directory if it doesn't exist
RUN mkdir -p /app/templates

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ || exit 1

# Run with Gunicorn + Eventlet (shell form for env var expansion)
CMD ["sh", "-c", "gunicorn --worker-class eventlet --workers 1 --bind $HOST:$PORT --timeout 120 --access-logfile - --error-logfile - wsgi:app"]
