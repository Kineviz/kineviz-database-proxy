FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install the local package so optional drivers follow pyproject.toml.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY frontend/dist/ ./src/kineviz_database_proxy/static/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[relational]"

# Create projects.json if it doesn't exist
RUN touch projects.json && \
    mkdir -p /app/config

# Expose port
EXPOSE 9080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9080/health || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "kineviz_database_proxy.main:app", "--host", "0.0.0.0", "--port", "9080"]
