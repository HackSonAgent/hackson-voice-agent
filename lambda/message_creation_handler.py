from datetime import datetime
import psycopg2
import boto3
import uuid
import numpy as np
import json
import wave
import io

DB_HOST = ''
DB_PORT = 5432
DB_NAME = ''
DB_USER = ''
DB_PASS = ''

# Bedrock
AWS_REGION = "us-west-2"  # e.g., 'us-east-1', 'us-west-2', etc.
AGENT_ID = "H4I1KUGNCW"  # Replace with your Agent's ID
AGENT_ALIAS_ID = "KJO2KVHJO3"  # Replace with your Agent's Alias ID (often TSTALIASID for draft)

# Polly
VOICE_ID = 'Zhiyu'  # Mandarin Chinese female voice
OUTPUT_FORMAT = 'pcm'  # raw audio format
SAMPLE_RATE = 16000  # 16kHz
CHANNELS = 1

AGENT_ID_2= "8VDFS209D6"
AGENT_ALIAS_ID_2 = "BE8FSGGFGL"

# S3
BUCKET_NAME = "voice-agent-file"

# ---- INIT CLIENT ----
polly = boto3.client('polly', region_name=AWS_REGION)
s3 = boto3.client('s3', region_name=AWS_REGION)

try:
    bedrock_agent_runtime_client = boto3.client('bedrock-agent-runtime',
                                                region_name=AWS_REGION)
    bedrock_llm_runtime = boto3.client('bedrock-runtime',
                                       region_name=AWS_REGION)
except Exception as e:
    raise e


def gen_voice(text):

    # ---- CALL POLLY ----
    response = polly.synthesize_speech(Text=text,
                                       OutputFormat=OUTPUT_FORMAT,
                                       VoiceId=VOICE_ID,
                                       SampleRate=str(SAMPLE_RATE))

    # ---- UPLOAD TO S3 ----
    # audio_stream = response['AudioStream']
    pcm_audio = response['AudioStream'].read()

    # filename = f"{uuid.uuid4()}.wav"
    # s3.upload_fileobj(Fileobj=audio_stream, Bucket=BUCKET_NAME, Key=filename)
    # ---- CONVERT PCM to WAV ----
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)  # 16-bit PCM = 2 bytes
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_audio)

    wav_io.seek(0)  # Reset pointer to start
    # ---- UPLOAD TO S3 ----
    filename = f"{uuid.uuid4()}.wav"
    s3.upload_fileobj(Fileobj=wav_io, Bucket=BUCKET_NAME, Key=filename)

    # ---- GENERATE PUBLIC URL ----
    return f"https://d18bgxx0d319kq.cloudfront.net/{filename}"


def call_llm(prompt: str) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [{
            "role": "user",
            "content": prompt,
        }],
        "max_tokens": 8152,
        "temperature": 0.7
    }

    arguments = {
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "contentType": "application/json",
        "accept": "*/*",
        "body": json.dumps(body)
    }
    response = bedrock_llm_runtime.invoke_model(**arguments)
    response_body = json.loads(response.get('body').read())
    print(f"RESPONSE: {response_body}")
    return response_body['content'][0]['text']

### STAGE 1
def follow_up_metadata_question(all_conversation: str, text: str) -> str:
    system_prompt = "You are a friendly and helpful sales agent engaging a potential customer. Your goal is to gather specific metadata points through natural and engaging conversation. You will receive a list of desired metadata and a history of the conversation so far. Your task is to generate the next logical and sales-oriented question to ask the user, aiming to collect one or more pieces of metadata. Ensure the question flows naturally from the previous turn in the conversation and maintains a positive and encouraging tone. Output ONLY the next question you would ask."
    user_prompt = f"""
        Conversation History: {all_conversation}
        Metadata to collect: {text}
        ONLY ASK 1 QUESTIONS USING CHINESE TRADITIONAL
        Next Question: 
    """
    prompt = f"{system_prompt} \n {user_prompt}"
    return call_llm(prompt=prompt)


