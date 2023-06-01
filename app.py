
from slack_bolt import App
import os
import time
import openai
import requests
import logging
from flask import Flask, request
from slack_bolt.adapter.flask import SlackRequestHandler



flask_app = Flask(__name__)
flask_app.config["PORT"] = int(os.environ.get("PORT", 8080))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
gpt_model = 'gpt-4'

if OPENAI_API_KEY is None:
    raise EnvironmentError("OPENAI_API_KEY not found in environment variables")
else:
    openai.api_key = OPENAI_API_KEY

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

handler = SlackRequestHandler(app)


logger = logging.getLogger(__name__)

class ExcludedFileExtensionError(Exception):
    pass

# dummy function to catch these events in case we want to do something with this
@app.event("file_created")
def handle_file_created_events(body, logger):
    logger.info(body)

def auto_rate_limit_gpt(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except openai.error.RateLimitError as e:
            if kwargs.get('rate_limit_mode','wait'):
                print(f"Hit rate limit error: {e} - waiting 1 minute and rerunning the function")
                time.sleep(60)
                return func(*args, **kwargs)
            else:
                kwargs['model'] = gpt_model
                print(f"Hit rate limit error: {e} - rerunning the function with model: {kwargs['model']}")
                return func(*args, **kwargs)
    return wrapper

def create_slack_post_for_flagged_message(client, channel_id, sender_name, sent_time, has_file=False, file_name="", message_link=""):
    if has_file:
        message_text = f"*ALERT :warning:*\n\nA sensitive file (`{file_name}`) was sent by {sender_name}. You can view and delete the original message here: {message_link}"
    else:
        message_text = f"*ALERT :warning:*\n\nA sensitive message was sent by {sender_name}. You can view and delete the original message here: {message_link}"

    message = {
        "text": message_text,  # Include the text argument
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            }
        ]
    }
    client.chat_postMessage(channel=channel_id, **message)




# Receives the initial MESSAGE
@app.event("message")
def handle_message(event, client, say):
    message_text = event['text']
    if not message_text:
        return  # Skip further processing if message is empty

    user_id = event['user']
    sent_time = event['ts']  # Unix timestamp
    channel_id = event['channel']

    # Get user's real name
    user_info = client.users_info(user=user_id)
    sender_name = user_info['user']['profile']['real_name']

    # Introduce a small delay (e.g., 1 second)
    time.sleep(1)

    try:
        # Get permalink of the message
        permalink_info = client.chat_getPermalink(channel=channel_id, message_ts=sent_time)
        if not permalink_info["ok"]:
            print(f"Failed to retrieve permalink for the message: {permalink_info['error']}")
            return

        message_permalink = permalink_info['permalink']
    except Exception as e:
        print(f"Error occurred while retrieving message permalink: {str(e)}")
        return

    print(f'message text is: {message_text}')
    is_sensitive = is_sensitive_message(message_text)
    print(f'is sensitive is: {str(is_sensitive)}')
    if is_sensitive == 'true':
        create_slack_post_for_flagged_message(client, channel_id, sender_name, sent_time, False, None, message_permalink)
    return is_sensitive




# Passes the MESSAGE to OpenAI to determine if it contains sensitive data
@auto_rate_limit_gpt
def is_sensitive_message(message, model=gpt_model):
    system_message = 'You are a sensitive data identifier. Analyze the following message. If the message contains sensitive data such as private keys, API keys or passwords, return "true". If no sensitive data is found, return "false".'
    user_message = f'This is my message: {message}'
    messages = [{"role": "user", "content": user_message}, {"role": "system", "content": system_message}]
    response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        max_tokens=2000,
        n=1,
        stop=None,
        temperature=.5,
    )

    print('open ai call was just hit')
    openai_response_message = response.choices[0].message['content']
    print(f'open ai response message is: {openai_response_message}')

    if openai_response_message.lower().strip() == 'true':
        return 'true'

    return 'false'

@app.event("file_shared")
def handle_file_shared(payload, client, say):
    file_id = payload["file_id"]
    file_info = client.files_info(file=file_id)
    file_url = file_info["file"]["url_private_download"]
    file_name = file_info["file"]["name"]
    user_id = file_info['file']['user']

    # Use the first channel where the file was shared
    channel_id = file_info['file']['channels'][0] 

    # Get user's real name
    user_info = client.users_info(user=user_id)
    sender_name = user_info['user']['profile']['real_name']

    try:
        # Get the message information
        event_ts = payload["event_ts"]
        message = client.conversations_history(
            channel=channel_id,
            inclusive='true',
            oldest=event_ts,
            limit=1)
        print('message is: ' + str(message))
        message_ts = message["messages"][0]["ts"]
        print('payload is: ' + str(payload))

        # Get permalink of the message
        permalink_info = client.chat_getPermalink(channel=channel_id, message_ts=message_ts)
        if not permalink_info["ok"]:
            print(f"Failed to retrieve permalink for the message: {permalink_info['error']}")
            # Use fallback message with just the timestamp
            message_permalink = f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"
        else:
            message_permalink = permalink_info['permalink']
    except Exception as e:
        print(f"Error occurred while retrieving message permalink: {str(e)}")
        # Use fallback message with just the timestamp
        message_permalink = f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"

    excluded_extensions = [
        ".jpg", ".png", ".jpeg", ".gif", ".bmp", ".ico", ".tif", ".tiff", ".raw",
        ".mp3", ".wav", ".ogg", ".flac", ".aac",
        ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv",
        ".obj", ".fbx", ".dae", ".3ds", ".x3d",
        ".exe", ".dll", ".so", ".bat", ".sh",
        ".db", ".accdb", ".mdb", ".sqlite"
    ]

    file_extension = os.path.splitext(file_name)[1]
    if file_extension in excluded_extensions:
        raise ExcludedFileExtensionError(f"Cannot process file with extension {file_extension}")

    response = requests.get(file_url, headers={"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"})
    file_content = response.text
    print('file content is: ' + file_content)

    is_sensitive = is_sensitive_file(file_name, file_content)
    print('is sensitive is: ' + str(is_sensitive))
    if is_sensitive == 'true':
        create_slack_post_for_flagged_message(client, channel_id, sender_name, message_ts, True, file_name, message_permalink)
    return is_sensitive





@auto_rate_limit_gpt
def is_sensitive_file(file_name, file_contents, model=gpt_model):
    print('OpenAI is analyzing file contents. This may take a few minutes.')
    system_message = 'You are a sensitive data identifier. Analyze the following file contents. If the file contains sensitive data that should not be shared publicly such as, but not limited to, private keys, API keys, pem files, or credit card numbers, return "true". If no sensitive data is found, return "false".'
    user_message = f'This is the contents of the file named "{file_name}":\n{file_contents}'
    messages = [{"role": "user", "content": user_message}, {"role": "system", "content": system_message}]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=.5,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    if openai_response_message.lower().strip() == 'true':
        return 'true'

    return 'false'





@flask_app.route("/_ah/start", methods=["GET"])
def start():
    return {"status": "Healthy"}

@flask_app.route("/_ah/health", methods=["GET"])
def health():
    return {"status": "Healthy"}

# Handle Slack events
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080



# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))  # testing only

