from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import requests
from slack_bolt import App
from requests.exceptions import RequestException
from cryptography.fernet import Fernet
from flask import Flask, redirect, request
from util import create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message
from slack_bolt.adapter.flask import SlackRequestHandler
import logging
import base64


# Setup logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask App
flask_app = Flask(__name__)
flask_app.config["PORT"] = int(os.environ.get("PORT", 8080))

# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)

# setup encryption
cipher_key = os.environ.get('FERNET_KEY')
cipher_suite = Fernet(cipher_key)


# Handle Slack events
# Initialize Slack Bolt app globally-- this will be initialized by @flask_app.route("/slack/events", methods=["POST"]) 
# # Define OAuth settings
# oauth_settings = OAuthSettings(
#     client_id=os.environ["SLACK_CLIENT_ID"],
#     client_secret=os.environ["SLACK_CLIENT_SECRET"],
#     installation_store=FileInstallationStore(),
#     state_store=FileOAuthStateStore(expiration_seconds=600),
# )
# app = App(
#     token='dummy',
#     signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
# )
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    global app

    try:
        # Get the team_id from the Slack event payload
        team_id = request.json.get("team_id")
        logging.info(f"Received Slack event for team_id: {team_id}")

        # Query Supabase to get the latest encrypted_access_token for the team
        response = supabase.from_("slack_tokens") \
                           .select("*") \
                           .eq("team_id", team_id) \
                           .execute()

        # Retrieve the encrypted access token from the response
        data = response.data
        if data:
            # Retrieve the encrypted_access_token from the record with the latest created_at timestamp
            latest_record = max(data, key=lambda record: record.get("created_at"))
            encrypted_access_token = latest_record.get("encrypted_access_token")
        else:
            encrypted_access_token = None

        logging.info(f"encrypted_access_token is: {encrypted_access_token}")

        # Update the token in the app's settings
        app = App(
            token=encrypted_access_token,
            signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
        )
       
        handler = SlackRequestHandler(app)

        # Handle the Slack request
        return handler.handle(request)

    except Exception as e:
        # Log the error
        logging.error(f"Error in slack_events function: {str(e)}")
        # Handle the error or re-raise it if necessary
        raise


@app.event("message")
def handle_messages(event, client, say, logger):
    message_text = event['text']
    if not message_text:
        return  # Skip further processing if message is empty

    user_id = event['user']
    sent_time = event['ts']  # Unix timestamp
    channel_id = event['channel']

    # Get user's real name
    user_info = client.users_info(user=user_id).data
    sender_name = user_info['user']['profile']['real_name']

    # Introduce a small delay (e.g., 1 second)
    time.sleep(1)

    try:
        # Get permalink of the message
        permalink_info = client.chat_getPermalink(channel=channel_id, message_ts=sent_time)
        if not permalink_info["ok"]:
            logger.error(f"Failed to retrieve permalink for the message: {permalink_info['error']}")
            return

        message_permalink = permalink_info['permalink']
    except Exception as e:
        logger.error(f"Error occurred while retrieving message permalink: {str(e)}")
        return

    logger.info(f'message text is: {message_text}')
    is_sensitive = is_sensitive_message(message_text)
    logger.info(f'is sensitive is: {str(is_sensitive)}')
    if is_sensitive == 'true':
        create_slack_post_for_flagged_message(client, channel_id, sender_name, sent_time, False, None, message_permalink)
    return is_sensitive

@app.event("file_created")
def handle_file_created_events(body, logger):
    logger.info(body)

class ExcludedFileExtensionError(Exception):
    pass

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


@flask_app.route("/_ah/start", methods=["GET"])
def start():
    return {"status": "Healthy"}

@flask_app.route("/_ah/health", methods=["GET"])
def health():
    return {"status": "Healthy"}

# Flask route for the initial install
# This exchanges the user's initial code from the redirect url for a slack bot access token
# encrypts it and stores it on slack_tokens in supabase
@flask_app.route('/exchange-code', methods=['POST'])
def exchange_code():
    data = request.json
    code = data.get('code')

    try:
        # Make a POST request to Slack's API
        response = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": os.getenv("MY_SLACK_CLIENT_ID"),
                "client_secret": os.getenv("MY_SLACK_CLIENT_SECRET"),
                "code": code,
            },
        )

        # Check if the request was successful
        response.raise_for_status()

    except RequestException as e:
        # Log the error (optional)
        print(f"Error during request to Slack API: {e}")
        return {'message': 'Failed to retrieve the access token from Slack.'}, 500

    slack_data = response.json()

    # Check if Slack returned an error
    if not slack_data.get("ok", False):
        error_message = slack_data.get('error', 'No error message returned from Slack.')
        return {'message': f'Slack API returned an error: {error_message}'}, 500

    # Extract the access token from the response
    access_token = slack_data.get("access_token")
    if not access_token:
        error_message = slack_data.get('error', 'No access token returned from Slack.')
        return {'message': f'Slack API returned an error: {error_message}'}, 500

    # # Encrypt the access token
    # cipher_text = cipher_suite.encrypt(access_token.encode())

    # # Convert the cipher text to a base64 string for storage
    # cipher_text_b64 = base64.b64encode(cipher_text).decode()

    # Validate slack_data and retrieve necessary values
    authed_user = slack_data.get("authed_user")
    if not authed_user:
        return {'message': 'Slack API returned an error: No authed_user.'}, 500

    user_id = authed_user.get("id")
    if not user_id:
        return {'message': 'Slack API returned an error: No user_id.'}, 500

    scope = slack_data.get("scope")
    if not scope:
        return {'message': 'Slack API returned an error: No scope.'}, 500

    token_type = slack_data.get("token_type")
    if not token_type:
        return {'message': 'Slack API returned an error: No token_type.'}, 500

    team = slack_data.get("team")
    if not team:
        return {'message': 'Slack API returned an error: No team.'}, 500

    team_id = team.get("id")
    if not team_id:
        return {'message': 'Slack API returned an error: No team_id.'}, 500

    # Insert encrypted data into database
    data, count = supabase.table("slack_tokens").insert(
        {
            "slack_user_id": user_id,
            "encrypted_access_token": access_token,
            "team_id": team_id,
            "scope": scope,
            "token_type": token_type,
        }
    ).execute()

    # if no rows were inserted in supabase
    if count == 0:
        return {'message': 'Insertion into database failed.'}, 500


    return {
        'message': 'Success',
    	'redirectUrl': 'https://getcleo.io/success'
    }, 200



if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080



# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))
