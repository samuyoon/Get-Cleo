from slack_bolt import App
from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import requests
from requests.exceptions import RequestException
from slack_bolt.request import BoltRequest
from slack_bolt.response import BoltResponse
from slack_sdk.web import WebClient
from cryptography.fernet import Fernet
from typing import Callable

from util import create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message


# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)

# setup encryption
cipher_key = os.environ.get('FERNET_KEY')
cipher_suite = Fernet(cipher_key)

# Middleware for fetching and decrypting bot token
def fetch_bot_token(req: BoltRequest, resp: BoltResponse, next: Callable[[], None]) -> None:
    team_id = req.body.get("team", {}).get("id")
    if team_id:
        response = supabase.from_("slack_tokens")\
                           .select("encrypted_access_token, created_at")\
                           .eq("team_id", team_id)\
                           .order("created_at", ascending=False)\
                           .limit(1).execute()
        encrypted_bot_token = response.get("data", [{}])[0].get("encrypted_access_token")
        bot_token = cipher_suite.decrypt(encrypted_bot_token).decode()
        req.context["bot_token"] = bot_token  # Store the token in context

        # Set the client's token here
        req.context["client"].token = bot_token
    next()  

# Initialize the Slack Bolt App using the decrpted bot token from supabase
app = App(
    # token is not provided here anymore
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
    raise_error_for_unhandled_request=True,
)

app.use(fetch_bot_token)  # Attach the middleware to the app


@app.event("message")
def handle_messages(event, context, say, logger):
    bot_token = context["bot_token"]
    client = WebClient(token=bot_token)
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

@app.event("http")
def start(event, say):
    if event["path"] == "/_ah/start" and event["httpMethod"] == "GET":
        say({"status": "Healthy"})

@app.event("http")
def health(event, say):
    if event["path"] == "/_ah/health" and event["httpMethod"] == "GET":
        say({"status": "Healthy"})

@app.event("http")
def exchange_code(event, say):
    if event["path"] == "/exchange-code" and event["httpMethod"] == "POST":
        data = event["body"]
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
            say({
                'text': 'Failed to retrieve the access token from Slack.',
                'response_type': 'ephemeral'
            })
            raise e  # Raise the exception for logging and debugging

        slack_data = response.json()

        # Check if Slack returned an error
        if not slack_data.get('ok', False):
            error_message = slack_data.get('error', 'No error message returned from Slack.')
            say({
                'text': f'Slack API returned an error: {error_message}',
                'response_type': 'ephemeral'
            })
            return

    print(slack_data)

    # Extract the access token from the response
    access_token = slack_data.get('access_token')
    if not access_token:
        return {'message': f'Access token not found in Slack response.'}, 500

    # Encrypt the access token
    cipher_text = cipher_suite.encrypt(access_token.encode())

    # Store the encrypted access token in your database, associated with the user who authenticated
    authed_user = slack_data.get('authed_user', {})
    user_id = authed_user.get('id')
    scope = authed_user.get('scope')
    token_type = authed_user.get('token_type')
    if not user_id or not scope or not token_type:
        return {'message': f'Missing data in authed_user field.'}, 500

    team = slack_data.get('team', {})
    team_id = team.get('id')
    if not team_id:
        return {'message': f'Missing team id in Slack response.'}, 500

    data, count = supabase.table("slack_tokens").insert({
        "slack_user_id": user_id,
        "encrypted_access_token": cipher_text.decode(),
        "team_id": team_id,
        "scope": scope,
        "token_type": token_type
    }).execute()

    # if no rows were inserted in supabase
    if count==0:
        return {'message': f'Slack Access Code was not successfully saved. Email support@getcleo.io with this error in subject line.'}, 500

    say({
        'text': 'Success! You will be redirected shortly...',
        'response_type': 'in_channel'
    })

if __name__ == "__main__":
    app.start(port=int(os.getenv('PORT', 8080)))



# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))
