FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Prevent Python from writing pyc files and enforce unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8501

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose Streamlit port
EXPOSE 8501

# Healthcheck for container stability
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Run the UI
ENTRYPOINT ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]