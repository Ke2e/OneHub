"""T012 DeepSeekProvider：单渠道实现（research D1 候选 B 子类）。

差异点仅 _headers()（Bearer 密钥）与 base_url；通用转发逻辑全部继承基类。
密钥经 gateway 端点从 settings.deepseek_api_key 注入，真实 base_url 来自
env（config 双键名兼容，见 task_plan 关键决策表）。
"""

from app.providers.base import BaseProvider


class DeepSeekProvider(BaseProvider):
    """DeepSeek 官方 OpenAI 兼容渠道。"""

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL) -> None:
        super().__init__(base_url=base_url, api_key=api_key)

    def _headers(self) -> dict[str, str]:
        """DeepSeek 鉴权：Authorization: Bearer <key>。"""
        return {"Authorization": f"Bearer {self.api_key}"}