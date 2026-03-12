"""
DingTalk Streaming AI Card - Based on official dingtalk-stream-sdk-python
打字机效果流式AI卡片

参考文档：
- https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card
- https://github.com/open-dingtalk/dingtalk-stream-sdk-python

核心流程：
1. 创建AI卡片 (start) - 状态为 PROCESSING
2. 流式更新内容 (streaming) - 打字机效果
3. 完成更新 (finish) - 状态为 FINISHED
"""
import copy
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


# AI卡片模板ID (官方模板)
AI_MARKDOWN_CARD_TEMPLATE_ID = "382e4302-551d-4880-bf29-a30acfab2e71.schema"


class StreamingCardManager:
    """
    流式AI卡片管理器 - 基于官方SDK实现
    
    实现钉钉AI卡片的流式更新功能，呈现打字机效果。
    
    使用流程:
    1. start() - 创建AI卡片，状态为PROCESSING
    2. streaming() - 流式更新内容
    3. finish() - 完成更新，状态为FINISHED
    """
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = None
        self._access_token_expire_time = 0
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _ensure_session(self):
        """确保HTTP会话存在"""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
    
    def _get_access_token_sync(self) -> Optional[str]:
        """同步获取access token"""
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
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            result = response.json()
            self._access_token = result['accessToken']
            self._access_token_expire_time = now + result['expireIn'] - 300
            return self._access_token
        except Exception as e:
            logger.error(f'Failed to get access token: {e}')
            return None
    
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
                return self._access_token
        except Exception as e:
            logger.error(f'Failed to get access token: {e}')
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
    
    @staticmethod
    def gen_card_id(msg_data: dict) -> str:
        """生成卡片ID"""
        factor = "%s_%s_%s_%s_%s" % (
            msg_data.get('senderId', ''),
            msg_data.get('senderCorpId', ''),
            msg_data.get('conversationId', ''),
            msg_data.get('msgId', ''),
            str(uuid.uuid1()),
        )
        m = hashlib.sha256()
        m.update(factor.encode("utf-8"))
        return m.hexdigest()
    
    async def create_and_send_card(
        self,
        card_template_id: str,
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
        
        Args:
            card_template_id: 卡片模板ID
            card_data: 卡片数据
            conversation_id: 会话ID
            conversation_type: 会话类型 (1=单聊, 2=群聊)
            sender_staff_id: 发送者staff_id
            at_sender: 是否@发送者
            at_all: 是否@所有人
            recipients: 接收者列表
            support_forward: 是否支持转发
            
        Returns:
            卡片实例ID
        """
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("Failed to get access token for card creation")
            return ""
        
        await self._ensure_session()
        headers = await self._get_headers()
        
        # 生成卡片实例ID
        card_instance_id = str(uuid.uuid4())
        
        # Step 1: 创建卡片实例
        body = {
            "cardTemplateId": card_template_id,
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": card_data},
            "callbackType": "STREAM",
            "imGroupOpenSpaceModel": {"supportForward": support_forward},
            "imRobotOpenSpaceModel": {"supportForward": support_forward},
        }
        
        url = f"{DINGTALK_OPENAPI_ENDPOINT}/v1.0/card/instances"
        
        try:
            async with self._session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Failed to create card: {resp.status} - {text}")
                    return ""
        except Exception as e:
            logger.error(f"Error creating card: {e}")
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
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Failed to deliver card: {resp.status} - {text}")
                    return ""
                
                return card_instance_id
        except Exception as e:
            logger.error(f"Error delivering card: {e}")
            return ""
    
    async def put_card_data(self, card_instance_id: str, card_data: dict):
        """更新卡片数据"""
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("Failed to get access token for card update")
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
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Failed to update card: {resp.status} - {text}")
        except Exception as e:
            logger.error(f"Error updating card: {e}")
    
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
        
        Args:
            conversation_id: 会话ID
            conversation_type: 会话类型
            sender_staff_id: 发送者staff_id
            title: 标题
            logo: logo URL
            recipients: 接收者
            support_forward: 是否支持转发
            
        Returns:
            卡片实例ID
        """
        card_data = {"flowStatus": AICardStatus.PROCESSING}
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        return await self.create_and_send_card(
            card_template_id=AI_MARKDOWN_CARD_TEMPLATE_ID,
            card_data=card_data,
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
            recipients=recipients,
            support_forward=support_forward,
        )
    
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
        
        Args:
            card_instance_id: 卡片实例ID
            content: 内容
            content_key: 内容字段key
            append: 是否追加模式
            finished: 是否完成
            failed: 是否失败
        """
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("Failed to get access token for streaming")
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
        
        try:
            async with self._session.put(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Failed to streaming update: {resp.status} - {text}")
        except Exception as e:
            logger.error(f"Error streaming update: {e}")
    
    async def ai_card_finish(
        self,
        card_instance_id: str,
        content: str,
        title: str = "",
        logo: str = "",
    ):
        """
        完成AI卡片 - 状态为 FINISHED
        
        Args:
            card_instance_id: 卡片实例ID
            content: 最终内容
            title: 标题
            logo: logo URL
        """
        card_data = {
            "msgContent": content,
            "flowStatus": AICardStatus.FINISHED,
        }
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
        await self.put_card_data(card_instance_id, card_data)
    
    async def ai_card_fail(
        self,
        card_instance_id: str,
        title: str = "",
        logo: str = "",
    ):
        """
        AI卡片失败 - 状态为 FAILED
        
        Args:
            card_instance_id: 卡片实例ID
            title: 标题
            logo: logo URL
        """
        card_data = {"flowStatus": AICardStatus.FAILED}
        
        if title:
            card_data["msgTitle"] = title
        if logo:
            card_data["logo"] = logo
        
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
        
        Args:
            open_conversation_id: 会话ID
            content_generator: 内容生成器
            title: 标题
            conversation_type: 会话类型
            sender_staff_id: 发送者staff_id
            chunk_delay: 流式更新延迟
            
        Returns:
            完整内容
        """
        # 1. 创建AI卡片
        card_instance_id = await self.ai_card_start(
            conversation_id=open_conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
            title=title,
        )
        
        if not card_instance_id:
            logger.error("Failed to create AI card")
            return ""
        
        full_content = ""
        
        try:
            # 2. 流式更新
            async for chunk in content_generator:
                if chunk:
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
            
            # 3. 完成
            await self.ai_card_finish(
                card_instance_id=card_instance_id,
                content=full_content,
                title=title,
            )
            
        except Exception as e:
            logger.error(f"Error during streaming: {e}")
            await self.ai_card_fail(card_instance_id, title=title)
        
        return full_content
    
    async def close(self):
        """关闭HTTP会话"""
        if self._session:
            await self._session.close()
            self._session = None


# 便捷函数
def create_markdown_message(title: str, content: str) -> Dict[str, Any]:
    """创建Markdown消息"""
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content,
        }
    }


def create_text_message(content: str) -> Dict[str, Any]:
    """创建文本消息"""
    return {
        "msgtype": "text",
        "text": {
            "content": content,
        }
    }
