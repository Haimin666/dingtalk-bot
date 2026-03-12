"""
DingTalk Stream Client - Based on official dingtalk-stream-sdk-python
https://github.com/open-dingtalk/dingtalk-stream-sdk-python

DingTalk Stream Mode Protocol:
1. Call OpenAPI to get endpoint and ticket
2. Connect to WebSocket using endpoint?ticket=ticket
"""
import asyncio
import json
import logging
import platform
import socket
import time
import uuid
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import requests
import websockets
from websockets.exceptions import ConnectionClosed

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-8s %(levelname)-8s %(message)s'
)
logger = logging.getLogger('dingtalk_stream')


@dataclass
class DingTalkMessage:
    """DingTalk message structure"""
    msg_id: str
    conversation_id: str
    conversation_type: str  # "1" = single chat, "2" = group chat
    sender_id: str
    sender_staff_id: str
    sender_name: str
    content: str
    msg_type: str = "text"
    create_time: int = field(default_factory=lambda: int(time.time() * 1000))
    session_webhook: str = ""
    session_webhook_expired_time: int = 0
    raw_data: dict = field(default_factory=dict)


class AckMessage:
    """Ack message for DingTalk Stream"""
    STATUS_OK = 0
    STATUS_NOT_IMPLEMENT = 1
    
    def __init__(self):
        self.code = self.STATUS_OK
        self.message = "OK"
        self.headers = Headers()
        self.data = {}
    
    def to_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "headers": self.headers.to_dict(),
            "data": self.data,
        }


class Headers:
    """Message headers"""
    CONTENT_TYPE_APPLICATION_JSON = "application/json"
    
    def __init__(self):
        self.message_id = ""
        self.content_type = self.CONTENT_TYPE_APPLICATION_JSON
    
    def to_dict(self):
        return {
            "messageId": self.message_id,
            "contentType": self.content_type,
        }


class Credential:
    """DingTalk credentials"""
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret


