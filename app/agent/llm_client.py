"""OpenAI 兼容的 Chat 客户端（GLM / 智谱 接入）。

GLM 提供 OpenAI 兼容接口，所以这里沿用最熟悉的方式：POST
``/chat/completions``，``tools`` 走标准 function-calling 字段。

默认配置为智谱 GLM：

    base_url = https://open.bigmodel.cn/api/paas/v4
    model    = glm-4-flash        (可选 glm-4.6 / glm-4-plus / glm-4-air ...)

环境变量（GLM_* 优先，其次 OPENAI_*）：

    GLM_API_KEY   / OPENAI_API_KEY     必填
    GLM_BASE_URL  / OPENAI_BASE_URL    可选
    GLM_MODEL     / OPENAI_MODEL       可选
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
# 默认用 glm-4.6：glm-4-flash 更便宜，但实测在「真正调用工具完成任务」上明显偏弱
# （只会给建议、反问用户,不执行 create_return_request）。可用 GLM_MODEL 覆盖。
DEFAULT_GLM_MODEL = "glm-4.6"
DEFAULT_EMBEDDING_MODEL = "embedding-3"


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def resolve_llm_config(base_url: str | None = None, api_key: str | None = None,
                       model: str | None = None, timeout_s: float | None = None) -> LLMConfig:
    """显式参数 > GLM_* 环境变量 > OPENAI_* 环境变量 > 默认值。"""
    return LLMConfig(
        base_url=(base_url or _first_env("GLM_BASE_URL", "OPENAI_BASE_URL")
                  or DEFAULT_GLM_BASE_URL).rstrip("/"),
        api_key=api_key or _first_env("GLM_API_KEY", "OPENAI_API_KEY") or "",
        model=model or _first_env("GLM_MODEL", "OPENAI_MODEL") or DEFAULT_GLM_MODEL,
        timeout_s=timeout_s if timeout_s is not None else 60.0,
    )


def has_llm_credentials() -> bool:
    return bool(_first_env("GLM_API_KEY", "OPENAI_API_KEY"))


class LLMError(RuntimeError):
    """HTTP 或协议层错误。"""


class ChatClient:
    """极简 OpenAI 兼容客户端：只做 chat/completions。"""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or resolve_llm_config()

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str | None = "auto", temperature: float = 0.2) -> dict:
        body: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        return self._post("/chat/completions", body)

    def embed(self, texts: list[str], model: str | None = None) -> list[dict]:
        """``/embeddings``（OpenAI 兼容），返回 ``data`` 列表（含 embedding）。"""
        response = self._post("/embeddings", {
            "model": model or DEFAULT_EMBEDDING_MODEL,
            "input": texts,
        })
        return response.get("data") or []

    # ------------------------------------------------------------------
    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.config.base_url}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:            # 4xx/5xx：带上响应体
            detail = exc.read().decode("utf-8", "replace")
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:             # 网络层
            raise LLMError(f"LLM connection error: {exc.reason}") from exc
