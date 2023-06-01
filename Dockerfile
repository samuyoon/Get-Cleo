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

# CLI deploy command below:
# gcloud run deploy cleo-slack-service --platform managed --region us-central1 --source=/Users/samyoon/Development/slack_apps/alert_bot --set-env-vars OPENAI_API_KEY=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,SLACK_BOT_TOKEN=xoxb-5315816251861-5304247088775-R0oJRp6aARrJtXWMVhYi1p3U,SLACK_SIGNING_SECRET=a2c8be465b37ce852415e7eb14c50f8f