class DingTalkStreamClient:
    """
    DingTalk Stream Client - Based on official SDK
    
    Stream Mode Flow:
    1. Register connection: POST to /v1.0/gateway/connections/open
       - Returns: endpoint (WebSocket URL) and ticket (auth token)
    2. Connect WebSocket: wss://{endpoint}?ticket={ticket}
    3. Receive and process messages
    """
    
    OPEN_CONNECTION_API = "https://api.dingtalk.com/v1.0/gateway/connections/open"
    TOPIC_CHATBOT_MESSAGE = '/v1.0/im/bot/messages/get'
    
    def __init__(
        self,
        credential: Credential,
        on_message: Optional[Callable[[DingTalkMessage], Any]] = None,
    ):
        self.credential = credential
        self.on_message = on_message
        self.websocket = None
        self._running = False
        self._access_token = None
        self._access_token_expire_time = 0
        
    def _get_host_ip(self) -> str:
        """Get local IP address"""
        ip = ""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    
    def _get_access_token(self) -> Optional[str]:
        """Get access token for OpenAPI calls"""
        now = int(time.time())
        if self._access_token and now < self._access_token_expire_time:
            return self._access_token
        
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        body = {
            'appKey': self.credential.client_id,
            'appSecret': self.credential.client_secret,
        }
        
        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            result = response.json()
            self._access_token = result['accessToken']
            self._access_token_expire_time = now + result['expireIn'] - 300
            return self._access_token
        except Exception as e:
            logger.error(f'Failed to get access token: {e}')
            return None
    
    def _open_connection(self) -> Optional[Dict]:
        """
        Step 1: Open connection to get endpoint and ticket
        
        POST https://api.dingtalk.com/v1.0/gateway/connections/open
        
        Request body:
        {
            "clientId": "AppKey",
            "clientSecret": "AppSecret", 
            "subscriptions": [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}],
            "localIp": "x.x.x.x"
        }
        
        Response:
        {
            "endpoint": "wss://stream.dingtalk.com/ws/v2/stream",
            "ticket": "xxxxx"
        }
        """
        logger.info(f'Opening connection: {self.OPEN_CONNECTION_API}')
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': f'DingTalkStream/1.0 Python/{platform.python_version()} '
                          f'(+https://github.com/open-dingtalk/dingtalk-stream-sdk-python)',
        }
        
        # Subscribe to chatbot messages
        subscriptions = [
            {'type': 'CALLBACK', 'topic': self.TOPIC_CHATBOT_MESSAGE}
        ]
        
        body = {
            'clientId': self.credential.client_id,
            'clientSecret': self.credential.client_secret,
            'subscriptions': subscriptions,
            'ua': f'dingtalk-sdk-python/v1.0',
            'localIp': self._get_host_ip()
        }
        
        try:
            response = requests.post(
                self.OPEN_CONNECTION_API,
                headers=headers,
                json=body
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f'Connection opened successfully: endpoint={result.get("endpoint", "")}')
            return result
        except Exception as e:
            logger.error(f'Failed to open connection: {e}')
            return None
    
    def _parse_chatbot_message(self, data: dict) -> Optional[DingTalkMessage]:
        """Parse chatbot message from DingTalk callback"""
        try:
            msg_type = data.get('msgtype', '')
            
            if msg_type == 'text':
                text_content = data.get('text', {})
                content = text_content.get('content', '')
            elif msg_type == 'richText':
                # Parse rich text content
                content_data = data.get('content', {})
                rich_text_list = content_data.get('richText', [])
                content = ' '.join([
                    item.get('text', '') 
                    for item in rich_text_list 
                    if 'text' in item
                ])
            else:
                content = data.get('content', {}).get('content', '')
            
            return DingTalkMessage(
                msg_id=data.get('msgId', str(uuid.uuid4())),
                conversation_id=data.get('conversationId', ''),
                conversation_type=data.get('conversationType', '1'),
                sender_id=data.get('senderId', ''),
                sender_staff_id=data.get('senderStaffId', ''),
                sender_name=data.get('senderNick', 'User'),
                content=content.strip(),
                msg_type=msg_type or 'text',
                create_time=data.get('createAt', int(time.time() * 1000)),
                session_webhook=data.get('sessionWebhook', ''),
                session_webhook_expired_time=data.get('sessionWebhookExpiredTime', 0),
                raw_data=data,
            )
        except Exception as e:
            logger.error(f'Error parsing message: {e}')
            return None
    
    async def _route_message(self, json_message: dict) -> Optional[str]:
        """Route incoming message to appropriate handler"""
        msg_type = json_message.get('type', '')
        
        if msg_type == 'SYSTEM':
            # System message (disconnect, etc.)
            topic = json_message.get('headers', {}).get('topic', '')
            if topic == 'SYSTEM_DISCONNECT':
                logger.info("Received disconnect message")
                return 'disconnect'
            else:
                logger.warning(f"Unknown system message topic: {topic}")
                
        elif msg_type == 'CALLBACK':
            # Callback message (chatbot, etc.)
            topic = json_message.get('headers', {}).get('topic', '')
            
            if topic == self.TOPIC_CHATBOT_MESSAGE:
                # Chatbot message
                data = json_message.get('data', {})
                message = self._parse_chatbot_message(data)
                
                if message and self.on_message:
                    try:
                        await self.on_message(message)
                    except Exception as e:
                        logger.error(f'Error in message handler: {e}')
            else:
                logger.warning(f"Unknown callback topic: {topic}")
        else:
            logger.warning(f"Unknown message type: {msg_type}")
        
        return None
    
    async def _keepalive(self, ws, ping_interval: int = 60):
        """Send periodic pings to keep connection alive"""
        while self._running:
            await asyncio.sleep(ping_interval)
            try:
                await ws.ping()
            except ConnectionClosed:
                break
            except Exception as e:
                logger.error(f'Ping error: {e}')
                break
    
    async def start(self):
        """Start the DingTalk Stream client"""
        self._running = True
        
        while self._running:
            try:
                # Step 1: Open connection to get endpoint and ticket
                connection = self._open_connection()
                
                if not connection:
                    logger.error('Failed to open connection, retrying in 10s...')
                    await asyncio.sleep(10)
                    continue
                
                endpoint = connection.get('endpoint', '')
                ticket = connection.get('ticket', '')
                
                if not endpoint or not ticket:
                    logger.error('Invalid connection response, retrying in 10s...')
                    await asyncio.sleep(10)
                    continue
                
                logger.info(f'Connecting to WebSocket: {endpoint}')
                
                # Step 2: Connect to WebSocket
                uri = f'{endpoint}?ticket={quote_plus(ticket)}'
                
                async with websockets.connect(uri) as websocket:
                    self.websocket = websocket
                    logger.info('[DingTalk] WebSocket connected successfully!')
                    
                    # Start keepalive task
                    keepalive_task = asyncio.create_task(self._keepalive(websocket))
                    
                    try:
                        # Step 3: Receive messages
                        async for raw_message in websocket:
                            if not self._running:
                                break
                            
                            try:
                                json_message = json.loads(raw_message)
                                
                                # Route message and process
                                result = await self._route_message(json_message)
                                
                                # Send ack for callback messages
                                if json_message.get('type') == 'CALLBACK':
                                    ack = AckMessage()
                                    ack.headers.message_id = json_message.get('headers', {}).get('messageId', '')
                                    ack.data = json_message.get('data', {})
                                    await websocket.send(json.dumps(ack.to_dict()))
                                
                                # Handle disconnect
                                if result == 'disconnect':
                                    logger.info("Disconnecting...")
                                    break
                                    
                            except json.JSONDecodeError:
                                logger.error(f'Invalid JSON: {raw_message[:100]}')
                            except Exception as e:
                                logger.error(f'Error processing message: {e}')
                    finally:
                        keepalive_task.cancel()
                        
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except ConnectionClosed as e:
                logger.error(f'WebSocket connection closed: {e}')
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f'Connection error: {e}')
                await asyncio.sleep(3)
            finally:
                self.websocket = None
    
    def start_forever(self):
        """Start the client and run forever"""
        while True:
            try:
                asyncio.run(self.start())
            except KeyboardInterrupt:
                break
            finally:
                time.sleep(3)
    
    async def stop(self):
        """Stop the DingTalk Stream client"""
        self._running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        logger.info("[DingTalk] Client stopped")


