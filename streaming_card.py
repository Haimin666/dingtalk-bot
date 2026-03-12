"""
DingTalk Streaming AI Card - Based on official dingtalk-stream-sdk-python
打字机效果流式AI卡片

参考文档：
- https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card
- https://github.com/open-dingtalk/dingtalk-stream-sdk-python

核心流程：
1. 创建AI卡片 (start) - 状态为 PROCESSING
2. 更新状态为 INPUTING - 开始输入
3. 流式更新内容 (streaming) - 打字机效果
4. 完成更新 (finish) - 状态为 FINISHED
"""
import hashlib
import json
import logging
import platform
import time
import uuid
from enum import Enum, unique
from typing import Optional, Dict, Any, AsyncGenerator

import aiohttp
import requests

from config import get_settings

logger = logging.getLogger('dingtalk_stream.card')

# 钉钉OpenAPI端点
DINGTALK_OPENAPI_ENDPOINT = "https://api.dingtalk.com"


@unique
class AICardStatus(str, Enum):
    """AI卡片状态"""
    PROCESSING = 1  # 处理中
    INPUTING = 2    # 输入中
    EXECUTING = 4   # 执行中
    FINISHED = 3    # 执行完成
    FAILED = 5      # 执行失败


class StreamingCardManager:
    """
    流式AI卡片管理器 - 基于官方SDK实现
    
    实现钉钉AI卡片的流式更新功能，呈现打字机效果。
    
    使用流程:
    1. start() - 创建AI卡片，状态为PROCESSING
    2. set_inputing() - 设置状态为INPUTING (必须在第一次streaming之前调用!)
    3. streaming() - 流式更新内容
    4. finish() - 完成更新，状态为FINISHED
    """
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = None
        self._access_token_expire_time = 0
        self._session: Optional[aiohttp.ClientSession] = None
        self._inputing_status = False  # 跟踪是否已设置为INPUTING状态
        
        # 获取卡片模板ID
        settings = get_settings()
        self.card_template_id = settings.ai_card_template_id
        
        logger.info(f"[Card] Initialized with template: {self.card_template_id}")
    
    async def _ensure_session(self):
        """确保HTTP会话存在"""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
    
    async def _get_access_token(self) -> Optional[str]:
        """获取access token"""
        await self._ensure_session()
        
        now = int(time.time())
        if self._access_token and now < self._access_token_expire_time:
            return self._access_token
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/oauth2/accessToken"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        body = {
            'appKey': self.client_id,
            'appSecret': self.client_secret,
        }
        
        try:
            async with self._session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Failed to get access token: {resp.status} - {text}")
                
                result = await resp.json()
                self._access_token = result['accessToken']
                self._access_token_expire_time = now + result['expireIn'] - 300
                logger.info(f"[Card] Got access token, expires in {result['expireIn']}s")
                return self._access_token
        except Exception as e:
            logger.error(f'[Card] Failed to get access token: {e}')
            return None
    
    async def _get_headers(self) -> Dict[str, str]:
        """获取API请求头"""
        token = await self._get_access_token()
        return {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "x-acs-dingtalk-access-token": token,
            "User-Agent": f"DingTalkStream/1.0 Python/{platform.python_version()} "
                          f"(+https://github.com/open-dingtalk/dingtalk-stream-sdk-python)",
        }
    
    async def create_and_send_card(
        self,
        card_data: dict,
        conversation_id: str,
        conversation_type: str = "1",
        sender_staff_id: str = "",
        at_sender: bool = False,
        at_all: bool = False,
        recipients: list = None,
        support_forward: bool = True,
    ) -> str:
        """
        创建并发送卡片
        
        Returns:
            卡片实例ID
        """
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("[Card] Failed to get access token for card creation")
            return ""
        
        await self._ensure_session()
        headers = await self._get_headers()
        
        # 生成卡片实例ID
        card_instance_id = str(uuid.uuid4())
        
        logger.info(f"[Card] Creating card with template: {self.card_template_id}")
        
        # Step 1: 创建卡片实例
        body = {
            "cardTemplateId": self.card_template_id,
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": card_data},
            "callbackType": "STREAM",
            "imGroupOpenSpaceModel": {"supportForward": support_forward},
            "imRobotOpenSpaceModel": {"supportForward": support_forward},
        }
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/card/instances"
        
        try:
            async with self._session.post(url, headers=headers, json=body) as resp:
                resp_text = await resp.text()
                logger.info(f"[Card] Create response: {resp.status}")
                if resp.status != 200:
                    logger.error(f"[Card] Failed to create: {resp.status} - {resp_text}")
                    return ""
        except Exception as e:
            logger.error(f"[Card] Error creating card: {e}")
            return ""
        
        # Step 2: 投放卡片
        body = {"outTrackId": card_instance_id, "userIdType": 1}
        
        if conversation_type == "2":
            # 群聊
            body["openSpaceId"] = f"dtv1.card//IM_GROUP.{conversation_id}"
            body["imGroupOpenDeliverModel"] = {
                "robotCode": self.client_id,
            }
            
            if at_all:
                body["imGroupOpenDeliverModel"]["atUserIds"] = {"@ALL": "@ALL"}
            elif at_sender and sender_staff_id:
                body["imGroupOpenDeliverModel"]["atUserIds"] = {
                    sender_staff_id: sender_staff_id,
                }
            
            if recipients:
                body["imGroupOpenDeliverModel"]["recipients"] = recipients
        else:
            # 单聊
            body["openSpaceId"] = f"dtv1.card//IM_ROBOT.{sender_staff_id}"
            body["imRobotOpenDeliverModel"] = {"spaceType": "IM_ROBOT"}
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/card/instances/deliver"
        
        try:
            async with self._session.post(url, headers=headers, json=body) as resp:
                resp_text = await resp.text()
                logger.info(f"[Card] Deliver response: {resp.status}")
                if resp.status != 200:
                    logger.error(f"[Card] Failed to deliver: {resp.status} - {resp_text}")
                    return ""
                
                logger.info(f"[Card] Card delivered successfully: {card_instance_id}")
                return card_instance_id
        except Exception as e:
            logger.error(f"[Card] Error delivering card: {e}")
            return ""
    
    async def put_card_data(self, card_instance_id: str, card_data: dict):
        """更新卡片数据"""
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("[Card] Failed to get access token for card update")
            return
        
        await self._ensure_session()
        headers = await self._get_headers()
        
        body = {
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": card_data},
        }
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/card/instances"
        
        try:
            async with self._session.put(url, headers=headers, json=body) as resp:
                resp_text = await resp.text()
                if resp.status != 200:
                    logger.error(f"[Card] Failed to update: {resp.status} - {resp_text}")
                else:
                    logger.info(f"[Card] Card updated successfully")
        except Exception as e:
            logger.error(f"[Card] Error updating card: {e}")
    
    # ============ AI卡片专用方法 ============
    
    async def ai_card_start(
        self,
        conversation_id: str,
        conversation_type: str = "1",
        sender_staff_id: str = "",
        title: str = "",
        logo: str = "",
        recipients: list = None,
        support_forward: bool = True,
    ) -> str:
        """
        发起AI卡片 - 状态为 PROCESSING
        """
        # 重置INPUTING状态
        self._inputing_status = False
        
        card_data = {"flowStatus": AICardStatus.PROCESSING}
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        return await self.create_and_send_card(
            card_data=card_data,
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
            recipients=recipients,
            support_forward=support_forward,
        )
    
    async def ai_card_set_inputing(
        self,
        card_instance_id: str,
        title: str = "",
        logo: str = "",
    ):
        """
        设置AI卡片状态为 INPUTING - 开始输入
        
        重要：必须在第一次调用 ai_card_streaming 之前调用此方法！
        """
        if self._inputing_status:
            logger.debug(f"[Card] Already in INPUTING status: {card_instance_id}")
            return
        
        card_data = {"flowStatus": AICardStatus.INPUTING}
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        logger.info(f"[Card] Setting to INPUTING: {card_instance_id}")
        await self.put_card_data(card_instance_id, card_data)
        self._inputing_status = True
    
    async def ai_card_streaming(
        self,
        card_instance_id: str,
        content: str,
        content_key: str = "msgContent",
        append: bool = False,
        finished: bool = False,
        failed: bool = False,
    ):
        """
        AI卡片流式更新 - 打字机效果
        """
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("[Card] Failed to get access token for streaming")
            return
        
        await self._ensure_session()
        headers = await self._get_headers()
        
        body = {
            "outTrackId": card_instance_id,
            "guid": str(uuid.uuid1()),
            "key": content_key,
            "content": content,
            "isFull": not append,
            "isFinalize": finished,
            "isError": failed,
        }
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/card/streaming"
        
        logger.debug(f"[Card] Streaming: len={len(content)}, isFull={not append}")
        
        try:
            async with self._session.put(url, headers=headers, json=body) as resp:
                resp_text = await resp.text()
                if resp.status != 200:
                    logger.error(f"[Card] Streaming failed: {resp.status} - {resp_text}")
        except Exception as e:
            logger.error(f"[Card] Error streaming: {e}")
    
    async def ai_card_finish(
        self,
        card_instance_id: str,
        content: str,
        title: str = "",
        logo: str = "",
    ):
        """
        完成AI卡片 - 状态为 FINISHED
        """
        card_data = {
            "msgContent": content,
            "flowStatus": AICardStatus.FINISHED,
        }
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        logger.info(f"[Card] Setting to FINISHED: {card_instance_id}")
        await self.put_card_data(card_instance_id, card_data)
    
    async def ai_card_fail(
        self,
        card_instance_id: str,
        title: str = "",
        logo: str = "",
    ):
        """
        AI卡片失败 - 状态为 FAILED
        """
        card_data = {"flowStatus": AICardStatus.FAILED}
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        logger.info(f"[Card] Setting to FAILED: {card_instance_id}")
        await self.put_card_data(card_instance_id, card_data)
    
    # ============ 便捷方法 ============
    
    async def send_and_stream(
        self,
        open_conversation_id: str,
        content_generator: AsyncGenerator[str, None],
        title: str = "🤖 AI助手",
        conversation_type: str = "1",
        sender_staff_id: str = "",
        chunk_delay: float = 0.05,
    ) -> str:
        """
        发送AI卡片并流式更新内容（一体化方法）
        """
        logger.info(f"[Card] send_and_stream: conversation={open_conversation_id}, type={conversation_type}")
        
        # 1. 创建AI卡片 (状态: PROCESSING)
        card_instance_id = await self.ai_card_start(
            conversation_id=open_conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
            title=title,
        )
        
        if not card_instance_id:
            logger.error("[Card] Failed to create AI card")
            return ""
        
        logger.info(f"[Card] AI card created: {card_instance_id}")
        
        full_content = ""
        
        try:
            # 2. 流式更新
            first_chunk = True
            async for chunk in content_generator:
                if chunk:
                    # 第一次收到内容时，设置状态为 INPUTING
                    if first_chunk:
                        await self.ai_card_set_inputing(card_instance_id, title=title)
                        first_chunk = False
                    
                    full_content += chunk
                    await self.ai_card_streaming(
                        card_instance_id=card_instance_id,
                        content=full_content,
                        append=False,  # 全量更新
                        finished=False,
                    )
                    if chunk_delay > 0:
                        import asyncio
                        await asyncio.sleep(chunk_delay)
            
            # 3. 完成 (状态: FINISHED)
            await self.ai_card_finish(
                card_instance_id=card_instance_id,
                content=full_content,
                title=title,
            )
            logger.info(f"[Card] AI card finished: {len(full_content)} chars")
            
        except Exception as e:
            logger.error(f"[Card] Error during streaming: {e}")
            await self.ai_card_fail(card_instance_id, title=title)
        
        return full_content
    
    async def close(self):
        """关闭HTTP会话"""
        if self._session:
            await self._session.close()
            self._session = None
