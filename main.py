"""
Main FastAPI Application for DingTalk Bot Service
支持流式AI卡片回复（打字机效果）

基于钉钉官方SDK: https://github.com/open-dingtalk/dingtalk-stream-sdk-python
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import get_settings
from dingtalk_client import (
    DingTalkStreamClient,
    DingTalkMessage,
    Credential,
)
from ai_service import AIService
from streaming_card import StreamingCardManager

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-16s %(levelname)-8s %(message)s'
)
logger = logging.getLogger('dingtalk_bot')


# Global instances
settings = get_settings()
dingtalk_client: Optional[DingTalkStreamClient] = None
ai_service: Optional[AIService] = None
streaming_card_manager: Optional[StreamingCardManager] = None


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    dingtalk_connected: bool
    ai_available: bool
    streaming_card_available: bool


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response model"""
    success: bool
    message: str
    response: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global dingtalk_client, ai_service, streaming_card_manager
    
    logger.info("[Bot] Starting DingTalk Bot Service with Streaming AI Card...")
    
    # Initialize AI service
    ai_service = AIService()
    logger.info("[Bot] AI Service initialized")
    
    # Initialize DingTalk components
    if settings.dingtalk_client_id and settings.dingtalk_client_secret:
        credential = Credential(
            client_id=settings.dingtalk_client_id,
            client_secret=settings.dingtalk_client_secret,
        )
        
        # Initialize streaming card manager
        streaming_card_manager = StreamingCardManager(
            client_id=settings.dingtalk_client_id,
            client_secret=settings.dingtalk_client_secret,
        )
        logger.info("[Bot] Streaming Card Manager initialized")
        
        # Initialize and start DingTalk Stream client
        dingtalk_client = DingTalkStreamClient(
            credential=credential,
            on_message=handle_dingtalk_message,
        )
        
        # Start DingTalk client in background
        asyncio.create_task(dingtalk_client.start())
        logger.info("[Bot] DingTalk Stream Client started")
    else:
        logger.warning("[Bot] Warning: DingTalk credentials not configured")
    
    yield
    
    # Cleanup
    logger.info("[Bot] Shutting down...")
    if dingtalk_client:
        await dingtalk_client.stop()
    if streaming_card_manager:
        await streaming_card_manager.close()
    if ai_service:
        await ai_service.close()
    logger.info("[Bot] Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="DingTalk Bot Service",
    description="DingTalk robot service with AI integration and streaming card support",
    version="1.0.0",
    lifespan=lifespan,
)


async def handle_dingtalk_message(message: DingTalkMessage):
    """
    Handle incoming DingTalk message
    
    This is called when a message is received from DingTalk Stream.
    """
    logger.info(f"[Bot] Received message from {message.sender_name}: {message.content[:50]}...")
    
    if not ai_service:
        logger.warning("[Bot] AI service not available")
        return
    
    # Skip empty messages
    if not message.content or not message.content.strip():
        return
    
    try:
        # Generate session ID from conversation ID
        session_id = message.conversation_id
        
        # Use streaming AI card response
        if streaming_card_manager:
            await handle_streaming_response(message, session_id)
        else:
            logger.warning("[Bot] Streaming card manager not available")
            
    except Exception as e:
        logger.error(f"[Bot] Error processing message: {e}")


async def handle_streaming_response(message: DingTalkMessage, session_id: str):
    """
    Handle message with streaming AI card response (typewriter effect)
    """
    logger.info(f"[Bot] Using streaming card response for session: {session_id}")
    
    try:
        # Create content generator from AI service
        async def content_generator():
            async for chunk in ai_service.stream_chat(
                user_message=message.content,
                session_id=session_id,
                user_id=message.sender_id,
            ):
                yield chunk
        
        # Send AI card and stream content
        full_content = await streaming_card_manager.send_and_stream(
            open_conversation_id=message.conversation_id,
            content_generator=content_generator(),
            title="🤖 AI助手",
            conversation_type=message.conversation_type,
            sender_staff_id=message.sender_staff_id,
        )
        
        logger.info(f"[Bot] Completed streaming: {len(full_content)} chars")
        
    except Exception as e:
        logger.error(f"[Bot] Error in streaming response: {e}")


# REST API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "DingTalk Bot Service",
        "status": "running",
        "version": "1.0.0",
        "features": ["streaming_card", "typewriter_effect", "zhipu_ai"],
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        dingtalk_connected=dingtalk_client is not None and dingtalk_client._running,
        ai_available=ai_service is not None,
        streaming_card_available=streaming_card_manager is not None,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat endpoint for testing
    
    Send a message to the AI and get a response.
    """
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    try:
        session_id = request.session_id or "default"
        user_id = request.user_id or "api_user"
        
        # Get AI response
        response = await ai_service.chat(
            user_message=request.message,
            session_id=session_id,
            user_id=user_id,
        )
        
        return ChatResponse(
            success=True,
            message="OK",
            response=response,
        )
        
    except Exception as e:
        return ChatResponse(
            success=False,
            message=str(e),
        )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Streaming chat endpoint - returns Server-Sent Events (SSE)
    """
    from fastapi.responses import StreamingResponse
    
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")
    
    session_id = request.session_id or "default"
    user_id = request.user_id or "api_user"
    
    async def generate_sse():
        async for chunk in ai_service.stream_chat(
            user_message=request.message,
            session_id=session_id,
            user_id=user_id,
        ):
            # Format as Server-Sent Events
            yield f"data: {chunk}\n\n"
        
        # Send end marker
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
