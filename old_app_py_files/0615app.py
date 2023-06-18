from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import requests
from slack_bolt import App
from requests.exceptions import RequestException
from cryptography.fernet import Fernet
from flask import Flask, redirect, request, jsonify, make_response, after_this_request
from util import create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message, generate_app_mention_reply, generate_treat_reply, gpt_generate_reply, gpt_generate_catchmeup
from slack_bolt.adapter.flask import SlackRequestHandler
import logging
import base64
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime
import threading


# Intro message DM'd to user who installed Cleo-- note  that only "Woof! I'm Cleo :dog:" must remain the same to catch feature_vote reactions
intro_message = ('*Woof! I\'m Cleo :dog:*\n\n'
                 '*Get Started:*\n'
                 'Say "Hey Cleo" and ask me anything. Like, “hey cleo what’s it like being a purple dog?”\n\n'
                 '*Chat with Cleo:*\n'
                 'Start any message with "Hey Cleo" in public channels or our DMs.\n\n'
                 '*Commands Cleo can do:*\n'
                 'I can understand and respond to certain commands. Here are some you can try:\n'
                 '`/catchmeup`: Use this command to get caught up on what\'s been going on in the channel without having to scroll through all of the messages you missed.\n'
                 '`/suggestareply`: Use this command to suggest a reply for a conversation context.\n'
                 '`/suggestnewtrick`: If you have an idea for a new trick I could learn, let me know with this command!\n\n'
                 '*Upcoming Tricks:*\n'
                 'What should I learn next?\n'
                 ':calendar: Conversation Highlights: Cleo could identify and highlight important or significant messages within a conversation. <https://getcleo.io/feature-votes?choice=conversation-highlights|Link>\n\n'
                 ':spiral_note_pad: Automatic Follow-up Reminders: Cleo could passively monitor conversations and automatically remind users to follow up on specific discussions or tasks after a certain period of time. <https://getcleo.io/feature-votes?choice=folowup-remind|Link>\n\n'
                 ':tada: Automated Kudos Generator: Cleo could passively analyze conversations and interactions within channels to recognize when someone deserves recognition or a kudos. <https://getcleo.io/feature-votes?choice=kudos-generator|Link>\n\n'
                 'Got a new trick in mind? Use `/suggestnewtrick`!')







# Setup logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask App
flask_app = Flask(__name__)
flask_app.config["PORT"] = int(os.environ.get("PORT", 8080))

# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)

# Create a global dictionary to store apps
apps = {}

