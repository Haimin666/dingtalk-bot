"""
DingTalk Stream Client - WebSocket connection to receive messages

Based on official dingtalk-stream-sdk-python:
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

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger('dingtalk_stream')


@dataclass
class DingTalkMessage:
    """DingTalk message structure"""
    msg_id: str
    conversation_id: str
    conversation_type: str  # "1" = single chat, "2" = group chat
    sender_id: str
    sender_staff_id: str = ""
    sender_name: str = "User"
    content: str = ""
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
        self._processed_msg_ids = set()  # Track processed messages to avoid duplicates
        
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
    
    def _open_connection(self) -> Optional[Dict]:
        """
        Step 1: Open connection to get endpoint and ticket
        """
        logger.info(f'[Stream] Opening connection: {self.OPEN_CONNECTION_API}')
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': f'DingTalkStream/1.0 Python/{platform.python_version()}',
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
            import requests
            response = requests.post(
                self.OPEN_CONNECTION_API,
                headers=headers,
                json=body
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f'[Stream] Connection opened: endpoint={result.get("endpoint", "")}')
            return result
        except Exception as e:
            logger.error(f'[Stream] Failed to open connection: {e}')
            return None
    
    def _parse_chatbot_message(self, data) -> Optional[DingTalkMessage]:
        """Parse chatbot message from DingTalk callback"""
        try:
            # data 可能是字符串或字典
            if isinstance(data, str):
                data = json.loads(data)
            
            if not isinstance(data, dict):
                logger.error(f'[Stream] Data is not dict: {type(data)}')
                return None
            
            logger.info(f'[Stream] Parsing message from {data.get("senderNick", "Unknown")}')
            
            # 提取sessionWebhook
            session_webhook = data.get('sessionWebhook', '')
            
            msg_type = data.get('msgtype', '')
            content = ''
            
            # 根据不同的消息类型解析内容
            if msg_type == 'text':
                text_content = data.get('text', {})
                if isinstance(text_content, str):
                    text_content = json.loads(text_content) if text_content else {}
                content = text_content.get('content', '') if isinstance(text_content, dict) else ''
            else:
                # 尝试多种方式获取内容
                content = data.get('content', '')
                if isinstance(content, dict):
                    content = content.get('content', '')
                
                if not content:
                    text_data = data.get('text', {})
                    if isinstance(text_data, dict):
                        content = text_data.get('content', '')
                    elif isinstance(text_data, str):
                        content = text_data
            
            return DingTalkMessage(
                msg_id=data.get('msgId', str(uuid.uuid4())),
                conversation_id=data.get('conversationId', ''),
                conversation_type=data.get('conversationType', '1'),
                sender_id=data.get('senderId', ''),
                sender_staff_id=data.get('senderStaffId', ''),
                sender_name=data.get('senderNick', 'User'),
                content=content.strip() if content else '',
                msg_type=msg_type or 'text',
                create_time=data.get('createAt', int(time.time() * 1000)),
                session_webhook=session_webhook,
                session_webhook_expired_time=data.get('sessionWebhookExpiredTime', 0),
                raw_data=data,
            )
        except Exception as e:
            logger.error(f'[Stream] Error parsing message: {e}')
            return None
    
    async def _route_message(self, json_message: dict) -> Optional[str]:
        """Route incoming message to appropriate handler"""
        msg_type = json_message.get('type', '')
        
        logger.info(f'[Stream] Received message type: {msg_type}')
        
        if msg_type == 'SYSTEM':
            # System message (disconnect, etc.)
            topic = json_message.get('headers', {}).get('topic', '')
            if topic == 'SYSTEM_DISCONNECT':
                logger.info("[Stream] Received disconnect message")
                return 'disconnect'
                
        elif msg_type == 'CALLBACK':
            # Callback message (chatbot, etc.)
            topic = json_message.get('headers', {}).get('topic', '')
            
            if topic == self.TOPIC_CHATBOT_MESSAGE:
                # Chatbot message
                data = json_message.get('data', {})
                
                message = self._parse_chatbot_message(data)
                
                if message and self.on_message:
                    # Check if message was already processed (防止重复处理)
                    if message.msg_id in self._processed_msg_ids:
                        logger.info(f'[Stream] Message already processed, skipping: {message.msg_id}')
                        return None
                    
                    # Mark as processed
                    self._processed_msg_ids.add(message.msg_id)
                    
                    # Clean up old message IDs (keep last 1000)
                    if len(self._processed_msg_ids) > 1000:
                        self._processed_msg_ids = set(list(self._processed_msg_ids)[-500:])
                    
                    try:
                        await self.on_message(message)
                    except Exception as e:
                        logger.error(f'[Stream] Error in message handler: {e}')
        
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
                logger.error(f'[Stream] Ping error: {e}')
                break
    
    async def start(self):
        """Start the DingTalk Stream client"""
        self._running = True
        
        while self._running:
            try:
                # Step 1: Open connection to get endpoint and ticket
                connection = self._open_connection()
                
                if not connection:
                    logger.error('[Stream] Failed to open connection, retrying in 10s...')
                    await asyncio.sleep(10)
                    continue
                
                endpoint = connection.get('endpoint', '')
                ticket = connection.get('ticket', '')
                
                if not endpoint or not ticket:
                    logger.error('[Stream] Invalid connection response, retrying in 10s...')
                    await asyncio.sleep(10)
                    continue
                
                logger.info(f'[Stream] Connecting to WebSocket: {endpoint}')
                
                # Step 2: Connect to WebSocket
                uri = f'{endpoint}?ticket={quote_plus(ticket)}'
                
                async with websockets.connect(uri) as websocket:
                    self.websocket = websocket
                    logger.info('[Stream] WebSocket connected successfully!')
                    
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
                                    logger.info("[Stream] Disconnecting...")
                                    break
                                    
                            except json.JSONDecodeError:
                                logger.error(f'[Stream] Invalid JSON: {raw_message[:100]}')
                            except Exception as e:
                                logger.error(f'[Stream] Error processing message: {e}')
                    finally:
                        keepalive_task.cancel()
                        
            except KeyboardInterrupt:
                logger.info("[Stream] Interrupted by user")
                break
            except ConnectionClosed as e:
                logger.error(f'[Stream] WebSocket connection closed: {e}')
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f'[Stream] Connection error: {e}')
                await asyncio.sleep(3)
            finally:
                self.websocket = None
    
    async def stop(self):
        """Stop the DingTalk Stream client"""
        self._running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        logger.info("[Stream] Client stopped")
