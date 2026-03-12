"""
AI Service - 智谱AI (ZhipuAI) integration with streaming support
"""
import asyncio
import json
import logging
from typing import Optional, List, Dict, Any, AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from config import get_settings

logger = logging.getLogger('dingtalk_bot.ai')


@dataclass
class ChatMessage:
    """Chat message structure"""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ConversationSession:
    """Conversation session with history"""
    session_id: str
    user_id: str
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    
    def add_message(self, role: str, content: str):
        """Add a message to the conversation"""
        self.messages.append(ChatMessage(role=role, content=content))
        self.last_active = datetime.now()
        
        # Trim history if exceeds max
        settings = get_settings()
        if len(self.messages) > settings.max_history * 2:
            # Keep recent messages
            self.messages = self.messages[-settings.max_history:]


class AIService:
    """
    AI Service for 智谱AI (ZhipuAI) conversations
    
    智谱AI API文档: https://open.bigmodel.cn/dev/api
    OpenAI兼容接口: https://open.bigmodel.cn/dev/api#openai-sdk
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ):
        settings = get_settings()
        self.api_key = api_key or settings.ai_api_key
        self.base_url = (base_url or settings.ai_base_url).rstrip('/')
        self.model = model or settings.ai_model
        self.system_prompt = system_prompt or settings.ai_system_prompt
        
        self._client: Optional[httpx.AsyncClient] = None
        self._sessions: Dict[str, ConversationSession] = {}
        
        logger.info(f"[AI] Service initialized - base_url: {self.base_url}, model: {self.model}")
    
    async def _ensure_client(self):
        """Ensure HTTP client exists"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
    
    def get_or_create_session(self, session_id: str, user_id: str) -> ConversationSession:
        """Get or create a conversation session"""
        if session_id not in self._sessions:
            self._sessions[session_id] = ConversationSession(
                session_id=session_id,
                user_id=user_id,
            )
        return self._sessions[session_id]
    
    def _build_messages(self, session: ConversationSession) -> List[Dict[str, str]]:
        """Build messages list for API request"""
        messages = []
        
        # Add conversation history
        for msg in session.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })
        
        return messages
    
    async def chat(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
    ) -> str:
        """
        Send a chat message and get AI response (non-streaming)
        
        Args:
            user_message: User's message text
            session_id: Session identifier
            user_id: User identifier
            
        Returns:
            AI response text
        """
        await self._ensure_client()
        
        # Get or create session
        session = self.get_or_create_session(session_id, user_id)
        
        # Add user message to history
        session.add_message("user", user_message)
        
        try:
            response = await self._call_api(session)
            
            # Add assistant response to history
            session.add_message("assistant", response)
            
            return response
            
        except Exception as e:
            logger.error(f"[AI] Error calling API: {e}")
            error_response = f"抱歉，AI服务暂时不可用：{str(e)}"
            session.add_message("assistant", error_response)
            return error_response
    
    async def _call_api(self, session: ConversationSession) -> str:
        """Call 智谱AI API (non-streaming)"""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._build_messages(session))
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "stream": False,
        }
        
        # 智谱AI OpenAI兼容端点
        url = f"{self.base_url}/chat/completions"
        
        logger.info(f"[AI] Calling API: {url}")
        
        response = await self._client.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"[AI] API error: {response.status_code} - {error_text}")
            raise Exception(f"API error: {response.status_code} - {error_text}")
        
        data = response.json()
        
        # Extract text from response
        choices = data.get("choices", [])
        if choices and len(choices) > 0:
            return choices[0].get("message", {}).get("content", "")
        
        return ""
    
    async def stream_chat(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat response from 智谱AI
        
        智谱AI流式调用示例:
        POST https://open.bigmodel.cn/api/paas/v4/chat/completions
        {
            "model": "glm-4-flash",
            "messages": [...],
            "stream": true
        }
        
        Args:
            user_message: User's message text
            session_id: Session identifier
            user_id: User identifier
            
        Yields:
            Text chunks from the AI response
        """
        await self._ensure_client()
        
        # Get or create session
        session = self.get_or_create_session(session_id, user_id)
        
        # Add user message to history
        session.add_message("user", user_message)
        
        # Build messages
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._build_messages(session))
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "stream": True,
        }
        
        # 智谱AI OpenAI兼容端点
        url = f"{self.base_url}/chat/completions"
        
        full_response = ""
        
        logger.info(f"[AI] Starting stream request to: {url}")
        
        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"[AI] Stream error: {response.status_code} - {error_text.decode()}")
                    raise Exception(f"API error: {response.status_code} - {error_text.decode()}")
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    # logger.debug(f"[AI] Stream line: {line[:100]}")
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        
                        if data_str == "[DONE]":
                            logger.info(f"[AI] Stream done, total response: {len(full_response)} chars")
                            break
                        
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                chunk = delta.get("content", "")
                                
                                if chunk:
                                    full_response += chunk
                                    yield chunk
                        except json.JSONDecodeError as e:
                            logger.warning(f"[AI] JSON decode error: {e}, line: {data_str[:100]}")
                            continue
            
            # Add complete response to history
            if full_response:
                session.add_message("assistant", full_response)
            else:
                logger.warning("[AI] Empty response from stream")
                
        except Exception as e:
            logger.error(f"[AI] Error during streaming: {e}")
            error_msg = f"\n\n[错误] AI服务暂时不可用：{str(e)}"
            full_response += error_msg
            yield error_msg
            if full_response:
                session.add_message("assistant", full_response)
    
    def clear_session(self, session_id: str):
        """Clear a conversation session"""
        if session_id in self._sessions:
            del self._sessions[session_id]
    
    def get_session_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session"""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            return [{"role": m.role, "content": m.content} for m in session.messages]
        return []
    
    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
