from supabase import create_client, Client
import os
import requests
import logging
from slack_bolt import App
from util import create_slack_post_for_flagged_message, is_sensitive_file, is_sensitive_message, generate_app_mention_reply, generate_treat_reply

# Setup logging
logging.basicConfig(level=logging.INFO)

# setup supabase client
url: str = os.environ.get('SUPABASE_URL')
key: str = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(url, key)

apps = {}

def get_app(team_id, team_data):
    logging.info(f"Attempting to get app for team_id: {team_id}")
    # Check if the app for this team is already created
    if team_id in apps:
        logging.info(f"App for team_id: {team_id} is already created.")
        return apps[team_id]

    # Get the required data from the pre-fetched team data
    if team_id in team_data:
        logging.info(f"Found team data for team_id: {team_id}")
        item = team_data[team_id]
        encrypted_access_token = item['encrypted_access_token']
        install_user_id = item['install_user_id']
    else:
        logging.warning(f"No team data found for team_id: {team_id}")
        encrypted_access_token = None
        install_user_id = None

    logging.info(f"Creating app for team_id: {team_id}...")
    app = App(
        token=encrypted_access_token,
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    )

    # Store the created app in the apps dictionary
    logging.info(f"Storing the created app for team_id: {team_id} in the apps dictionary.")
    apps[team_id] = app


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
        message_text = event['text']
        if not message_text:
            return  # Skip further processing if message is empty

        user_id = event['user']
        sent_time = event['ts']  # Unix timestamp
        channel_id = event['channel']

        # Get user's real name
        user_info = client.users_info(user=user_id).data
        sender_name = user_info['user']['profile']['real_name']

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
        
        if "hey cleo" in message_text.lower():
            reply = generate_app_mention_reply(message_text)
            logger.info(f"reply text is: {reply}")
            say(reply, channel=channel_id)
        elif "treat for cleo" in message_text.lower():
            reply = generate_treat_reply(message_text)
            logger.info("Thanks for the treat!")
            say(reply, channel=channel_id)
        else:
            is_sensitive = is_sensitive_message(message_text)
            logger.info(f'is sensitive is: {str(is_sensitive)}')
            if is_sensitive == 'true':
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


    
    return app
# End of def get_app(team_id):