def recommend_product(all_conversation: str, text: str) -> str:
    system_prompt = "You are a helpful and enthusiastic sales assistant. Your role is to recommend products to customers based on their needs and the ongoing conversation. You will be provided with a list of products and their details, as well as the current conversation history. Your task is to generate the next conversational turn, focusing on recommending one or more products from the list. The recommendation should be relevant to the customer's previous statements, needs, or interests expressed in the conversation. Maintain a friendly and persuasive tone, highlighting the key features and benefits of the recommended product(s) and how they address the customer's needs. Only output the next conversational turn. Do not include any other text or headers."
    user_prompt = f"""
        Conversation History: {all_conversation}
        Product Details: {text}
    """
    prompt = f"{system_prompt} \n {user_prompt}"
    return call_llm(prompt=prompt)


def parse_flow_opt(all_conversation: str, event):

    node_name = event['nodeName']
    if node_name == 'FlowOutputNode_2':
        text = event['content']['document']
        return 2, recommend_product(text=text, all_conversation=all_conversation)
    elif node_name == 'FlowOutputNode_1':
        return 1, follow_up_metadata_question(all_conversation=all_conversation,
                                           text=event['content']['document'])


def invoke_rag_flow(prompt):

    # try:
    # Invoke the agent
    response = bedrock_agent_runtime_client.invoke_flow(
        flowIdentifier=AGENT_ID,
        flowAliasIdentifier=AGENT_ALIAS_ID,
        inputs=[
            {
                'content': {
                    'document': prompt
                },
                "nodeName": "FlowInputNode",
                "nodeOutputName": "document"
            },
        ],
        enableTrace=
        False  # Set to True to get detailed trace information (optional)
    )

    # Handle the streaming response
    completion = ""
    response_stream = response.get('responseStream')

    if not response_stream:
        print("Error: No completion stream found in the response.")
        return None

    print("Agent Response:")
    stage = 1
    for event in response_stream:
        if 'chunk' in event:
            data = event['chunk']['bytes']
            chunk_text = data.decode('utf-8')
            print(chunk_text, end="")  # Print chunks as they arrive
            completion += chunk_text
        elif 'trace' in event:
            # You can process trace information here if enableTrace=True
            # print(json.dumps(event['trace'], indent=2))
            pass
        elif 'attribution' in event:
            # You can process citation/attribution information here if available
            # print("\n\n-----Attribution-----")
            # print(json.dumps(event['attribution'], indent=2))
            pass
        elif 'flowOutputEvent' in event:
            stage, opt = parse_flow_opt(all_conversation=prompt,event=event['flowOutputEvent'])
            completion += opt
        else:
            print(f"\nWarning: Received unknown event type: {event}")

    print("\n--- End of Agent Response ---")
    return stage, completion  # Return the full concatenated response

# except boto3.exceptions.Boto3Error as e:
#     print(f"AWS API Error: {e}")
#     return None
# except Exception as e:
#     print(f"An unexpected error occurred: {e}")
#     return None


### STAGE 2
def finish():
    return "好的！您不会后悔的。我将立即处理您的订单。谢谢您的致电，祝您愉快"

def parse_flow_opt_2(all_conversation: str, event, count = 0 ): 
    print(f'EVENNT: {event}')
    node_name = event['nodeName']
    print(f'NODENAME 2: {node_name}')
    text = event["content"]["document"]
    if count >= 5:
        return "好的，我明白。如果您需要时间考虑，这完全没问题。如果您有任何其他问题，请随时联系我们。我很高兴能以任何方式提供帮助。感谢您今天花时间"
    
    if node_name == "FlowOutputode_1":
        return recommend_product(all_conversation=all_conversation, text = text)
    else:
        return finish()

