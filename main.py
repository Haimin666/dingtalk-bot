"""
Main FastAPI Application for DingTalk Bot Service
支持流式AI卡片回复（打字机效果）

基于钉钉官方SDK: https://github.com/open-dingtalk/dingtalk-stream-sdk-python
"""
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import get_settings
from dingtalk_client import (
    DingTalkStreamClient,
    DingTalkMessage,
    DingTalkMessageSender,
    Credential,
)
from ai_service import AIService
from streaming_card import StreamingCardManager, create_markdown_message, create_text_message

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-8s %(levelname)-8s %(message)s'
)
logger = logging.getLogger('dingtalk_bot')


# Global instances
settings = get_settings()
dingtalk_client: Optional[DingTalkStreamClient] = None
message_sender: Optional[DingTalkMessageSender] = None
ai_service: Optional[AIService] = None
streaming_card_manager: Optional[StreamingCardManager] = None


class MessageResponse(BaseModel):
    """API response model"""
    success: bool
    message: str
    data: Optional[dict] = None


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    streaming: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global dingtalk_client, message_sender, ai_service, streaming_card_manager
    
    logger.info("[Service] Starting DingTalk Bot Service with Streaming AI Card...")
    
    # Initialize AI service
    ai_service = AIService()
    logger.info("[Service] AI Service initialized")
    
    # Initialize DingTalk components
    if settings.dingtalk_client_id and settings.dingtalk_client_secret:
        credential = Credential(
            client_id=settings.dingtalk_client_id,
            client_secret=settings.dingtalk_client_secret,
        )
        
        # Initialize message sender
        message_sender = DingTalkMessageSender(credential)
        logger.info("[Service] DingTalk Message Sender initialized")
        
        # Initialize streaming card manager
        streaming_card_manager = StreamingCardManager(
            client_id=settings.dingtalk_client_id,
            client_secret=settings.dingtalk_client_secret,
        )
        logger.info("[Service] Streaming Card Manager initialized")
        
        # Initialize and start DingTalk Stream client
        dingtalk_client = DingTalkStreamClient(
            credential=credential,
            on_message=handle_dingtalk_message,
        )
        
        # Start DingTalk client in background
        asyncio.create_task(dingtalk_client.start())
        logger.info("[Service] DingTalk Stream Client started")
    else:
        logger.warning("[Service] Warning: DingTalk credentials not configured")
    
    yield
    
    # Cleanup
    logger.info("[Service] Shutting down...")
    if dingtalk_client:
        await dingtalk_client.stop()
    if streaming_card_manager:
        await streaming_card_manager.close()
    if ai_service:
        await ai_service.close()
    logger.info("[Service] Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="DingTalk Bot Service",
    description="DingTalk robot service with AI integration and streaming card support",
    version="0.3.0",
    lifespan=lifespan,
)


async def handle_dingtalk_message(message: DingTalkMessage):
    """
    Handle incoming DingTalk message
    
    This is called when a message is received from DingTalk Stream.
    """
    logger.info(f"[DingTalk] Received message from {message.sender_name}: {message.content[:50]}...")
    
    if not ai_service:
        logger.warning("[DingTalk] AI service not available")
        return
    
    # Skip empty messages
    if not message.content or not message.content.strip():
        return
    
    # Handle slash commands
    if message.content.startswith("/"):
        await handle_slash_command(message)
        return
    
    try:
        # Generate session ID from conversation ID
        session_id = message.conversation_id
        
        # Check if streaming card is available
        if streaming_card_manager:
            # Use streaming AI card response
            await handle_streaming_response(message, session_id)
        else:
            # Fallback to normal response
            await handle_normal_response(message, session_id)
            
    except Exception as e:
        logger.error(f"[DingTalk] Error processing message: {e}")
        if message_sender:
            await message_sender.reply_text(message, f"处理消息时出错：{str(e)}")


async def handle_streaming_response(message: DingTalkMessage, session_id: str):
    """
    Handle message with streaming AI card response (typewriter effect)
    """
    logger.info(f"[DingTalk] Using streaming card response for session: {session_id}")
    
    try:
        # Create content generator from AI service
        async def content_generator():
            async for chunk in ai_service.stream_chat_auto(
                user_message=message.content,
                session_id=session_id,
                user_id=message.sender_id,
            ):
                yield chunk
        
        # Send AI card and stream content
        if streaming_card_manager:
            full_content = await streaming_card_manager.send_and_stream(
                open_conversation_id=message.conversation_id,
                content_generator=content_generator(),
                title="🤖 AI助手",
                conversation_type=message.conversation_type,
                sender_staff_id=message.sender_staff_id,
            )
            logger.info(f"[StreamingCard] Completed streaming: {len(full_content)} chars")
        else:
            # Fallback to normal response
            await handle_normal_response(message, session_id)
            
    except Exception as e:
        logger.error(f"[StreamingCard] Error in streaming response: {e}")
        # Fallback to normal response
        await handle_normal_response(message, session_id)


