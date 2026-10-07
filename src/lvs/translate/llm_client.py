"""OpenAI 兼容 LLM 客户端。

兼容：llama.cpp (llama-server) / Ollama / LM Studio / vLLM / DeepSeek / 任何 /v1 接口。
只用 /chat/completions 和 /models 两个端点，不依赖 openai SDK 之外的东西。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)


class LlmError(Exception):
    """LLM 调用错误。"""


class LlmConnectionError(LlmError):
    """连不上服务。"""


@dataclass
class LlmConfig:
    base_url: str = "http://127.0.0.1:8080/v1"
    api_key: str = "sk-no-key-required"
    model: str = ""
    temperature: float = 0.3
    top_p: float = 0.9
    max_tokens: int = 4096
    timeout: float = 300.0
    max_retries: int = 3
    # 采样/惩罚（部分本地推理器支持，不支持会被忽略）
    repeat_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    # 是否请求 JSON 输出模式
    json_mode: bool = False
    # Qwen3/Qwen3.5 等 reasoning 模型：通过 chat template 关闭思考，直接输出译文。
    disable_thinking: bool = False
    # 额外请求体字段（透传给服务端）
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg) -> "LlmConfig":
        return cls(
            base_url=str(cfg.get("translate.base_url", "http://127.0.0.1:8080/v1")),
            api_key=str(cfg.get("translate.api_key", "sk-no-key-required")),
            model=str(cfg.get("translate.model", "")),
            temperature=float(cfg.get("translate.temperature", 0.3)),
            top_p=float(cfg.get("translate.top_p", 0.9)),
            max_tokens=int(cfg.get("translate.max_tokens", 4096)),
            timeout=float(cfg.get("translate.timeout", 300)),
            max_retries=int(cfg.get("translate.max_retries", 3)),
            repeat_penalty=cfg.get("translate.repeat_penalty"),
            presence_penalty=cfg.get("translate.presence_penalty"),
            frequency_penalty=cfg.get("translate.frequency_penalty"),
            seed=cfg.get("translate.seed"),
            json_mode=bool(cfg.get("translate.json_mode", False)),
            disable_thinking=bool(cfg.get("translate.disable_thinking", False)),
            extra_body=dict(cfg.get("translate.extra_body") or {}),
        )

    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"

    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


class LlmClient:
    """极简 OpenAI 兼容客户端，带重试与并发。"""

    def __init__(self, config: LlmConfig | None = None) -> None:
        self.config = config or LlmConfig()
        self._session = requests.Session()
        self._resolved_model: str | None = None

    # ------------------------------------------------------------------
    # 服务发现
    # ------------------------------------------------------------------
    def list_models(self, timeout: float | None = None) -> list[str]:
        url = self.config.models_url()
        try:
            r = self._session.get(
                url,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                timeout=timeout or 10,
            )
        except requests.RequestException as exc:
            raise LlmConnectionError(
                f"连接失败 {url}\n{_hint(exc)}\n{exc}"
            ) from exc
        if r.status_code != 200:
            raise LlmError(f"{url} 返回 {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise LlmError(f"{url} 返回的不是 JSON: {r.text[:200]}") from exc
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    def resolve_model(self) -> str:
        """确定用哪个模型名。配置为空时取服务端第一个。"""
        if self.config.model:
            return self.config.model
        if self._resolved_model:
            return self._resolved_model
        models = self.list_models()
        if not models:
            raise LlmError("服务端没有可用模型，请在设置里填写模型名")
        self._resolved_model = models[0]
        log.info("自动选用模型: %s", self._resolved_model)
        return self._resolved_model

    def ping(self, timeout: float = 5.0) -> tuple[bool, str]:
        try:
            models = self.list_models(timeout=timeout)
        except LlmError as exc:
            return False, str(exc)
        return True, f"{len(models)} 个模型: " + ", ".join(models[:5])

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool | None = None,
        stop: list[str] | None = None,
        cancel: Any = None,
    ) -> str:
        """单次对话，返回文本内容。

        cancel: 取消令牌（.is_set()）。非空时采用流式读，每个 chunk 前检查，
        取消则断开连接让服务端停止生成，并抛 _Cancelled。
        """
        body: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "top_p": self.config.top_p,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": False,
        }
        if stop:
            body["stop"] = stop
        if json_mode if json_mode is not None else self.config.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.config.disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
            # LM Studio OpenAI 兼容接口使用 reasoning_effort=none；
            # llama.cpp 等服务则读取上面的 chat_template_kwargs。
            body["reasoning_effort"] = "none"
        for key in ("repeat_penalty", "presence_penalty", "frequency_penalty", "seed"):
            val = getattr(self.config, key, None)
            if val is not None:
                body[key] = val
        body.update(self.config.extra_body)

        last_err: Exception | None = None
        for attempt in range(max(1, self.config.max_retries)):
            try:
                return self._post_chat(body, cancel=cancel)
            except LlmConnectionError:
                raise  # 连不上就别重试了，浪费时间
            except (_RetryableErrorTypes) as exc:  # type: ignore[misc]
                last_err = exc
                if attempt + 1 >= self.config.max_retries:
                    break
                wait = min(20.0, 1.5 ** attempt + random.random())
                log.warning("LLM 调用失败(第%d次)，%.1fs 后重试: %s", attempt + 1, wait, exc)
                time.sleep(wait)
            except LlmError:
                raise

        raise LlmError(f"LLM 调用重试 {self.config.max_retries} 次仍失败: {last_err}")

    def _post_chat(self, body: dict[str, Any], cancel: Any = None) -> str:
        url = self.config.chat_url()
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        content = self._post_chat_stream(body, url, headers, cancel)
        if content or (cancel is not None and cancel.is_set()):
            return content
        # 流式无输出（个别服务端对 stream 支持不佳）→ 退回非流式兑底
        return self._post_chat_nonstream(dict(body), url, headers)

    def _post_chat_stream(
        self, body: dict[str, Any], url: str, headers: dict[str, str], cancel: Any
    ) -> str:
        """流式对话：逐 chunk 累积，取消时断连让服务端停止生成。"""
        req_body = dict(body)
        req_body["stream"] = True
        r = self._do_post(url, headers, req_body)

        # 400 降级：去掉服务端不认识的字段重试
        for field in ("response_format", "chat_template_kwargs", "reasoning_effort"):
            if r.status_code == 400 and field in req_body:
                r.close()
                req_body.pop(field, None)
                r = self._do_post(url, headers, req_body)

        if r.status_code == 404:
            r.close()
            raise LlmError(
                f"{url} 返回 404 —— base_url 可能不对。\n"
                "正确写法应包含 /v1，例如 http://127.0.0.1:8080/v1"
            )
        if r.status_code in (429, 500, 502, 503, 504):
            msg = _safe_text(r)
            r.close()
            raise _ServerErr(f"HTTP {r.status_code}: {msg}")
        if r.status_code != 200:
            msg = _safe_text(r)
            r.close()
            raise LlmError(f"HTTP {r.status_code}: {msg}")

        parts: list[str] = []
        try:
            for raw in _iter_sse_lines(r):
                if cancel is not None and cancel.is_set():
                    raise _Cancelled()
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if piece:
                    parts.append(piece)
        except requests.Timeout as exc:
            raise _TimeoutErr(f"请求超时({self.config.timeout}s)") from exc
        except requests.RequestException as exc:
            raise LlmError(f"流式读取异常: {exc}") from exc
        finally:
            # 关闭连接：取消时这里断开 TCP，服务端（llama.cpp/Ollama）会停止生成
            r.close()

        return "".join(parts)

    def _do_post(self, url: str, headers: dict[str, str], body: dict[str, Any]):
        try:
            return self._session.post(
                url, headers=headers, json=body, stream=True, timeout=self.config.timeout
            )
        except requests.Timeout as exc:
            raise _TimeoutErr(f"请求超时({self.config.timeout}s)") from exc
        except requests.ConnectionError as exc:
            raise LlmConnectionError(f"无法连接 {url}\n{_hint(exc)}") from exc
        except requests.RequestException as exc:
            raise LlmError(f"请求异常: {exc}") from exc

    def _post_chat_nonstream(
        self, body: dict[str, Any], url: str, headers: dict[str, str]
    ) -> str:
        """非流式兑底（旧逻辑，用于流式无输出的服务端）。"""
        body["stream"] = False
        try:
            r = self._session.post(
                url, headers=headers, json=body, timeout=self.config.timeout
            )
        except requests.Timeout as exc:
            raise _TimeoutErr(f"请求超时({self.config.timeout}s)") from exc
        except requests.ConnectionError as exc:
            raise LlmConnectionError(f"无法连接 {url}\n{_hint(exc)}") from exc
        except requests.RequestException as exc:
            raise LlmError(f"请求异常: {exc}") from exc

        if r.status_code == 400 and "response_format" in body:
            body.pop("response_format", None)
            r = self._session.post(url, headers=headers, json=body, timeout=self.config.timeout)
        if r.status_code == 400 and "chat_template_kwargs" in body:
            body.pop("chat_template_kwargs", None)
            r = self._session.post(url, headers=headers, json=body, timeout=self.config.timeout)
        if r.status_code == 400 and "reasoning_effort" in body:
            body.pop("reasoning_effort", None)
            r = self._session.post(url, headers=headers, json=body, timeout=self.config.timeout)
        if r.status_code == 404:
            raise LlmError(
                f"{url} 返回 404 —— base_url 可能不对。\n"
                "正确写法应包含 /v1，例如 http://127.0.0.1:8080/v1"
            )
        if r.status_code in (429, 500, 502, 503, 504):
            raise _ServerErr(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            raise LlmError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise _ServerErr(f"返回非 JSON: {r.text[:200]}") from exc
        choices = data.get("choices") or []
        if not choices:
            err = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
            raise _ServerErr(f"响应没有 choices: {err or str(data)[:200]}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content is None:
            content = msg.get("reasoning_content") or choices[0].get("text") or ""
        return str(content)

    def close(self) -> None:
        self._session.close()


# --------------------------------------------------------------------------
class _RetryableError(Exception):
    pass


class _TimeoutErr(_RetryableError):
    pass


class _ServerErr(_RetryableError):
    pass


_RetryableErrorTypes = (_RetryableError,)


def _safe_text(r) -> str:
    """安全读响应体文本（流式对象在 close 前调用）。"""
    try:
        return (r.text or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


def _iter_sse_lines(r):
    """按 \n 读 SSE 行（字节级 + UTF-8 解码）。

    不用 requests 的 iter_lines：其内部 splitlines 会把字节 0x85(NEL) 等
    误判为换行符，破坏含多字节字符的 data 行。这里只按 0x0A 切行。
    """
    buf = b""
    for chunk in r.iter_content(chunk_size=1024):
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace")
    if buf:
        yield buf.decode("utf-8", errors="replace")


def _hint(exc: Exception) -> str:
    msg = str(exc).lower()
    if "connection refused" in msg or "actively refused" in msg or "10061" in msg:
        return (
            "服务未启动。本地起一个：\n"
            "  llama.cpp : llama-server -m <模型.gguf> -c 8192 -ngl 99 --port 8080\n"
            "  Ollama    : ollama serve   （base_url 用 http://127.0.0.1:11434/v1）\n"
            "  LM Studio : 打开 Local Server 开关"
        )
    if "name or service not known" in msg or "getaddrinfo" in msg:
        return "域名解析失败，检查 base_url 拼写或代理设置。"
    return ""


# --------------------------------------------------------------------------
# 并发执行辅助（用于批翻译）
# --------------------------------------------------------------------------
def run_concurrent(
    fn: Callable[[int, Any], Any],
    items: list[Any],
    concurrency: int = 1,
    cancel: Any = None,
    on_done: Callable[[int, Any, Exception | None], None] | None = None,
) -> list[Any]:
    """按并发度执行 fn(index, item)，返回结果列表（保持顺序）。

    并发>1 时用线程池；cancel 令牌在每个任务开始前检查。
    """
    results: list[Any] = [None] * len(items)

    if concurrency <= 1 or len(items) <= 1:
        for i, item in enumerate(items):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            try:
                results[i] = fn(i, item)
                if on_done:
                    on_done(i, results[i], None)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, _Cancelled):
                    raise
                if on_done:
                    on_done(i, None, exc)
                else:
                    raise
        return results

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, item in enumerate(items):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            futures[pool.submit(fn, i, item)] = i
        try:
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                    if on_done:
                        on_done(i, results[i], None)
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, _Cancelled):
                        raise
                    if on_done:
                        on_done(i, None, exc)
                    else:
                        raise
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return results


class _Cancelled(Exception):
    """内部取消信号。"""


# --------------------------------------------------------------------------
# 从 LLM 回复里抠 JSON（本地小模型经常裹着解释文字）
# --------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """尽最大努力从回复中解析出 JSON。失败抛 LlmError。"""
    if text is None:
        raise LlmError("模型返回为空")
    raw = text.strip()
    if not raw:
        raise LlmError("模型返回为空")

    # 1) 直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2) 代码块里的
    for m in _FENCE.finditer(raw):
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue

    # 3) 找最外层的 {...} 或 [...]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            snippet = raw[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                fixed = _repair_json(snippet)
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    continue

    raise LlmError(f"无法从回复中解析 JSON: {raw[:300]}")


def _repair_json(s: str) -> str:
    """修补本地模型常见 JSON 瑕疵：尾逗号、中文标点、单引号。"""
    out = s
    out = re.sub(r",\s*([}\]])", r"\1", out)          # 尾逗号
    out = out.replace("，", ",").replace("：", ":")    # 中文标点
    out = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', out)   # 单引号键
    out = re.sub(r":\s*'([^']*)'", r': "\1"', out)     # 单引号值
    return out
