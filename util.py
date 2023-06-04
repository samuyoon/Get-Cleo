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