async def handle_normal_response(message: DingTalkMessage, session_id: str):
    """
    Handle message with normal (non-streaming) response
    """
    logger.info(f"[DingTalk] Using normal response for session: {session_id}")
    
    try:
        # Get AI response
        response = await ai_service.chat(
            user_message=message.content,
            session_id=session_id,
            user_id=message.sender_id,
        )
        
        logger.info(f"[AI] Response: {response[:100]}...")
        
        # Send response back
        if message_sender:
            await message_sender.reply_text(message, response)
            
    except Exception as e:
        logger.error(f"[DingTalk] Error in normal response: {e}")
        if message_sender:
            await message_sender.reply_text(message, f"处理消息时出错：{str(e)}")


async def handle_slash_command(message: DingTalkMessage):
    """Handle slash commands"""
    content = message.content.strip()
    command = content.split()[0].lower() if content.split() else ""
    
    response = ""
    
    if command == "/help":
        response = """🤖 **钉钉AI机器人帮助**

**基本命令：**
- `/help` - 显示帮助信息
- `/new` - 开始新会话
- `/history` - 查看对话历史
- `/clear` - 清除当前会话
- `/stream` - 切换流式输出模式
- `/normal` - 切换普通输出模式

**流式输出：**
支持打字机效果的AI卡片回复，实时显示AI生成的内容。

**使用方法：**
直接发送消息即可与AI对话，AI会记住上下文。

**示例：**
```
你好，请帮我写一个Python函数
```
"""
    elif command == "/new":
        if ai_service:
            ai_service.clear_session(message.conversation_id)
            response = "✅ 已开始新会话，之前的对话历史已清除。"
        else:
            response = "❌ AI服务不可用"
    
    elif command == "/history":
        if ai_service:
            history = ai_service.get_session_history(message.conversation_id)
            if history:
                history_text = "\n".join([
                    f"{'👤' if m['role'] == 'user' else '🤖'} {m['content'][:100]}..."
                    for m in history[-10:]
                ])
                response = f"📝 **最近对话历史：**\n\n{history_text}"
            else:
                response = "📝 当前会话暂无历史记录"
        else:
            response = "❌ AI服务不可用"
    
    elif command == "/clear":
        if ai_service:
            ai_service.clear_session(message.conversation_id)
            response = "✅ 会话已清除"
        else:
            response = "❌ AI服务不可用"
    
    elif command == "/stream":
        response = "✅ 流式输出模式已启用（打字机效果）\n\n发送消息后将看到AI实时生成内容。"
    
    elif command == "/normal":
        response = "✅ 普通输出模式已启用\n\n发送消息后将一次性返回完整内容。"
    
    else:
        response = f"❓ 未知命令：{command}\n发送 `/help` 查看可用命令"
    
    if message_sender:
        await message_sender.reply_text(message, response)


# REST API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "DingTalk Bot Service",
        "status": "running",
        "version": "0.3.0",
        "features": ["streaming_card", "typewriter_effect", "official_sdk"],
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "dingtalk_connected": dingtalk_client is not None and dingtalk_client._running,
        "ai_available": ai_service is not None,
        "streaming_card_available": streaming_card_manager is not None,
    }


@app.post("/chat", response_model=MessageResponse)
async def chat(request: ChatRequest):
    """
    Chat endpoint for testing
    
    Send a message to the AI and get a response.
    Supports both streaming and non-streaming modes.
    """
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    try:
        session_id = request.session_id or "default"
        user_id = request.user_id or "api_user"
        
        if request.streaming:
            # Streaming mode - collect all chunks
            full_response = ""
            async for chunk in ai_service.stream_chat_auto(
                user_message=request.message,
                session_id=session_id,
                user_id=user_id,
            ):
                full_response += chunk
            
            return MessageResponse(
                success=True,
                message="OK (streaming mode)",
                data={"response": full_response, "session_id": session_id, "streaming": True}
            )
        else:
            # Normal mode
            response = await ai_service.chat(
                user_message=request.message,
                session_id=session_id,
                user_id=user_id,
            )
            
            return MessageResponse(
                success=True,
                message="OK",
                data={"response": response, "session_id": session_id}
            )
        
    except Exception as e:
        return MessageResponse(
            success=False,
            message=str(e),
        )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Streaming chat endpoint - returns Server-Sent Events (SSE)
    
    This endpoint demonstrates the streaming output used for
    DingTalk AI card typewriter effect.
    """
    from fastapi.responses import StreamingResponse
    
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    session_id = request.session_id or "default"
    user_id = request.user_id or "api_user"
    
    async def generate_sse():
        async for chunk in ai_service.stream_chat_auto(
            user_message=request.message,
            session_id=session_id,
            user_id=user_id,
        ):
            # Format as Server-Sent Events
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        
        # Send end marker
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
    )


@app.get("/sessions/{session_id}/history")
async def get_history(session_id: str):
    """Get conversation history for a session"""
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    history = ai_service.get_session_history(session_id)
    return {"session_id": session_id, "history": history}


@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear a conversation session"""
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    ai_service.clear_session(session_id)
    return {"success": True, "message": f"Session {session_id} cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
