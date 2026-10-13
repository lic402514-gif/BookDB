"""VLM 适配层基类（PRD §13）。

适配器只负责「图像 + 提示 → 文本」，不含任何业务判断。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ModelUnavailableError(RuntimeError):
    """本地推理服务不可用（未启动 / 模型未拉取 / 连接失败）。

    `book enrich` 捕获后给出明确提示并退出，不影响其余功能（§13）。
    """


class VLMAdapter(ABC):
    """视觉语言模型适配器统一接口。"""

    def __init__(self, *, endpoint: str, model: str):
        self.endpoint = endpoint
        self.model = model

    @abstractmethod
    def describe_image(self, image: bytes, prompt: str) -> str:
        """把图像 + 提示交给模型，返回其原始文本输出。"""

    def chat_text(self, prompt: str) -> str:
        """**纯文本**对话（不喂图）—— 供 MinerU 文本流程使用。

        与 `describe_image` 同源的模型即可胜任（多模态模型都支持纯文本）。
        默认实现抛 NotImplementedError，由适配器各自覆盖。
        """
        raise NotImplementedError(f"{type(self).__name__} 不支持纯文本对话")
