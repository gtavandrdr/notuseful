# Use official Python runtime as a parent image
FROM python:3.9-slim

# Create non-root user
RUN useradd -m -u 1000 botuser

# Set working directory in the container
WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code and database
COPY main.py .
COPY bot_database.db* ./ 2>/dev/null || :
COPY .env .

# Create directory for logs and set permissions
RUN mkdir -p /app/logs && \
    chown -R botuser:botuser /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER botuser

# Run the bot
CMD ["python", "main.py"] 
