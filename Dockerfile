# First stage: Python 'builder' stage (pull in dependencies, compile byte code)
FROM python:3-slim-buster AS builder

# Create and change to app directory
WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --user --no-cache-dir -r requirements.txt

# Second stage: Setup for production
FROM python:3-slim-buster AS production

WORKDIR /app

# # Environmental Variables, You have to set these in Google Cloud Run Configuration
# ENV OPENAI_API_KEY=
# ENV SLACK_BOT_TOKEN=
# ENV SLACK_SIGNING_SECRET=
# ENV PORT=8080

# Copy built libraries from builder stage
COPY --from=builder /root/.local /root/.local

# Copy app
COPY . /app

# Make sure scripts in .local are usable:
ENV PATH=/root/.local/bin:$PATH

# Expose correct port
EXPOSE $PORT

# cmd to launch app when container is run
CMD ["python", "./app.py"]