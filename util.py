import os
import openai
import time
# Initialize Open AI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
gpt_model = 'gpt-4'

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


# No longer in use
def create_slack_post_for_flagged_message(client, user_id, sender_name, sent_time, has_file=False, file_name="", message_link=""):
    if has_file:
        message_text = f"*Woof Woof! :mega:*\n\nI fetched a sensitive file (`{file_name}`) that was sent by {sender_name}. You can view and delete the original message here: {message_link}"
    else:
        message_text = f"*Woof Woof! :mega:*\n\nI fetched a sensitive message that was sent by {sender_name}. You can view and delete the original message here: {message_link}"

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
    
    # Open a direct message channel with the user
    response = client.conversations_open(users=user_id)
    
    # Get the channel ID for the direct message channel
    channel_id = response["channel"]["id"]
    
    # Post the message
    client.chat_postMessage(channel=channel_id, **message)

# No longer in use
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

# No longer in use
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



@auto_rate_limit_gpt
def generate_app_mention_reply(user_message, model=gpt_model):
    system_message = 'You are Cleo, a Slack App. Users can teach you new tricks by using /suggestnewtrick in any channel where you is present. Your persona is one of a purple, sassy goldendoodle dog. Respond to the user message by being as helpful as possible. If you are not sure how to respond, include a dog related pun in your response. If return code, always use code snippets and ensure that you use Slack text formatting rules.'
    user_message = f'{user_message}'
    messages = [{"role": "user", "content": user_message}, {"role": "system", "content": system_message}]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message

@auto_rate_limit_gpt
def generate_treat_reply(user_message, model=gpt_model):
    system_message = 'You are Cleo, a Slack App. The user message contains an emoji which represents a treat for you, a goldendoodle puppy. React to the treat as if you were a sassy goldendoodle puppy. Only react positively to emojis that would be edible for a dog.'
    user_message = f'{user_message}'
    messages = [{"role": "user", "content": user_message}, {"role": "system", "content": system_message}]
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message

@auto_rate_limit_gpt
def gpt_generate_reply(messages_dict, user_real_name, model=gpt_model):
    # Convert the messages_dict into a sorted string
    messages_str = '\n'.join([f'{timestamp}: {message}' for timestamp, message in sorted(messages_dict.items(), reverse=True)])

    system_message = f'You are {user_real_name} and you are responding to a slack channel with other people in it. You have been given a series of messages with timestamps. Your task is to understand these messages and generate the next message in the discussion. Keep in mind that more recent messages are often more relevant to the current context, so they should be given more importance in your response. Generate a response in a tone that matches the tone in the conversation history. Format your response to use Slack text formatting rules. Ensure your response addresses any questions that you have enough information to answer, but do not try to answer questions that cannot be answered without more context. Return only a string with your response and nothing else. Ignore any user messages that are obviously from bot users, such as Cleo AI, Trello, or Google.'

    user_message = f'{messages_str}'

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message

@auto_rate_limit_gpt
def gpt_generate_catchmeup(messages_dict, user_real_name, model=gpt_model):
    # Convert the messages_dict into a sorted string
    messages_str = '\n'.join([f'{timestamp}: {message}' for timestamp, message in sorted(messages_dict.items(), reverse=True)])

    system_message = f'You are a channel summarizer. You have been given a series of messages with timestamps. Your task is to understand these messages and generate a helpful and actionable summary of the conversation thus far. Keep in mind that more recent messages are often more relevant to the current context, so they should be given more importance in your response. Format your response to use Slack text formatting rules. Return the summary as only a string of 250 characters or less as your response and nothing else.'

    user_message = f'I am {user_real_name}. Here is the message history: {messages_str}'

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message

	
@auto_rate_limit_gpt
def gpt_generate_doc(context_input_value, document_type, model=gpt_model):

    system_message = f'You are a {document_type} document generator. If the user has not provided you with enough context to generate a {document_type}, return only the following string: "false". Otherwise, return only the content for the document and no other text.'

    user_message = f'Please generate a {document_type} document. Here is some context: {context_input_value} you should use.'

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message


	
@auto_rate_limit_gpt
def basically_summarize(selected_text, model=gpt_model):

    system_message = f'Your job is to take in text and explain it to the user like they are five years old. Be succint and use examples.'

    user_message = f'Here is my text. Explain it to me like I am five: ```{selected_text}```'

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ]

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=2000,
            n=1,
            stop=None,
            temperature=1,
        )
    except openai.error.OpenAIError as e:
        print(f"An error occurred with OpenAI: {e}")
        return 'Error during processing'
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 'Unexpected error during processing'

    openai_response_message = response.choices[0].message['content']
    print(f"Model's response: {openai_response_message}")

    return openai_response_message