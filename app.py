from cryptography.fernet import Fernet
from supabase import create_client, Client
import os
import time
import requests
from slack_bolt import App
from requests.exceptions import RequestException
from cryptography.fernet import Fernet
from flask import Flask, redirect, request, jsonify, make_response, after_this_request
from util import basically_summarize, create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message, generate_app_mention_reply, generate_treat_reply, gpt_generate_reply, gpt_generate_catchmeup, gpt_generate_doc
from slack_bolt.adapter.flask import SlackRequestHandler
import logging
import base64
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime
import threading
import json
from hashlib import sha256
import hmac
import time



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
                 ':calendar: Conversation Highlights: Cleo could identify and highlight important or significant messages within a conversation.\n\n'
                 ':spiral_note_pad: Automatic Follow-up Reminders: Cleo could passively monitor conversations and automatically remind users to follow up on specific discussions or tasks after a certain period of time.\n\n'
                 ':tada: Automated Kudos Generator: Cleo could passively analyze conversations and interactions within channels to recognize when someone deserves recognition or a kudos.\n\n'
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

        

    @app.event("reaction_added")
    def handle_feature_vote_reaction(client, body, logger):
        logger.info(f"feature vote reaction body is: {body}")
        # Define emoji-feature mapping
        emoji_feature_map = {
            "1️⃣": "Conversation Highlights: Cleo could identify and highlight important or significant messages within a conversation.",
            "one": "Conversation Highlights: Cleo could identify and highlight important or significant messages within a conversation.",
            "2️⃣": "Automatic Follow-up Reminders: Cleo could passively monitor conversations and automatically remind users to follow up on specific discussions or tasks after a certain period of time.",
            "two": "Automatic Follow-up Reminders: Cleo could passively monitor conversations and automatically remind users to follow up on specific discussions or tasks after a certain period of time.",
            "3️⃣": "Automated Kudos Generator: Cleo could passively analyze conversations and interactions within channels to recognize when someone deserves recognition or a kudos.",
            "three": "Automated Kudos Generator: Cleo could passively analyze conversations and interactions within channels to recognize when someone deserves recognition or a kudos.",
        }
        emoji_treat_map = {
            "bacon": "Bacon! My favorite!",
            "cut_of_meat": "Steak, delicious and nutritious!",
            "meat_on_bone": "Mmm, meat on a bone. A classic!",
            "hamburger": "A burger? Well, I'm not usually allowed, but just this once...",
            "pizza": "Pizza, you say? I could get used to this human food!",
            "hot_dog": "Hot dog! Can't resist that one!",
            "cheese_wedge": "Cheese? Yes, please!",
            "cookie": "Cookie! I promise to eat it slowly...",
            "apple": "An apple a day keeps the vet away!",
            "carrot": "A carrot! Great for my teeth.",
            "bread": "Bread, soft and yummy!",
            "fries": "Fries? I promise I won't tell the vet...",
            "ice_cream": "Ice cream? Oh, it's my lucky day!",
            "doughnut": "A donut! Sweet and delicious!",
            "green_apple": "An apple? Crunchy and sweet!",
            "banana": "A banana? It's not exactly a bone, but I'll take it!",
            "grapes": "Grapes? These are usually off-limits, but I'll make an exception...",
            "watermelon": "Watermelon? I love the crunch!",
            "strawberry": "Strawberries? What a sweet treat!",
            "poultry_leg": "Chicken? That's top-tier treat!",
            "peanuts": "Peanuts? What an interesting flavor!",
            "ear_of_corn": "Corn? A surprising treat, but I'll give it a try!",
            "poultry_leg": "Chicken is my favorite! Yum Yum!"
        }



        # Get the item.channel and the item.ts from the body
        item = body["event"]["item"]
        channel_id = item["channel"]
        message_ts = item["ts"]

        # Fetch message
        response = client.conversations_history(channel=channel_id, 
                                                inclusive=True, 
                                                oldest=message_ts, 
                                                limit=1)
        message = response['messages'][0]
        logger.info(f"associated message text is: {message['text']}")

        # Check the message to see if it was posted by "cleoai" or "Cleo AI"
        user_info = client.users_info(user=message['user'])
        logger.info(f"user name is: {user_info['user']['name']}")

        if user_info["ok"] and user_info["user"]["name"].lower() in ['cleoai', 'cleo ai']:
            # Get the reaction 
            reaction = body["event"]["reaction"]
            logger.info(f"reaction is: {reaction}")

            # Check the reaction and the message content
            if reaction in emoji_treat_map:
                # Send a reply to the user if the reaction is a treat emoji
                treat_response = emoji_treat_map[reaction]
                client.chat_postMessage(channel=channel_id,
                                        text=treat_response,
                                        thread_ts=message_ts)
            elif reaction in emoji_feature_map and message['text'] and "Woof! I'm Cleo :dog:" in message['text']:
                logger.info("detected intro message")
                # Map reaction to a feature
                voted_feature = emoji_feature_map[reaction]
                logger.info(f"voted_feature is: {voted_feature}")
                # Insert reaction data into "feature_votes" table
                data = {"slack_user_id": body["event"]["user"], 
                        "team_id": body["team_id"], 
                        "voted_feature": voted_feature}
                supabase.table("feature_votes").insert(data).execute()

                # Send a reply to the user
                client.chat_postMessage(channel=channel_id, 
                                    text="It's on Cleo's curriculum! ",
                                    thread_ts=message_ts)
            else:
                # Generate a reply using the generate_treat_reply function regardless of the message content as long as the reaction is on cleo's message
                treat_response = generate_treat_reply(reaction)
                client.chat_postMessage(channel=channel_id,
                                        text=treat_response,
                                        thread_ts=message_ts)


    @app.event("app_home_opened")
    def update_home_tab(client, event, logger):
        logger.info(f"update_home_tab called with event: {event}")

        try:
            client.views_publish(
                user_id=event["user"],
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*Welcome home, <@" + event["user"] + ">!*"
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Generate Document"
                                    },
                                    "value": "generate_document",
                                    "action_id": "button_generate_document"
                                },
                                # Add more buttons for different actions
                                # ...
                            ]
                        }
                    ]
                }
            )
        except Exception as e:
            logger.error(f"Error publishing home tab: {e}")

    @app.action("button_generate_document")
    def open_modal(ack, body, client):
        ack()

        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "modal_callback_id",
                "title": {
                    "type": "plain_text",
                    "text": "Document Generator"
                },
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "context_input_block",
                        "label": {
                            "type": "plain_text",
                            "text": "Dump your context here:"
                        },
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "context_input_action",
                            "multiline": True
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "radio_buttons_block",
                        "label": {
                            "type": "plain_text",
                            "text": "Choose document type"
                        },
                        "element": {
                            "type": "radio_buttons",
                            "options": [
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Roadmap"
                                    },
                                    "value": "roadmap"
                                },
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Meeting Summary"
                                    },
                                    "value": "meeting_summary"
                                }
                                # Add more options for different document types
                                # ...
                            ],
                            "action_id": "radio_buttons_action"
                        }
                    }
                ],
                "submit": {
                    "type": "plain_text",
                    "text": "Submit"
                }
            }
        )




    @app.view("modal_callback_id")
    def handle_view_submission(ack, body, client, logger):
        ack()

        user_id = body["user"]["id"]
        context_input_value = body["view"]["state"]["values"]["context_input_block"]["context_input_action"]["value"]
        document_type = body["view"]["state"]["values"]["radio_buttons_block"]["radio_buttons_action"]["selected_option"]["value"]

        try:
            # Call the gpt_generate_doc function to generate the document
            generated_document = gpt_generate_doc(context_input_value, document_type)
            logger.info(f"generated doc contents: {generated_document}")

            # Update the home tab with the generated document
            client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Welcome home, <@{user_id}>!*"
                            }
                        },
                        {
                            "type": "section",
                            "block_id": "cleo_output_block",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Cleo Output:*\n{generated_document}"  # Display the generated document
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Retry"
                                    },
                                    "action_id": "button_retry"
                                }
                            ]
                        }
                    ]
                }
            )

        except Exception as e:
            logger.error(f"Error generating or sending document: {e}")


    @app.action("button_retry")
    def handle_retry_button_press(ack, body, client):
        # Acknowledge the action
        ack()

        # Reopen the modal by calling the open_modal function
        open_modal(ack, body, client)



    @app.action("button_clear_output")
    def handle_clear_output_button_press(ack, body, client, logger):
        # Acknowledge the action
        ack()

        user_id = body["user"]["id"]

        # Update the home tab to clear the output section
        try:
            client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Welcome home, <@{user_id}>!*"
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Generate Document"
                                    },
                                    "action_id": "button_generate_document"
                                },
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Clear Output"
                                    },
                                    "action_id": "button_clear_output"
                                },
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Retry"
                                    },
                                    "action_id": "button_retry"
                                }
                            ]
                        }
                    ]
                }
            )
        except Exception as e:
            logger.error(f"Error clearing output: {e}")





    # Store the app for future use
    apps[team_id] = app
    
    return app
