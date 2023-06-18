
from slack_bolt import App
from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import openai
import requests
import logging
from flask import Flask, redirect, request
from slack_bolt.adapter.flask import SlackRequestHandler
from requests.exceptions import RequestException


# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)

# setup encryption
cipher_key = os.environ.get('FERNET_KEY')
cipher_suite = Fernet(cipher_key)


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

handler = SlackRequestHandler(app) # TODO Make sure this uses an app that is instnatiated with the access token


logger = logging.getLogger(__name__)

class ExcludedFileExtensionError(Exception):
    pass


def get_user_slack_token(team_id):
    # Fetch encrypted access token from Supabase
    data, error = supabase.table("slack_tokens").select("encrypted_access_token").filter("team_id", "eq", team_id).execute()
    if error or not data:
        print(f"Failed to retrieve token for team {team_id}: {error}")
        return None

    # Decrypt token
    encrypted_token = data[0]["encrypted_access_token"]
    decrypted_token = cipher_suite.decrypt(encrypted_token.encode()).decode()
    return decrypted_token

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
	# # Extract team_id from event
    # team_id = event.get('team_id')
    
    # # Get token for team and instantiate app
    # slack_bot_token = get_user_slack_token(team_id)
    # app = App(
    #     token=slack_bot_token,
    #     signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    # )

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

def initialize_slack_app(bot_token):
    app = App(
        token=bot_token,
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    )
    return app

# Handle Slack events
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    payload = request.get_json()
    team_id = payload["team_id"]

    # Retrieve the bot token for the given team ID from the Supabase table
    response = supabase.from_table("slack_tokens").select("bot_token").eq("team_id", team_id).execute()
    encrypted_bot_token = response["bot_token"]

	#decrypt the bot token using fernet key env var
    bot_token = cipher_suite.decrypt(encrypted_bot_token)

    # Initialize the Slack Bolt App with the bot token
    slack_app = initialize_slack_app(bot_token)


    # Return the handler instead of invoking it
    return SlackRequestHandler(slack_app)



@flask_app.route('/exchange-code', methods=['POST'])
def exchange_code():
    data = request.json
    code = data.get('code')

    try:
        # Make a POST request to Slack's API
        response = requests.post('https://slack.com/api/oauth.v2.access', data={
            'client_id': os.getenv('SLACK_CLIENT_ID'),
            'client_secret': os.getenv('SLACK_CLIENT_SECRET'),
            'code': code,
        })

        # Check if the request was successful
        response.raise_for_status()

    except RequestException as e:
        # Log the error (optional)
        print(f"Error during request to Slack API: {e}")
        return {'message': 'Failed to retrieve the access token from Slack.'}, 500

    # Parse the response from Slack
    slack_data = response.json()

    # Check if Slack returned an error
    if not slack_data.get('ok', False):
        error_message = slack_data.get('error', 'No error message returned from Slack.')
        return {'message': f'Slack API returned an error: {error_message}'}, 500

    # Extract the access token from the response
    access_token = slack_data.get('access_token')

    # Encrypt the access token
    cipher_text = cipher_suite.encrypt(access_token.encode())

    # Store the encrypted access token in your database, associated with the user who authenticated
    user_id = slack_data.get('authed_user').get('id')

    team_id = slack_data.get('team', {}).get('id')
    scope = slack_data.get('scope')
    token_type = slack_data.get('token_type')
    bot_user_id = slack_data.get('bot_user_id')

    data, count = supabase.table("slack_tokens").insert({
        "slack_user_id": user_id,
        "encrypted_access_token": cipher_text.decode(),
        "team_id": team_id,
        "scope": scope,
        "token_type": token_type,
        "bot_user_id": bot_user_id,
    }).execute()

	# if no rows were inserted in supabase
    if count==0:
        return {'message': f'Slack Access Code was not successfully saved. Email support@getcleo.io with this error in subject line.'}, 500


    return {
        'message': 'Success',
    	'redirectUrl': 'https://getcleo.io/success'
    }, 200

# if __name__ == "__main__":
#     flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080



if __name__ == "__main__":
    app.start(port=int(os.environ.get("PORT", 3000)))  # testing only

