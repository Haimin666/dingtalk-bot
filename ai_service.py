"""
AI Service - LLM integration for conversation
Uses z-ai-web-dev-sdk compatible API with streaming support
"""
import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
import json
import httpx

from config import get_settings


@dataclass
class Message:
    """Chat message structure"""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ConversationSession:
    """Conversation session with history"""
    session_id: str
    user_id: str
    messages: List[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    
    def add_message(self, role: str, content: str):
        """Add a message to the conversation"""
        self.messages.append(Message(role=role, content=content))
        self.last_active = datetime.now()
        
        # Trim history if exceeds max
        settings = get_settings()
        if len(self.messages) > settings.max_history * 2:
            # Keep recent messages
            self.messages = self.messages[-settings.max_history:]


class AIService:
    """
    AI Service for LLM conversations
    
    Supports OpenAI-compatible API (Anthropic, OpenAI, etc.)
    with streaming output for DingTalk AI cards
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
        self.base_url = base_url or settings.ai_base_url or "https://api.anthropic.com"
        self.model = model or settings.ai_model
        self.system_prompt = system_prompt or settings.ai_system_prompt
        
        self._client: Optional[httpx.AsyncClient] = None
        self._sessions: Dict[str, ConversationSession] = {}
    
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
            # Determine API type based on base_url
            if "anthropic" in self.base_url:
                response = await self._call_anthropic_api(session)
            else:
                response = await self._call_openai_compatible_api(session)
            
            # Add assistant response to history
            session.add_message("assistant", response)
            
            return response
            
        except Exception as e:
            print(f"[AI] Error calling LLM API: {e}")
            return f"抱歉，AI服务暂时不可用：{str(e)}"
    
    async def _call_anthropic_api(self, session: ConversationSession) -> str:
        """Call Anthropic Claude API"""
        messages = self._build_messages(session)
        
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": messages,
        }
        
        url = f"{self.base_url}/v1/messages"
        
        response = await self._client.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            raise Exception(f"API error: {response.status_code} - {response.text}")
        
        data = response.json()
        
        # Extract text from response
        content = data.get("content", [])
        if content and len(content) > 0:
            return content[0].get("text", "")
        
        return ""
    
    async def _call_openai_compatible_api(self, session: ConversationSession) -> str:
        """Call OpenAI-compatible API"""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._build_messages(session))
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }
        
        url = f"{self.base_url}/v1/chat/completions"
        
        response = await self._client.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            raise Exception(f"API error: {response.status_code} - {response.text}")
        
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
        Stream chat response (OpenAI-compatible streaming)
        
        This method yields text chunks as they arrive from the LLM API,
        suitable for updating DingTalk AI cards with typewriter effect.
        
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
            "max_tokens": 4096,
            "stream": True,  # Enable streaming
        }
        
        url = f"{self.base_url}/v1/chat/completions"
        
        full_response = ""
        
        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise Exception(f"API error: {response.status_code} - {error_text.decode()}")
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            chunk = delta.get("content", "")
                            
                            if chunk:
                                full_response += chunk
                                yield chunk
                        except json.JSONDecodeError:
                            continue
            
            # Add complete response to history
            if full_response:
                session.add_message("assistant", full_response)
                
        except Exception as e:
            print(f"[AI] Error during streaming: {e}")
            error_msg = f"\n\n[错误] AI服务暂时不可用：{str(e)}"
            full_response += error_msg
            yield error_msg
            if full_response:
                session.add_message("assistant", full_response)
    
    async def stream_chat_anthropic(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat response using Anthropic API
        
        Args:
            user_message: User's message text
            session_id: Session identifier
            user_id: User identifier
            
        Yields:
            Text chunks from the AI response
        """
        await self._ensure_client()
        
        session = self.get_or_create_session(session_id, user_id)
        session.add_message("user", user_message)
        
        messages = self._build_messages(session)
        
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": messages,
            "stream": True,
        }
        
        url = f"{self.base_url}/v1/messages"
        
        full_response = ""
        
        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise Exception(f"API error: {response.status_code} - {error_text.decode()}")
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type", "")
                            
                            if event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    chunk = delta.get("text", "")
                                    if chunk:
                                        full_response += chunk
                                        yield chunk
                        except json.JSONDecodeError:
                            continue
            
            if full_response:
                session.add_message("assistant", full_response)
                
        except Exception as e:
            print(f"[AI] Error during Anthropic streaming: {e}")
            error_msg = f"\n\n[错误] AI服务暂时不可用：{str(e)}"
            full_response += error_msg
            yield error_msg
            if full_response:
                session.add_message("assistant", full_response)
    
    async def stream_chat_auto(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Automatically choose streaming method based on API type
        
        Args:
            user_message: User's message text
            session_id: Session identifier
            user_id: User identifier
            
        Yields:
            Text chunks from the AI response
        """
        if "anthropic" in self.base_url:
            async for chunk in self.stream_chat_anthropic(user_message, session_id, user_id):
                yield chunk
        else:
            async for chunk in self.stream_chat(user_message, session_id, user_id):
                yield chunk
    
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
