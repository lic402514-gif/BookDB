"""本地 VLM 适配层（PRD §13）。

程序不绑定具体模型：通过 `VLMAdapter` 统一接口调用本地推理程序。
默认实现见 `adapters/ollama.py`。新增推理框架只需再写一个适配器。
"""
