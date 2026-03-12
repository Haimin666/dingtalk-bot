# DingTalk Bot Service

基于 Python FastAPI 的钉钉机器人服务，集成 AI 对话功能和**流式AI卡片**（打字机效果）。

参考官方SDK: [dingtalk-stream-sdk-python](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)

## 功能特性

- 🔗 **钉钉 Stream 模式接入** - 无需公网 IP，通过 WebSocket 长连接接收消息
- 🤖 **AI 对话集成** - 支持 Anthropic Claude、OpenAI 等 LLM API
- ✨ **流式AI卡片** - 打字机效果实时显示AI回复
- 💬 **多会话管理** - 每个对话独立管理上下文历史
- 📝 **Slash 命令** - 支持丰富的命令系统
- 🔄 **自动重连** - 断线后自动恢复连接

## Stream 模式原理

钉钉 Stream 模式通过以下步骤建立连接：

```
1. 注册连接凭证
   POST https://api.dingtalk.com/v1.0/gateway/connections/open
   {
     "clientId": "AppKey",
     "clientSecret": "AppSecret",
     "subscriptions": [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}]
   }
   
   Response:
   {
     "endpoint": "wss://stream.dingtalk.com/ws/v2/stream",
     "ticket": "xxxxx"
   }

2. 建立 WebSocket 连接
   ws://{endpoint}?ticket={ticket}

3. 接收消息并处理
```

## 流式AI卡片（打字机效果）

### 概述

流式AI卡片是钉钉官方推出的创新交互方式，支持实时更新卡片内容：

```
┌─────────────────────────────────────────────────────────────┐
│                      AI助手                                  │
│                                                              │
│  你好！我是AI助手，可以帮你解决各种问题。                     │
│  我支持代码编写、数据分析、文档撰写等多种任务。               │
│  有什么可以帮助你的吗？█                                     │
│                                                              │
│  [已完成]                                                    │
└─────────────────────────────────────────────────────────────┘
```

### 核心API

1. **创建AI卡片** - `POST /v1.0/card/instances`
2. **投放卡片** - `POST /v1.0/card/instances/deliver`  
3. **流式更新** - `PUT /v1.0/card/streaming`
4. **完成更新** - `PUT /v1.0/card/instances`

### 使用示例

```python
from streaming_card import StreamingCardManager

# 初始化
manager = StreamingCardManager(
    client_id="your_client_id",
    client_secret="your_client_secret",
)

# 一体化方法（推荐）
async def ai_response_generator():
    for word in ["你好", "！", "我是", "AI", "助手"]:
        yield word
        await asyncio.sleep(0.1)

full_content = await manager.send_and_stream(
    open_conversation_id="conversation_id",
    content_generator=ai_response_generator(),
    title="🤖 AI助手",
)

# 手动控制
# 1. 创建AI卡片
card_id = await manager.ai_card_start(
    conversation_id="conversation_id",
    conversation_type="1",
    title="AI助手",
)

# 2. 流式更新
await manager.ai_card_streaming(
    card_instance_id=card_id,
    content="AI回复内容...",
    append=False,
)

# 3. 完成
await manager.ai_card_finish(
    card_instance_id=card_id,
    content="最终内容",
)
```

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Haimin666/dingtalk-bot.git
cd dingtalk-bot
```

### 2. 安装依赖

```bash
# 使用 uv (推荐)
uv sync

# 或使用 pip
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件：

```env
# DingTalk 配置 (必填)
DINGTALK_CLIENT_ID=your_app_key
DINGTALK_CLIENT_SECRET=your_app_secret

# AI 配置 (必填)
AI_API_KEY=your_api_key
AI_BASE_URL=https://api.anthropic.com
AI_MODEL=claude-sonnet-4-20250514

# 服务配置
PORT=3030
DEBUG=false
```

### 4. 运行服务

```bash
python main.py
```

## 钉钉应用配置

### 1. 创建钉钉应用

1. 访问 [钉钉开放平台](https://open-dev.dingtalk.com/)
2. 创建企业内部应用
3. 获取 AppKey 和 AppSecret

### 2. 配置机器人

1. 添加「机器人」能力
2. 消息接收模式选择 **Stream 模式**
3. 开通权限：
   - `Contact.User.Read` - 通讯录用户信息读权限
   - `IM.Chat.Read` - 会话消息读权限
   - `IM.Chat.Write` - 会话消息写权限

### 3. 配置AI卡片权限（用于流式卡片）

1. 开通 `Card.Streaming` 权限
2. 配置卡片回调地址（可选）

## 项目结构

```
dingtalk-bot/
├── main.py              # FastAPI 主应用
├── config.py            # 配置管理
├── dingtalk_client.py   # 钉钉 Stream 客户端
├── streaming_card.py    # 流式AI卡片管理器
├── ai_service.py        # AI 服务
├── pyproject.toml       # 项目依赖
├── .env.example         # 环境变量示例
└── README.md            # 本文档
```

## API 接口

### 健康检查

```bash
GET /health
```

### 聊天接口

```bash
# 普通模式
POST /chat
{
  "message": "你好",
  "session_id": "optional_session_id"
}

# 流式模式
POST /chat/stream
{
  "message": "写一首诗"
}
```

### 会话管理

```bash
# 获取历史
GET /sessions/{session_id}/history

# 清除会话
DELETE /sessions/{session_id}
```

## 环境变量说明

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `DINGTALK_CLIENT_ID` | 是 | 钉钉 AppKey |
| `DINGTALK_CLIENT_SECRET` | 是 | 钉钉 AppSecret |
| `AI_API_KEY` | 是 | AI API 密钥 |
| `AI_BASE_URL` | 否 | AI API 地址，默认 Anthropic |
| `AI_MODEL` | 否 | 模型名称，默认 claude-sonnet-4-20250514 |
| `AI_SYSTEM_PROMPT` | 否 | AI 系统提示词 |
| `PORT` | 否 | 服务端口，默认 3030 |
| `DEBUG` | 否 | 调试模式，默认 false |

## 开发调试

```bash
# 测试健康检查
curl http://localhost:3030/health

# 测试聊天接口
curl -X POST http://localhost:3030/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'

# 测试流式聊天
curl -X POST http://localhost:3030/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "写一首诗"}'
```

## 注意事项

1. **Stream 模式优势**
   - 无需公网 IP 和域名
   - 适合本地开发和内网部署
   - 自动重连机制

2. **权限配置**
   - 确保已申请所有必要的权限
   - AI卡片需要开通 `Card.Streaming` 权限

3. **流式更新频率**
   - 建议每 50-100ms 更新一次
   - 可以批量发送内容块

## 参考资料

- [钉钉开放平台](https://open.dingtalk.com/)
- [钉钉 Stream 模式文档](https://open.dingtalk.com/document/development/introduction-to-stream-mode)
- [官方 Python SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)
- [打字机效果流式AI卡片](https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card)
- [FastAPI 文档](https://fastapi.tiangolo.com/)

## License

MIT License
