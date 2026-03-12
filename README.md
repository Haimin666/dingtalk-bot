# DingTalk AI Bot Service

钉钉机器人服务，支持流式AI卡片回复（打字机效果）

## 功能特性

- 🤖 **AI对话**: 集成智谱AI (GLM-4)，支持智能对话
- 💬 **流式卡片**: 打字机效果实时展示AI回复
- 🔄 **Stream模式**: 通过WebSocket接收钉钉消息，无需公网IP
- 📝 **多轮对话**: 支持上下文记忆的多轮对话

## 技术栈

- **Python 3.10+**
- **FastAPI** - Web框架
- **智谱AI** - 大语言模型
- **钉钉Stream SDK** - 消息接收

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Haimin666/dingtalk-bot.git
cd dingtalk-bot
```

### 2. 安装依赖

```bash
pip install -e .
# 或者
uv sync
```

### 3. 配置环境变量

复制示例配置文件：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置：

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `DINGTALK_CLIENT_ID` | 钉钉AppKey | [钉钉开放平台](https://open.dingtalk.com/) |
| `DINGTALK_CLIENT_SECRET` | 钉钉AppSecret | [钉钉开放平台](https://open.dingtalk.com/) |
| `AI_API_KEY` | 智谱AI API密钥 | [智谱AI开放平台](https://open.bigmodel.cn/) |

### 4. 运行服务

```bash
uvicorn main:app --host 0.0.0.0 --port 3030
```

## API接口

### 健康检查

```bash
GET /health
```

### 发送消息

```bash
POST /chat
Content-Type: application/json

{
  "message": "你好",
  "session_id": "optional-session-id",
  "user_id": "optional-user-id"
}
```

### 流式消息

```bash
POST /chat/stream
Content-Type: application/json

{
  "message": "写一首诗"
}
```

## 钉钉配置

1. 登录 [钉钉开放平台](https://open.dingtalk.com/)
2. 创建企业内部机器人
3. 获取 AppKey 和 AppSecret
4. 配置消息接收方式为 **Stream模式**
5. 发布机器人到企业

## 项目结构

```
dingtalk-bot/
├── main.py              # FastAPI 主应用
├── config.py            # 配置管理
├── ai_service.py        # 智谱AI 服务
├── streaming_card.py    # 流式AI卡片
├── dingtalk_client.py   # 钉钉WebSocket客户端
├── pyproject.toml       # 项目配置
├── .env.example         # 环境变量示例
└── README.md            # 项目文档
```

## 参考资料

- [钉钉Stream模式文档](https://open.dingtalk.com/document/orgapp/streaming-mode)
- [钉钉AI卡片文档](https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card)
- [钉钉Stream SDK (Python)](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)
- [智谱AI API文档](https://open.bigmodel.cn/dev/api)

## License

MIT License