def get_app(team_id):
    # Check if the app for this team is already created
    if team_id in apps:
        return apps[team_id]
    
    # Create the app
    response = supabase.from_("slack_tokens").select("*").eq("team_id", team_id).execute()
    data = response.data
    if data:
        latest_record = max(data, key=lambda record: record.get("created_at"))
        encrypted_access_token = latest_record.get("encrypted_access_token")
        install_user_id = latest_record.get("install_user_id")
    else:
        encrypted_access_token = None
        install_user_id = None

    
    app = App(
        token=encrypted_access_token,
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    )

    @app.event("app_mention")
    def handle_app_mention(payload, client, say, logger):
        logger.info(f"payload text is: {payload}")

        channel_id = payload["channel"]
        text = payload["text"]

        if channel_id:
            # Check if the channel ID represents a direct message
            if channel_id.startswith("D"):
                logger.info(f"DM was detected, channel id was {channel_id}")

                # Retrieve the user ID who mentioned Cleo
                user_id = payload["user"]

                # Open a direct message channel with the user
                response = client.conversations_open(users=[user_id])
                if response["ok"]:
                    channel_id = response["channel"]["id"]
                    logger.info(f"DM was opened, channel id is {channel_id}")
                else:
                    logger.error(f"Failed to open direct message channel: {response['error']}")

            user_message = text
            reply = generate_app_mention_reply(user_message)
            logger.info(f"reply text is: {reply}")
            say(reply, channel=channel_id)
        else:
            logger.error("Missing 'event' or 'channel' key in payload.")



    @app.event("message")
    def handle_messages(event, client, say, logger):
        # Extract the message text, user id, time of message, and channel id from the event.
        message_text = event['text']
        if not message_text:
            return 

        user_id = event['user']
        sent_time = event['ts']
        channel_id = event['channel']

        # Retrieve user's real name.
        user_info = client.users_info(user=user_id).data
        sender_name = user_info['user']['profile']['real_name']

        logger.info(f'message text is: {message_text}')

        # Check if "hey cleo" or "treat for cleo" is in the message.
        if "hey cleo" in message_text.lower() or "treat for cleo" in message_text.lower():
            # Send a typing indicator to the user.
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Cleo is fetching a response...")

        if "hey cleo" in message_text.lower():
            reply = generate_app_mention_reply(message_text)
            logger.info(f"reply text is: {reply}")
            say(reply, channel=channel_id)
        elif "treat for cleo" in message_text.lower():
            reply = generate_treat_reply(message_text)
            logger.info("Thanks for the treat!")
            say(reply, channel=channel_id)
        else:
            # If the message is neither directed to Cleo or a treat for Cleo, check if it is a sensitive message.
            is_sensitive = is_sensitive_message(message_text)
            logger.info(f'is sensitive is: {str(is_sensitive)}')
            if is_sensitive == 'true':
                try:
                    # Retrieve permalink of the message
                    permalink_info = client.chat_getPermalink(channel=channel_id, message_ts=sent_time)
                    if not permalink_info["ok"]:
                        logger.error(f"Failed to retrieve permalink for the message: {permalink_info['error']}")
                        return

                    message_permalink = permalink_info['permalink']
                except Exception as e:
                    logger.error(f"Error occurred while retrieving message permalink: {str(e)}")
                    return

                create_slack_post_for_flagged_message(client, install_user_id, sender_name, sent_time, False, None, message_permalink)
            else:
                logger.info(f"not sensitive")



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

        response = requests.get(file_url, headers={"Authorization": f"Bearer {encrypted_access_token}"})
        file_content = response.text
        print('file content is: ' + file_content)

        is_sensitive = is_sensitive_file(file_name, file_content)
        print('is sensitive is: ' + str(is_sensitive))
        if is_sensitive == 'true':
            create_slack_post_for_flagged_message(client, install_user_id, sender_name, message_ts, True, file_name, message_permalink)
        return is_sensitive


    @app.event("reaction_added")
    def handle_feature_vote_reaction(client, body, logger):
        logger.info(f"feature vote reaction body is: {body}")
        # Define emoji-feature mapping
        emoji_feature_map = {
            "1️⃣": "Automatically create useful documents based on channel content",
            "2️⃣": "Delay the send of a Slack message to a specific date and time",
            "3️⃣": "Use `/catchmeup` to have Cleo send you (privately) a summary of the discussion ",
        }

        # Get the item.channel and the item.ts from the body
        item = body["event"]["item"]
        channel_id = item["channel"]
        message_ts = item["ts"]

        # Fetch message
        response = client.conversations_history(channel=channel_id, 
                                                inclusive=True, 
                                                latest=message_ts, 
                                                limit=1)
        logger.info(f"associated message text is: {response['messages'][0]['text']}")

        # Check the message to see if it contains the intro message text
        intro_text = "Woof! I'm Cleo :dog:"
        if response["messages"] and intro_text in response["messages"][0]["text"]:
            # Get the reaction and map it to a feature
            reaction = body["event"]["reaction"]
            if reaction in emoji_feature_map:
                voted_feature = emoji_feature_map[reaction]

                # Insert reaction data into "feature_votes" table
                data = {"slack_user_id": body["event"]["user"], 
                        "team_id": body["team_id"], 
                        "voted_feature": voted_feature}
                supabase.table("feature_votes").insert(data).execute()

                # Send a reply to the user
                client.chat_postMessage(channel=channel_id, 
                                    text="It's on Cleo's curriculum! ",
                                    thread_ts=message_ts)

    # End of @app.event("reaction_added")

    # Store the app for future use
    apps[team_id] = app
    
    return app
# End of def get_app(team_id):





@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    # Get the team_id from the Slack event payload
    team_id = request.json.get("team_id")
    logging.info(f"Received Slack event for team_id: {team_id}")
    
    app = get_app(team_id)
    
    handler = SlackRequestHandler(app)
    return handler.handle(request)



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
            "text": "Thank you for your feedback! We're working on it!"
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

# endpoint for slash command for suggestareply feature
@flask_app.route('/suggestareply', methods=['POST'])
def generate_reply():
    data = request.form
    logging.info(f"Received slash command: {data}")

    slack_user_id = data.get('user_id')  # Slack user ID who issued the command
    team_id = data.get('team_id')  # Slack Team ID where the command was issued
    channel_id = data.get('channel_id')  # the ID of the channel where the command was issued
    response_url = data.get('response_url')  # URL to send additional responses

    # Send an immediate response to acknowledge receipt
    response_payload = {
        "response_type": "ephemeral",
        "text": "Cleo is thinking..."
    }
    requests.post(response_url, json=response_payload)

    # Use threading to handle the long-running process in the background
    thread = threading.Thread(target=process_request_and_send_reply, args=(slack_user_id, team_id, channel_id, response_url))
    thread.start()

    return make_response("", 200)

