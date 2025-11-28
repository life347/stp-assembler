# Build for amd64 platform for better package support
# Use Python slim image
FROM --platform=linux/amd64 python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required by build123d/OCC
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libxrender1 \
    libxcursor1 \
    libxft2 \
    libxinerama1 \
    libxi6 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY stp_assembler.py /app/
COPY stp_to_dxf_converter.py /app/
COPY server.py /app/

# Make scripts executable
RUN chmod +x /app/stp_assembler.py /app/stp_to_dxf_converter.py /app/server.py

# Create directories for output and uploads
RUN mkdir -p /app/output /app/uploads

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=server.py

# Expose port
EXPOSE 5001

# Default command - run Flask server
CMD ["python", "/app/server.py"]
