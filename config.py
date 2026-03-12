"""
Configuration management for DingTalk Bot Service
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Server settings
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=3030, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")
    
    # DingTalk settings
    dingtalk_client_id: str = Field(default="", description="DingTalk AppKey/Client ID")
    dingtalk_client_secret: str = Field(default="", description="DingTalk AppSecret/Client Secret")
    
    # AI settings
    ai_api_key: Optional[str] = Field(default=None, description="AI API key")
    ai_base_url: Optional[str] = Field(default=None, description="AI API base URL")
    ai_model: str = Field(default="claude-sonnet-4-20250514", description="AI model name")
    ai_system_prompt: str = Field(
        default="你是一个智能助手，帮助用户解答问题、编写代码、分析数据等。请用简洁、专业的方式回答用户的问题。",
        description="AI system prompt"
    )
    
    # Session settings
    session_timeout: int = Field(default=3600, description="Session timeout in seconds")
    max_history: int = Field(default=20, description="Max conversation history per session")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra environment variables


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get settings instance"""
    return settings