def process_request_and_send_reply(slack_user_id, team_id, channel_id, response_url):
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

    # Fetch user's profile to get the real name of the command issuer
    try:
        user_profile = client.users_profile_get(user=slack_user_id)
        user_real_name = user_profile['profile']['real_name']
    except SlackApiError as e:
        logging.error(f"Bad dog! Something went wrong: {e.response['error']}")

    # Get the last 8 messages from the channel
    try:
        channel_history = client.conversations_history(channel=channel_id, limit=15)
        messages = channel_history.data['messages']
        # Create a dictionary of messages with timestamp as the key
        # Also get the user's real name for each message
        messages_dict = {}
        for msg in messages:
            if 'user' in msg:  # This line ensures you only consider messages with a 'user' field
                try:
                    msg_user_profile = client.users_profile_get(user=msg['user'])
                    msg_user_real_name = msg_user_profile['profile']['real_name']
                    # Store the message along with the user's real name
                    messages_dict[msg['ts']] = {"user": msg_user_real_name, "text": msg['text']}
                except SlackApiError as e:
                    logging.error(f"Bad dog! Something went wrong: {e.response['error']}")
    except SlackApiError as e:
        logging.error(f"Bad dog! Something went wrong: {e.response['error']}")

    # Generate a smart reply using the dictionary of messages and the user_real_name
    smart_reply = gpt_generate_reply(messages_dict, user_real_name)  


    # Insert the generated reply along with the user's real name into the Supabase "catchmeup_replies" table
    insert_data = {
        "slack_user_id": slack_user_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "user_real_name": user_real_name,
        "reply": smart_reply
    }
    execute_result = supabase.table("suggested_replies").insert(insert_data).execute()
    logging.info(f"execute_result: {execute_result}")


    if execute_result.data and len(execute_result.data) > 0 and 'reply' in execute_result.data[0]:
        try:
            response = client.chat_postEphemeral(channel=channel_id, user=slack_user_id, text=smart_reply)
            if response["ok"]:
                logging.info(f"Posted the following as ephemeral message: {smart_reply}")
            else:
                logging.error(f"Failed to send the final response to Slack: {response['error']}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send the final response to Slack: {e}")
    else:
        logging.error("No reply data found in execute_result.data")

# endpoint for slash command for catchmeup feature
@flask_app.route('/catchmeup', methods=['POST'])
def generate_channel_summary():
    data = request.form
    logging.info(f"Received slash command: {data}")

    slack_user_id = data.get('user_id')  # Slack user ID who issued the command
    team_id = data.get('team_id')  # Slack Team ID where the command was issued
    channel_id = data.get('channel_id')  # the ID of the channel where the command was issued
    response_url = data.get('response_url')  # URL to send additional responses

    # Send an immediate response to acknowledge receipt
    response_payload = {
        "response_type": "ephemeral",
        "text": "Cleo is thinking..."
    }
    requests.post(response_url, json=response_payload)

    # Use threading to handle the long-running process in the background
    thread = threading.Thread(target=process_request_and_send_catchmeup, args=(slack_user_id, team_id, channel_id, response_url))
    thread.start()

    return make_response("", 200)

def process_request_and_send_catchmeup(slack_user_id, team_id, channel_id, response_url):
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

    # Fetch user's profile to get the real name of the command issuer
    try:
        user_profile = client.users_profile_get(user=slack_user_id)
        user_real_name = user_profile['profile']['real_name']
    except SlackApiError as e:
        logging.error(f"Bad dog! Something went wrong: {e.response['error']}")

    # Get the last 15 messages from the channel
    try:
        channel_history = client.conversations_history(channel=channel_id, limit=15)
        messages = channel_history.data['messages']
        # Create a dictionary of messages with timestamp as the key
        # Also get the user's real name for each message
        messages_dict = {}
        for msg in messages:
            if 'user' in msg:  # This line ensures you only consider messages with a 'user' field
                try:
                    msg_user_profile = client.users_profile_get(user=msg['user'])
                    msg_user_real_name = msg_user_profile['profile']['real_name']
                    # Store the message along with the user's real name
                    messages_dict[msg['ts']] = {"user": msg_user_real_name, "text": msg['text']}
                except SlackApiError as e:
                    logging.error(f"Bad dog! Something went wrong: {e.response['error']}")
    except SlackApiError as e:
        logging.error(f"Bad dog! Something went wrong: {e.response['error']}")

    # Generate a smart reply using the dictionary of messages and the user_real_name
    smart_reply = gpt_generate_catchmeup(messages_dict, user_real_name)  

    response = client.chat_postEphemeral(channel=channel_id, user=slack_user_id, text=smart_reply)
    if response["ok"]:
        logging.info(f"Posted the following as ephemeral message: {smart_reply}")
    else:
        logging.error(f"Failed to send the final response to Slack: {response['error']}")

    # # Insert the generated reply along with the user's real name into the Supabase "catchmeup_replies" table
    # insert_data = {
    #     "slack_user_id": slack_user_id,
    #     "team_id": team_id,
    #     "channel_id": channel_id,
    #     "user_real_name": user_real_name,
    #     "reply": smart_reply
    # }
    # execute_result = supabase.table("suggested_replies").insert(insert_data).execute()
    # logging.info(f"execute_result: {execute_result}")


    # if execute_result.data and len(execute_result.data) > 0 and 'reply' in execute_result.data[0]:
    #     try:
            
    #     except requests.exceptions.RequestException as e:
    #         logging.error(f"Failed to send the final response to Slack: {e}")
    # else:
    #     logging.error("No reply data found in execute_result.data")

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080


# DEV ONLY
# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))