class DingTalkMessageSender:
    """Send messages to DingTalk using sessionWebhook"""
    
    def __init__(self, credential: Credential):
        self.credential = credential
        self._access_token = None
        self._access_token_expire_time = 0
    
    def _get_access_token(self) -> Optional[str]:
        """Get access token"""
        now = int(time.time())
        if self._access_token and now < self._access_token_expire_time:
            return self._access_token
        
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        body = {
            'appKey': self.credential.client_id,
            'appSecret': self.credential.client_secret,
        }
        
        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            result = response.json()
            self._access_token = result['accessToken']
            self._access_token_expire_time = now + result['expireIn'] - 300
            return self._access_token
        except Exception as e:
            logger.error(f'Failed to get access token: {e}')
            return None
    
    async def reply_text(self, message: DingTalkMessage, text: str) -> bool:
        """
        Reply with text message using sessionWebhook
        
        This is the simplest way to reply to a message.
        """
        if not message.session_webhook:
            logger.error('No sessionWebhook available')
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': '*/*',
        }
        
        body = {
            'msgtype': 'text',
            'text': {
                'content': text,
            },
            'at': {
                'atUserIds': [message.sender_staff_id] if message.sender_staff_id else [],
            }
        }
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    message.session_webhook,
                    headers=headers,
                    json=body
                ) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        text = await resp.text()
                        logger.error(f'Failed to reply: {resp.status} - {text}')
                        return False
        except Exception as e:
            logger.error(f'Error replying: {e}')
            return False
    
    async def reply_markdown(self, message: DingTalkMessage, title: str, content: str) -> bool:
        """Reply with markdown message"""
        if not message.session_webhook:
            logger.error('No sessionWebhook available')
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': '*/*',
        }
        
        body = {
            'msgtype': 'markdown',
            'markdown': {
                'title': title,
                'text': content,
            },
            'at': {
                'atUserIds': [message.sender_staff_id] if message.sender_staff_id else [],
            }
        }
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    message.session_webhook,
                    headers=headers,
                    json=body
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f'Error replying markdown: {e}')
            return False