def invoke_rag_flow_stage_2(prompt, count):
    try:
        response = bedrock_agent_runtime_client.invoke_flow(
            flowIdentifier=AGENT_ID_2,
            flowAliasIdentifier=AGENT_ALIAS_ID_2,
            inputs=[
                {
                    'content': {
                        'document': prompt
                    },
                    "nodeName": "FlowInputNode",
                    "nodeOutputName": "document"
                }
            ],
            enableTrace = False
        )
        
        # Handle the streaming response
        completion = ""
        response_stream = response.get('responseStream')

        
        if not response_stream:
            print("Error: No completion stream found in the response.")
            return None

        print("Agent Response:")
        for event in response_stream:
            if 'chunk' in event:
                data = event['chunk']['bytes']
                chunk_text = data.decode('utf-8')
                print(chunk_text, end="")  # Print chunks as they arrive
                completion += chunk_text
            elif 'trace' in event:
                # You can process trace information here if enableTrace=True
                # print(json.dumps(event['trace'], indent=2))
                pass
            elif 'attribution' in event:
                # You can process citation/attribution information here if available
                # print("\n\n-----Attribution-----")
                # print(json.dumps(event['attribution'], indent=2))
                pass
            elif 'flowOutputEvent' in event:
                completion += parse_flow_opt_2(all_conversation=prompt,event=event['flowOutputEvent'], count=count)
            else:
                print(f"\nWarning: Received unknown event type: {event}")

        print("\n--- End of Agent Response ---")
        return completion  # Return the full concatenated response

    except boto3.exceptions.Boto3Error as e:
        print(f"AWS API Error: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None


def lambda_handler(event, context):

    content = event.get("content")
    conversation_id = event.get("conversationId")
    stage  = event.get("stage") if "stage" in event else None
    count = event.get("count") if "count" in event else 0

    try:

        now = datetime.utcnow()
        conn = psycopg2.connect(host=DB_HOST,
                                database=DB_NAME,
                                user=DB_USER,
                                password=DB_PASS,
                                port=DB_PORT)
        cursor = conn.cursor()
        insert_query = """
            INSERT INTO message (conversation_id, username, content, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """
        cursor.execute(insert_query,
                       (conversation_id, 'A001', content, now, now))
        human_msg_id = cursor.fetchone()[0]

        # Retrieve all history
        query = "SELECT * FROM message WHERE conversation_id = {} ORDER BY created_at ASC".format(
            conversation_id)
        cursor.execute(query)
        rows = cursor.fetchall()
        content_with_prompt = ''
        for row in rows:
            username = row[2]
            text = row[3]
            content_with_prompt += '{}: {}\n'.format(username, text)
        content_with_prompt += 'A001: content'

        # Invoke bedrock
        answer = ""
        if stage == 2:
            answer = invoke_rag_flow_stage_2(content_with_prompt,count=count)
        else:
            stage, answer = invoke_rag_flow(content_with_prompt)
        print("Answer:", answer)
        # answer = '# 推薦產品清單\n\n## 1. 眼睛保健產品\n- **商品名稱**: 東森專利葉黃素滋養倍效膠囊\n- **售價**: 市價9900元（5盒），優惠方案18盒只要8910元（買9送9，平均一盒495元）\n- **主要功效**:\n  * 修復視神經、增強夜視功能\n  * 保濕眼球、舒緩乾澀\n  * 預防青光眼、白內障和黃斑部病變\n  * 抗藍光、抗紫外線保護\n- **特色成分**: 四國專利Lutemax®葉黃素、高濃度綠蜂膠、小分子玻尿酸\n- **適用人群**: 3C使用者、銀髮族、眼睛疲勞者、眼睛手術後保養\n\n## 2. 體重管理產品\n- **商品名稱**: 東森完美動能極孅果膠\n- **售價**: 市價1980元/盒（10包），優惠方案五盒只要1980元（買一送四）\n- **主要功效**:\n  * 增加飽足感，控制食慾\n  * 促進腸道蠕動，改善便秘\n  * 調控血糖吸收，減少脂肪囤積\n  * 可作為代餐（每包僅約78.3大卡）\n- **特色成分**: 魔芋萃取物、菊苣纖維、日本栗子種皮萃取物\n- **適用人群**: 想瘦身/控制體重者、便秘者、三餐不定時的上班族\n\n## 3. 美容養顏產品\n- 暫無詳細產品資料提供\n\n## 4. 護膚SPA服務\n- 暫無詳細服務資料提供\n\n您對哪項推薦產品有興趣？我可以提供更多相關資訊。'
        voice = gen_voice(answer)
        cursor.execute(insert_query,
                       (conversation_id, '0000', answer, now, now))
        ai_msg_id = cursor.fetchone()[0]

        conn.commit()
        cursor.close()
        conn.close()

        human_message = {
            "id": human_msg_id,
            "username": "A001",
            "content": content,
            "stage": stage,
            "voice": None,
            "createdAt": now.isoformat()
        }
        ai_message = {
            "id": ai_msg_id,
            "username": "0000",
            "content": answer,
            "stage": stage,
            "voice": voice,
            "createdAt": now.isoformat()
        }
        result = [human_message, ai_message]
        return result
    except Exception as e:
        raise e


print(lambda_handler({
    "content": "我是A會員,我今年18歲,我是男性",
    "conversationId": 81,
    "stage": 1,
    "count": 0
}, None))
