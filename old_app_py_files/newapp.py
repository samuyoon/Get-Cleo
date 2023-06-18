from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import requests
from slack_bolt import App
from requests.exceptions import RequestException
from cryptography.fernet import Fernet
from flask import Flask, redirect, request, jsonify, make_response
from util import create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message, generate_app_mention_reply, generate_treat_reply
from slack_bolt.adapter.flask import SlackRequestHandler
import logging
import base64
import threading 
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime
from old_app_py_files.slackevents import get_app
from slack_bolt.adapter.socket_mode import SocketModeHandler


# Intro message DM'd to user who installed Cleo-- note  that only "Woof! I'm Cleo :dog:" must remain the same to catch feature_vote reactions
intro_message = ('*Woof! I\'m Cleo :dog:*\n\n'
                 '*Get Started:*\n'
                 'Say "Hey Cleo" and ask me anything. Like, “hey cleo what’s it like being a purple dog?”\n\n'
                 '*Chat with Cleo:*\n'
                 'Start any message with "Hey Cleo" in public channels or our DMs. '
                 'I alert you of any accidentally shared sensitive data in public channels.\n\n'
                 '*Upcoming Tricks:*\n'
                 'What should I learn next?\n'
                 ':one: Auto-document creation from channels <https://getcleo.io/feature-votes?choice=option1>\n\n'
                 ':two: Scheduled Slack messages <https://getcleo.io/feature-votes?choice=option2>\n\n'
                 ':three: Private /catchmeup summaries <https://getcleo.io/feature-votes?choice=option3>\n\n'
                 'Got a new trick in mind? Use /suggestnewtrick!')


# Setup logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask App
flask_app = Flask(__name__)
flask_app.config["PORT"] = int(os.environ.get("PORT", 8080))

# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)



# @flask_app.route("/slack/events", methods=["POST"])
# def slack_events():
#     # Get the team_id from the Slack event payload
#     team_id = request.json.get("team_id")
#     logging.info(f"Received Slack event for team_id: {team_id}")
    
#     app = get_app(team_id)
    
#     handler = SlackRequestHandler(app)
#     return handler.handle(request)

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
        response = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": os.getenv("MY_SLACK_CLIENT_ID"),
                "client_secret": os.getenv("MY_SLACK_CLIENT_SECRET"),
                "code": code,
            },
        )

        response.raise_for_status()

    except RequestException as e:
        print(f"Error during request to Slack API: {e}")
        return {'message': 'Failed to retrieve the access token from Slack.'}, 500

    slack_data = response.json()
    logging.info(f"Received slack oauth response: {slack_data}")

    if not slack_data.get("ok", False):
        error_message = slack_data.get('error', 'No error message returned from Slack.')
        return {'message': f'Slack API returned an error: {error_message}'}, 500

    access_token = slack_data.get("access_token")
    if not access_token:
        error_message = slack_data.get('error', 'No access token returned from Slack.')
        return {'message': f'Slack API returned an error: {error_message}'}, 500

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

    bot_user_id = slack_data.get('bot_user_id')
    if not bot_user_id:
        return {'message': 'Slack API returned an error: No bot_user_id.'}, 500

    # Create new instance of slack WebClient with access token
    slack_client = WebClient(token=access_token)
    channel_id = None


    try:
        response = slack_client.chat_postMessage(channel=user_id, text=intro_message)
        logging.info(f'slack_client.chat_postMessage(channel={user_id}, text={intro_message})')
        intro_message_ts = response["ts"]

        # Get a list of all public channels
        public_channels_response = slack_client.conversations_list(types="public_channel")
        logging.info(f'slack_client.conversations_list(types="public_channel") resulted in: {public_channels_response}')

        # If the request was successful, extract the channels
        if public_channels_response.get('ok'):
            public_channels = public_channels_response['channels']

            # Initialize an empty list to hold channel IDs
            public_channel_ids = []

            # Add bot to all public channels
            for channel in public_channels:
                public_channel_ids.append(channel['id'])  # Store the channel ID
                try:
                    # Get the list of members of the channel
                    channel_members = slack_client.conversations_members(channel=channel["id"])["members"]

                    # If the bot is not already a member of the channel, join it
                    if bot_user_id not in channel_members:
                        response_join = slack_client.conversations_join(channel=channel["id"])
                        logging.info(f'slack_client.conversations_join(channel={channel["id"]}) resulted in: {response_join}')
                        
                except SlackApiError as e:
                    logging.error(f"Error adding bot to channel {channel['name']}: {e}")
                    continue

    except SlackApiError as e:
        logging.error(f"Error creating new channel or inviting users: {e}")
        return {'message': f'Failed to create a new channel in Slack or invite users. {e}'}, 500


    # Insert encrypted data into database
    data, count = supabase.table("slack_tokens").insert(
        {
            "slack_user_id": user_id,
            "encrypted_access_token": access_token,
            "team_id": team_id,
            "scope": scope,
            "token_type": token_type,
            "cleo_alerts_channel_id": channel_id,
            "bot_user_id": bot_user_id,
            "install_user_id": user_id,
            "intro_message_ts": intro_message_ts
        }
    ).execute()

    if count == 0:
        return {'message': 'Insertion into database failed.'}, 500

    return {
        'message': 'Success',
        'redirectUrl': 'https://getcleo.io/success'
    }, 200