# End of def get_app(team_id):





@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    # Get the team_id from the Slack event payload
    team_id = request.json.get("team_id")
    logging.info(f"/slack/events payload is: {request.json}")
    logging.info(f"Received Slack event for team_id: {team_id}")

    # Add this log statement
    logging.info(f"Event type: {request.json.get('event', {}).get('type')}")

    app = get_app(team_id)

    handler = SlackRequestHandler(app)
    return handler.handle(request)




@flask_app.route("/slack/interactivity", methods=["POST"])
def slack_interactivity():
    logging.info(f"Started slack interactivity codeblock")

    # Extract the signature and timestamp from headers
    slack_signature = request.headers.get("X-Slack-Signature", "")
    slack_request_timestamp = request.headers.get("X-Slack-Request-Timestamp", "")

    # Verify the request timestamp to prevent replay attacks
    if abs(time.time() - int(slack_request_timestamp)) > 60 * 5:
        # If the timestamp is older than five minutes, reject the request
        return "Invalid request timestamp", 401

    # Form the base string by concatenating the version, timestamp and the raw payload
    req = str.encode(f"v0:{slack_request_timestamp}:{request.get_data(as_text=True)}")

    # Create a new HMAC using the secret as the key and SHA-256 as the digest
    hmac_obj = hmac.new(bytes(os.environ.get("SLACK_SIGNING_SECRET"), 'utf-8'), req, sha256)

    # Calculate the HMAC
    my_signature = "v0=" + hmac_obj.hexdigest()

    # Compare the HMACs
    if not hmac.compare_digest(my_signature, slack_signature):
        # If they don't match, reject the request
        return "Invalid request signature", 401

    # If we reach here, the request is valid. Parse the payload
    data = json.loads(request.form.get("payload"))
    logging.info(f"If we reach here, the request is valid. Parse the payload")


    # Get the team_id from the Slack event payload
    team_id = data.get("team", {}).get("id")
    logging.info(f"Team id from payload is: {team_id}")

    # Initialize Slack App
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
        # Post the intro message as a DM to the user
        response = slack_client.chat_postMessage(channel=user_id, text=intro_message)
        logging.info(f'slack_client.chat_postMessage(channel={user_id}, text={intro_message})')
        intro_message_ts = response["ts"]

        # Get a list of all public channels
        # public_channels_response = slack_client.conversations_list(types="public_channel")
        # logging.info(f'slack_client.conversations_list(types="public_channel") resulted in: {public_channels_response}')

        # # If the request was successful, extract the channels
        # if public_channels_response.get('ok'):
        #     public_channels = public_channels_response['channels']

        #     # Initialize an empty list to hold channel IDs
        #     public_channel_ids = []

        #     # Add bot to all public channels
        #     for channel in public_channels:
        #         public_channel_ids.append(channel['id'])  # Store the channel ID
        #         try:
        #             # Get the list of members of the channel
        #             channel_members = slack_client.conversations_members(channel=channel["id"])["members"]

        #             # If the bot is not already a member of the channel, join it
        #             if bot_user_id not in channel_members:
        #                 response_join = slack_client.conversations_join(channel=channel["id"])
        #                 logging.info(f'slack_client.conversations_join(channel={channel["id"]}) resulted in: {response_join}')
                        
        #         except SlackApiError as e:
        #             logging.error(f"Error adding bot to channel {channel['name']}: {e}")
        #             continue

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



# Endpoint for Basically Summarize Chrome Extension
@flask_app.route('/chromeextension', methods=['POST'])
def handle_basically_summarize_request():
    # parse the request payload
    payload = request.get_json()
    logging.info(f"request payload is: {payload}")


    # store the selectedText from the payload in selected_text variable
    selected_text = payload.get('selectedText')

    summarized_text = basically_summarize(selected_text)
    logging.info(f"summarized_text is: {summarized_text}")


    # respond to the chrome extension front end with the summarized_text
    return jsonify({
        'summarizedText': summarized_text
    })





if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=os.getenv('PORT', '8080'))  # default Cloud Run port is 8080


# DEV ONLY
# if __name__ == "__main__":
#     app.start(port=int(os.environ.get("PORT", 3000)))
