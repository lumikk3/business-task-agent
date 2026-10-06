"""OpenAI 兼容的 Chat 客户端（MiMo / OpenAI 接入）。

MiMo 提供 OpenAI 兼容接口，所以这里沿用标准方式：POST
``/chat/completions``，``tools`` 走标准 function-calling 字段。

默认配置为 Xiaomi MiMo：

    base_url = https://api.xiaomimimo.com/v1
    model    = mimo-v2-flash

环境变量（推荐只修改 LLM_*；旧变量仅作为兼容回退）：

    LLM_API_KEY    必填
    LLM_BASE_URL   可选
    LLM_MODEL      可选
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_MODEL = "mimo-v2-flash"
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
        if value and value.lower() not in {
            "your_key", "your_mimo_api_key", "your_api_key", "changeme"
        }:
            return value
    return None


def _load_env_file(path: str | os.PathLike[str]) -> None:
    file_path = Path(path)
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if not key:
            continue
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


def _load_default_env_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path.home() / ".hermes" / ".env",
        Path.cwd() / ".env",
        repo_root / ".env",
    ]
    for candidate in dict.fromkeys(str(p) for p in candidates):
        _load_env_file(candidate)


_load_default_env_files()


def resolve_llm_config(base_url: str | None = None, api_key: str | None = None,
                       model: str | None = None, timeout_s: float | None = None) -> LLMConfig:
    """显式参数 > LLM_* 环境变量 > 旧 provider 变量 > 默认值。"""
    return LLMConfig(
        base_url=(base_url or _first_env("LLM_BASE_URL", "MIMO_BASE_URL", "OPENAI_BASE_URL")
              or DEFAULT_MIMO_BASE_URL).rstrip("/"),
        api_key=api_key or _first_env("LLM_API_KEY", "MIMO_API_KEY", "OPENAI_API_KEY") or "",
        model=model or _first_env("LLM_MODEL", "MIMO_MODEL", "OPENAI_MODEL") or DEFAULT_MIMO_MODEL,
        timeout_s=timeout_s if timeout_s is not None else 60.0,
    )


def has_llm_credentials() -> bool:
    return bool(_first_env("LLM_API_KEY", "MIMO_API_KEY", "OPENAI_API_KEY"))


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