# receive feedback
@flask_app.route('/suggestnewtrick', methods=['POST'])
def suggest_new_trick():
    data = request.form
    logging.info(f"Received slash command: {data}")

    slack_user_id = data.get('user_id')  # Slack user ID who issued the command
    team_id = data.get('team_id')  # Slack Team ID where the command was issued
    text = data.get('text')  # the text of the user's message
    logging.info(f"text from slash command: {text}")

    # Send an immediate response to acknowledge receipt
    response_payload = {
        "response_type": "ephemeral",
        "text": "Command received! Processing your request..."
    }
    response = make_response(jsonify(response_payload), 200)

    # Fetch access token for the team from Supabase
    sb_slack_tokens_response = supabase.from_("slack_tokens").select("*").eq("team_id", team_id).execute()
    sb_slack_tokens_data = sb_slack_tokens_response.data
    logging.info(f"supabase data for slash command: {sb_slack_tokens_data}")

    if sb_slack_tokens_data:
        latest_record = max(sb_slack_tokens_data, key=lambda record: record.get("created_at"))
        encrypted_access_token = latest_record.get("encrypted_access_token")
    else:
        encrypted_access_token = None

    # Create a client with the access token
    client = WebClient(token=encrypted_access_token)

    # Fetch user's profile to get the real name and email
    try:
        user_profile = client.users_profile_get(user=slack_user_id)
    except SlackApiError as e:
        error_response_payload = {
            "response_type": "ephemeral",
            "text": f"Bad dog! Something went wrong: {e.response['error']}"
        }
        return make_response(jsonify(error_response_payload), 500)

    real_name = user_profile['profile']['real_name']
    email = user_profile['profile']['email']
    logging.info(f"email for slash command: {email}")

  # Insert the feedback into the Supabase "cleo_feedback" table
    insert_data = {
        "real_name": real_name,
        "slack_user_id": slack_user_id,
        "team_id": team_id,
        "email": email,
        "feedback": text
    }
    data, count = supabase.table("cleo_feedback").insert(insert_data).execute()
    logging.info(f"insert response from supabase for cleo feedback table: {data}")

    if count == 0:
        error_response_payload = {
            "response_type": "ephemeral",
            "text": "Bad dog! Something went wrong..."
        }
        logging.info(f"count was == 0 and returned: {count}")

        return make_response(jsonify(error_response_payload), 500)
    else:
        # Reply back to the original slash command with a success message
        success_response_payload = {
            "response_type": "ephemeral",
            "text": "Thank you for your feedback!"
        }
        # client.chat_postMessage(
        #     channel=slack_user_id,
        #     text="Thank you for your feedback!"
        # )
        return make_response(jsonify(success_response_payload), 200)


@flask_app.route('/feature-votes', methods=['POST'])
def store_feature_vote():
    data = request.json
    choice = data.get('choice')
    data = { "voted_feature": choice}
    supabase.table("feature_votes").insert(data).execute()
    return {
        'message': 'Success',
        'redirectUrl': 'https://getcleo.io/feedback-submitted'
    }, 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080



# Maintain a dictionary of running socket mode handlers
socket_mode_handlers = {}

# and run socket 
def run_socket_mode(team_id, team_data):
    logging.info(f"Attempting to run socket mode for team_id: {team_id}")
    if team_id in socket_mode_handlers:
        logging.warning(f"Socket mode handler for team_id {team_id} is already running. No need to start another.")
        return

    app = get_app(team_id, team_data)
    if app:
        logging.info(f"App created for team_id: {team_id}")
        handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
        socket_mode_handlers[team_id] = handler
        logging.info(f"Starting SocketModeHandler for team_id: {team_id}")
        handler.start()
    else:
        logging.warning(f"No app found for team_id: {team_id}. Cannot start SocketModeHandler.")


def run_flask():
    logging.info("Starting Flask...")
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080
    logging.info("Flask is running.")

# Define a global variable to store team data
team_data = {}

# Run Flask and SocketModeHandler in separate threads
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()

    # Execute the Supabase query and store the data in a variable
    logging.info("Retrieving data from Supabase...")
    response = supabase.table('slack_tokens').select('team_id, encrypted_access_token, install_user_id, created_at').execute().data
    data = response.data

    if not data:
        logging.error("Failed to retrieve data from Supabase.")
    else:
        logging.info(f"Data successfully retrieved from Supabase. Received {len(data)} items.")
        for item in data:
            team_id = item['team_id']
            if team_id not in team_data or item['created_at'] > team_data[team_id]['created_at']:
                team_data[team_id] = item
                logging.info(f"New data stored for team_id: {team_id}")



# DEV ONLY
# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))
