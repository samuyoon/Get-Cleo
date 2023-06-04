# First stage: Python 'builder' stage (pull in dependencies, compile byte code)
FROM python:3.8-slim-buster AS builder

# Create and change to app directory
WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Second stage: Setup for production
FROM python:3.8-slim-buster AS production

WORKDIR /app

# Copy built libraries from builder stage
COPY --from=builder /usr/local/lib/python3.8/site-packages/ /usr/local/lib/python3.8/site-packages/

# Copy app
COPY . /app

# Set the entrypoint
CMD ["python3", "app.py"]
