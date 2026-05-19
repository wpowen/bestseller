from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import re
import time
from time import perf_counter
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel
from bestseller.services.word_targets import model_output_token_ceiling
from bestseller.settings import AppSettings, LLMRoleSettings, RetrySettings, get_runtime_env_value


logger = logging.getLogger(__name__)

# Lazy-cached litellm module reference.  litellm is an optional dependency
# so we cannot ``import litellm`` at the top level.  Previous code called
# ``importlib.import_module("litellm")`` on every LLM request (16-20+ per
# chapter), paying dictionary-lookup overhead each time.  We cache the
# result here after the first successful import.
_litellm_module: Any = None


def _get_litellm() -> Any:
    """Return the cached litellm module, importing it on first call.

    On first import we also disable LiteLLM's internal async logging
    infrastructure.  We record every LLM call in our own ``llm_runs``
    table so we don't need LiteLLM callbacks.  Leaving them enabled
    causes a background ``LoggingWorker`` task (queue size 50 000) to
    accumulate references to full response objects inside each
    ``asyncio.run()`` call, and those tasks are "destroyed while
    pending" when the event loop closes — leaking memory across every
    chapter generation.
    """
    global _litellm_module
    if _litellm_module is None:
        _litellm_module = importlib.import_module("litellm")
        _disable_litellm_logging(_litellm_module)
    return _litellm_module


def _disable_litellm_logging(litellm: Any) -> None:
    """Turn off all LiteLLM internal success/failure callbacks and verbose logging.

    LiteLLM's ``LoggingWorker`` is only active when callbacks are registered
    or verbose mode is on.  By clearing every callback list and disabling
    verbose output we prevent the worker from enqueuing logging tasks that
    hold large response-object references across event-loop boundaries.
    """
    try:
        # Clear all callback lists — we do our own logging via llm_runs table.
        for attr in (
            "callbacks",
            "success_callback",
            "failure_callback",
            "_async_success_callback",
            "_async_failure_callback",
            "input_callback",
            "service_callback",
        ):
            if isinstance(getattr(litellm, attr, None), list):
                setattr(litellm, attr, [])

        # Disable verbose / debug output that feeds the logging worker queue.
        litellm.set_verbose = False
        litellm.verbose = False

        # Suppress request/response body logging (saves significant memory for
        # large prompts/completions stored inside the LoggingWorker queue).
        litellm.turn_off_message_logging = True

        logger.debug("LiteLLM internal logging disabled (using our own llm_runs table)")
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: worst case LiteLLM logs more than necessary.
        logger.warning("Could not fully disable LiteLLM logging: %s", exc)


LLMRole = Literal["planner", "writer", "critic", "summarizer", "editor"]


# ── Circuit Breaker ─────────────────────────────────────────────────────
#
# Prevents cascading fallback-text contamination when the LLM provider is
# down.  After ``failure_threshold`` consecutive failures, the breaker
# opens and all calls fail fast for ``recovery_timeout`` seconds.  Then a
# single probe call is allowed; if it succeeds the breaker closes.

class _CircuitBreaker:
    """Simple async-safe circuit breaker for LLM calls."""

    __slots__ = (
        "_failure_threshold",
        "_recovery_timeout",
        "_consecutive_failures",
        "_last_failure_time",
        "_state",
    )

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._state: Literal["closed", "open", "half_open"] = "closed"

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        """Reset breaker to initial closed state (useful for testing)."""
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._state = "closed"

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            logger.warning(
                "LLM circuit breaker OPEN after %d consecutive failures (recovery in %ds)",
                self._consecutive_failures,
                self._recovery_timeout,
            )

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = "half_open"
                logger.info("LLM circuit breaker HALF_OPEN — allowing probe request")
                return True
            return False
        # half_open: allow exactly one probe
        return True


_llm_breaker = _CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)

# Primary-model rate-limit cooldowns.  When a configured primary (MiniMax in
# production) returns 429/quota-exhausted, we send traffic to the configured
# fallback model for a short window.  When the window expires, the next call
# probes the primary again; a successful probe automatically switches traffic
# back without changing configuration.
_rate_limit_fallback_until: dict[str, float] = {}


# ── Rate-limit detection ────────────────────────────────────────────────
#
# 429 Too Many Requests is a transient signal from the provider — it means
# "back off and try again", not "your request is broken".  Unlike generic
# failures, we should be willing to wait much longer for these and must not
# silently swap in fallback content (which would silently degrade quality).


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detect whether an exception represents a rate-limit / 429 response.

    Handles three forms:
      * ``litellm.exceptions.RateLimitError`` (the documented class).
      * Any exception whose class name ends with ``RateLimitError``
        (defensive: litellm re-exports / provider-specific subclasses).
      * Generic exceptions carrying a ``status_code`` attribute == 429.
    """
    name = type(exc).__name__
    if name.endswith("RateLimitError"):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if isinstance(status, int) and status == 429:
        return True
    message = str(exc).lower()
    if "429" in message and ("rate" in message or "too many requests" in message):
        return True
    quota_markers = (
        "quota exceeded",
        "quota exhausted",
        "insufficient quota",
        "insufficient_quota",
        "usage limit",
        "resource exhausted",
        "too many requests",
    )
    if any(marker in message for marker in quota_markers):
        return True
    return False


def _extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Extract a ``Retry-After`` hint from a provider exception, if present.

    litellm exposes upstream response headers via ``.response.headers``
    on some error classes.  We look for a ``Retry-After`` header and
    interpret it as seconds (HTTP also allows HTTP-date, which we skip).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001
        return None
    if value is None:
        return None
    try:
        seconds = float(value)
        if seconds < 0:
            return None
        return seconds
    except (TypeError, ValueError):
        return None


# --- Opt-C: shared litellm HTTP client ----------------------------------------
#
# By default, litellm creates a fresh ``httpx.AsyncClient`` for every
# ``acompletion`` call when no shared client is configured. For OpenAI-compatible
# providers (like MiniMax via ``openai/MiniMax-M2.7-*``), this means a TLS
# handshake per call — measurably 0.5–1s of latency overhead per request, which
# adds up across the 16–20+ LLM calls per chapter.
#
# litellm exposes a documented hook: setting ``litellm.aclient_session`` to a
# long-lived ``httpx.AsyncClient`` makes the OpenAI handler reuse it
# (see ``litellm/llms/openai/common_utils.py::_get_async_http_client``).
#
# We initialize a single process-wide client lazily on first LLM call so:
#   * Test paths (``settings.llm.mock = True``) never construct it.
#   * Worker / API processes share connection pooling across all LLM calls.
#   * Errors initializing the shared client fall back silently to litellm's
#     per-call default (no behavioral regression).
# Per-event-loop litellm client cache. The web server runs each autowrite
# task in its own thread with ``asyncio.run()`` which creates a fresh event
# loop.  A single ``httpx.AsyncClient`` cannot be shared across loops — its
# internal connection pool is bound to the loop it was created on.  Re-using
# a stale client leads to "Future attached to a different loop" errors and
# cross-task response mixing.
#
# We key the cache by loop id so each ``asyncio.run()`` invocation gets its
# own pooled client, while calls within the same loop share one.
import threading as _threading

_litellm_client_by_loop: dict[int, Any] = {}
_litellm_client_lock = _threading.Lock()


def _ensure_shared_litellm_http_client() -> None:
    """Install a per-loop ``httpx.AsyncClient`` into litellm.

    Creates a fresh client for each event loop (thread-safe) and caches it
    for the loop's lifetime.  The previous process-wide singleton caused
    cross-loop contamination when two autowrite tasks ran concurrently.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — nothing to install

    loop_id = id(loop)
    with _litellm_client_lock:
        if loop_id in _litellm_client_by_loop:
            return

    try:
        import httpx

        litellm = _get_litellm()
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=300.0,
            ),
            follow_redirects=True,
        )
        litellm.aclient_session = client
        with _litellm_client_lock:
            _litellm_client_by_loop[loop_id] = client

        # Register a proper shutdown callback to close the client and remove
        # it from the cache when the event loop finishes.  This prevents the
        # memory leak where orphaned httpx clients (with their connection
        # pools and TLS state) accumulated after each asyncio.run().
        def _cleanup_client(client: Any = client, loop_id: int = loop_id) -> None:
            with _litellm_client_lock:
                _litellm_client_by_loop.pop(loop_id, None)
            try:
                # httpx.AsyncClient.aclose() is a coroutine; since the loop
                # is shutting down we close synchronously via the transport.
                client._transport.close()
            except Exception:
                pass

        # Use weakref.finalize so the callback fires when the loop is
        # garbage-collected (which happens at the end of asyncio.run()).
        import weakref
        weakref.finalize(loop, _cleanup_client)

        logger.info(
            "Installed per-loop httpx.AsyncClient into litellm (loop=%d, keepalive=10)",
            loop_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to install litellm http client for loop %d: %s",
            loop_id,
            exc,
        )


def _cleanup_stale_litellm_clients() -> None:
    """Remove entries from ``_litellm_client_by_loop`` whose httpx client is
    no longer usable (transport closed or event loop gone).

    Called periodically by the web server's watchdog to prevent unbounded
    growth in long-running processes.
    """
    with _litellm_client_lock:
        stale_ids: list[int] = []
        for lid, client in _litellm_client_by_loop.items():
            try:
                # A closed client's transport is_closed; if so it's stale.
                if getattr(client, "is_closed", False):
                    stale_ids.append(lid)
            except Exception:
                stale_ids.append(lid)
        for lid in stale_ids:
            client = _litellm_client_by_loop.pop(lid, None)
            if client is not None:
                try:
                    client._transport.close()
                except Exception:
                    pass
        if stale_ids:
            logger.info("Cleaned up %d stale litellm httpx client(s)", len(stale_ids))


class LLMCompletionRequest(BaseModel):
    logical_role: LLMRole
    model_tier: Literal["standard", "strong"] = "standard"
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    fallback_response: str = Field(min_length=1)
    prompt_template: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=32)
    project_id: UUID | None = None
    workflow_run_id: UUID | None = None
    step_run_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    max_tokens_override: int | None = Field(default=None, ge=1)

    # ── Tool-use / function-calling extensions (Batch 1 Stage 0) ──────────
    # ``tools`` is the OpenAI-style function schema list passed straight
    # through to the provider.  ``tool_choice`` is "auto" | "none" | a
    # specific ``{"type":"function","function":{"name":...}}`` dict.
    # Both are forwarded verbatim to litellm.acompletion.
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None

    # When running a multi-round tool loop, the caller needs to replay the
    # prior assistant ``tool_calls`` + ``tool`` response messages on each
    # turn.  If ``messages_override`` is provided, it REPLACES the default
    # ``[system, user]`` wrapper — callers are responsible for including
    # the system + initial user messages themselves.  This is intentional:
    # it makes the override explicit rather than silently concatenating.
    messages_override: list[dict[str, Any]] | None = None


class LLMCompletionResult(BaseModel):
    content: str
    provider: str
    model_name: str
    llm_run_id: UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None

    # ── Tool-use extensions ────────────────────────────────────────────────
    # ``tool_calls`` is a list of structured tool-call records parsed from
    # the provider's response.  ``None`` means the model returned plain
    # text; an empty list means the model was offered tools but declined.
    tool_calls: list[dict[str, Any]] | None = None
    # ``raw_message`` is the full assistant message dict (content +
    # tool_calls if any) suitable for appending to ``messages_override``
    # on the next round of a tool loop.
    raw_message: dict[str, Any] | None = None


def _hash_prompt(system_prompt: str, user_prompt: str) -> str:
    payload = f"{system_prompt}\n\n{user_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(stripped) // 2)


def _get_role_settings(settings: AppSettings, logical_role: LLMRole) -> LLMRoleSettings:
    return cast(LLMRoleSettings, getattr(settings.llm, logical_role))


def _effective_request_max_tokens(
    role_settings: LLMRoleSettings,
    request: LLMCompletionRequest,
) -> int:
    role_cap = int(role_settings.max_tokens)
    if request.max_tokens_override is None:
        return role_cap
    requested = max(1, int(request.max_tokens_override))
    if requested <= role_cap:
        return requested
    model_ceiling = model_output_token_ceiling(role_settings.model)
    if model_ceiling is None:
        return role_cap
    return min(requested, int(model_ceiling))


def _rate_limit_fallback_key(logical_role: LLMRole, role_settings: LLMRoleSettings) -> str:
    return "|".join(
        [
            logical_role,
            role_settings.model,
            role_settings.api_base or "",
            role_settings.api_key_env or "",
        ]
    )


def _build_rate_limit_fallback_settings(
    role_settings: LLMRoleSettings,
) -> LLMRoleSettings | None:
    if not role_settings.rate_limit_fallback_model:
        return None
    fallback_key_env = role_settings.rate_limit_fallback_api_key_env
    if fallback_key_env and not get_runtime_env_value(fallback_key_env):
        return None
    return role_settings.model_copy(
        update={
            "model": role_settings.rate_limit_fallback_model,
            "api_base": role_settings.rate_limit_fallback_api_base,
            "api_key_env": fallback_key_env,
            "stream": role_settings.rate_limit_fallback_stream,
            "model_override": None,
        }
    )


def _is_rate_limit_fallback_active(key: str) -> bool:
    until = _rate_limit_fallback_until.get(key)
    if until is None:
        return False
    if time.monotonic() >= until:
        _rate_limit_fallback_until.pop(key, None)
        return False
    return True


def _mark_rate_limit_fallback_active(key: str, cooldown_seconds: int) -> None:
    _rate_limit_fallback_until[key] = time.monotonic() + max(0, cooldown_seconds)


def _clear_rate_limit_fallback(key: str) -> None:
    _rate_limit_fallback_until.pop(key, None)


def _mock_content_for_request(request: LLMCompletionRequest) -> str:
    """Return deterministic mock content that can pass local functional verification."""

    content = request.fallback_response.strip()
    if (
        request.prompt_template in {"scene_writer", "scene_writer_regen"}
        and content.startswith("<!-- scene-draft-fallback")
    ):
        chapter_number = int(request.metadata.get("chapter_number") or 1)
        scene_number = int(request.metadata.get("scene_number") or 1)
        protagonist_name = str(request.metadata.get("protagonist_name") or "").strip() or "沈砚"
        supporting_name = str(request.metadata.get("supporting_name") or "").strip() or "顾临"
        context_query = str(request.metadata.get("context_query") or "")
        scene_focus = (
            "顾临与失踪巡逻舰"
            if "顾临" in context_query or chapter_number >= 2
            else ("偏移航标与旧日志" if scene_number >= 2 else "封港命令与港务官")
        )
        details = [
            ("警报", "碎潮星港的警报沿着穹顶逐层亮起，红光把每一张值守脸都切成锋利的阴影。"),
            ("钥匙", "沈砚把旧校准钥匙藏进掌心，金属边缘硌着伤口，提醒他不能再把真相交给别人的报告。"),
            ("阻力", "港务频道反复播报禁令，港务官要求他交出权限卡，巡检无人机已经贴着舱门降低高度。"),
            ("选择", "他没有和任何人争辩，而是把临时任务单折成两半，借签收动作遮住接入底层端口的手势。"),
            ("发现", "航标日志里跳出一串不该存在的校准签名，时间戳新得像刚凝固的血，签名人却写着沈砚。"),
            ("代价", "如果这条接入记录被追踪，他会重新背上七年前的事故罪名，甚至连离开星港的资格都被抹掉。"),
            ("反应", "他听见自己呼吸变轻，视野却异常清楚，所有航线偏移值像冰冷星点一样排成可追索的路径。"),
            ("人物", "港务官敲了三下封港章，声音短促而克制，像是在提醒他这里没有友情，只有流程和责任。"),
            ("推进", f"{scene_focus}不再是传闻，而变成摆在他眼前的坐标、签名和被删除的二级校准指令。"),
            ("情绪", "沈砚忽然意识到，自己这些年反复梦见的并不是舰队爆炸，而是最后一秒无人相信他的沉默。"),
            ("行动", "他把日志切成三份缓存，一份写入袖扣里的旧芯片，一份伪装成维护回执，一份投向顾临留下的暗线地址。"),
            ("压力", "倒计时还剩十七分钟，外港封锁闸已经开始下落，所有没有授权的船只都会被锁进黑潮前的死水区。"),
            ("转折", "就在他准备拔出钥匙时，底层日志自动展开第二个隐藏页，里面记录着祁镇亲自批准的非法校准。"),
            ("钩子", "屏幕最下方忽然亮起一段求救声纹：那不是事故录音的结尾，而是有人刚刚从静默航道重新发送的坐标。"),
        ]
        extensions = [
            "他把眼前的每个读数都和七年前的事故报告对照，发现官方叙述里有一条始终没有被解释的空白。",
            "远处的牵引臂拖着货箱缓慢转向，箱体编号与被封存的航线档案出现了同一组尾码。",
            "通讯器里传来三秒静默，那是顾临过去约定的危险信号，说明军方频道已经有人旁听。",
            "港口外壁震了一下，黑潮前锋擦过防波盾，所有灯光都短暂偏成不自然的蓝色。",
            "沈砚逼自己不去想失败后的审判，只把证据链按时间顺序压进脑海，像重新校准一条濒死航线。",
            "港务官的手停在封锁确认键上，迟疑短得几乎看不见，却足以证明他也听出了日志里的异常。",
            "一名年轻巡检员想要开口，又被上级频道的噪声压回去，整个星港像在共同保守一个不敢说出的秘密。",
            "沈砚把旧钥匙转过半圈，端口发出轻微咬合声，隐藏字段终于从灰色变成可读取的白色。",
            "屏幕反光里，他看见自己的脸比七年前更冷，也更像那个被所有人要求认罪的幸存者。",
            "如果现在撤退，他可以活得久一点；如果继续读取，他至少能让下一艘船知道自己为什么会死。",
            "顾临留下的暗线地址闪了一下，像有人在远处确认收到缓存，却又不敢暴露自己的位置。",
            "祁镇的批注没有情绪，只有整齐的权限编号，正因为太整齐，反而像一把擦净血迹的刀。",
            "封港闸下落到三分之二时，沈砚终于把证据包推送出去，代价是自己的实时位置被系统标红。",
            "下一秒，静默航道传回坐标回声，回声里夹着一个不该存活的舰队呼号。",
        ]
        if chapter_number >= 2:
            details = [
                ("重逢", "顾临站在巡逻舰断裂的登舰桥尽头，军装肩章被冷雾打湿，却始终没有放低枪口。"),
                ("黑匣", "失踪巡逻舰的黑匣子卡在主控台下方，外壳有烧灼痕，仍按旧军规每隔九秒闪一次蓝灯。"),
                ("军令", "军方频道命令顾临撤离，理由是静默航道存在污染风险，但撤离码和祁镇办公室的私钥相连。"),
                ("对质", "沈砚把签名实证投到顾临面前，没有解释旧怨，只问他还敢不敢看完最后一段航行记录。"),
                ("误会", "顾临的手指停在扳机外侧，他想起七年前自己被迫送走求救包时，沈砚正被全舰广播点名定罪。"),
                ("违令", "他最终切断上级监听，把舰桥门反锁，代价是自己的副官编号立刻进入军纪审查队列。"),
                ("读取", "黑匣子吐出断续声纹，第一句不是求救，而是有人低声要求舰队关闭对外定位。"),
                ("证人", "录音里出现第三个人的呼吸声，频率和祁镇私人护卫队的加密通道完全一致。"),
                ("裂痕", "沈砚没有立刻胜利的快感，只有迟来的寒意，因为真相证明顾临当年并不是抛下他。"),
                ("选择", "顾临把自己的权限徽章塞给沈砚，承认这会毁掉仕途，却能打开失踪舰最后一层舱门。"),
                ("追兵", "舱外传来磁靴落地声，巡逻队没有喊话，直接把切割枪贴上封闭门缝。"),
                ("交换", "沈砚用缓存证据换顾临三分钟掩护，顾临则要求他若自己被捕，必须把黑匣子送出边境。"),
                ("揭露", "舱门内侧刻着一排遇难者名字，其中一个名字被新鲜划掉，旁边补上了沈砚的旧权限号。"),
                ("尾声", "黑匣子最后弹出一枚坐标，坐标指向祁镇亲自封存的边境校准总库。"),
            ]
            extensions = [
                "顾临没有再说抱歉，因为那两个字太轻，压不住七年前那艘船沉没时留下的重量。",
                "沈砚蹲下去拆开保护扣，闻到绝缘层烧焦后的甜腥味，知道这艘船不是自然失联。",
                "监听灯熄灭的一瞬间，舰桥里只剩两个人的呼吸和远处推进器冷却时的金属哀鸣。",
                "旧搭档之间的信任没有恢复，只是被更大的危险逼出一条临时通道。",
                "每一段声纹都像从海底拖上来的尸体，沉默多年后终于开始指认活人。",
                "顾临把军帽扣在终端摄像头上，那动作轻得像玩笑，却等于向整套军纪宣战。",
                "沈砚听见自己的名字被录音里的陌生人念出，终于确认有人从一开始就在替他写罪名。",
                "封锁门外的切割火花照亮顾临侧脸，他没有回头，只让沈砚继续读下去。",
                "他们谁也没有提旧情，只在同一张星图前同时伸手，指向完全相同的异常节点。",
                "这一次顾临没有选择服从命令，沈砚也没有选择独自背走全部证据。",
                "追兵的脚步越来越近，黑匣子的进度条却慢得像在故意折磨每一个幸存者。",
                "沈砚把证据包压缩成三层密钥，第一层写顾临的编号，第二层写失踪舰呼号，第三层留给死者。",
                "那排名字让顾临终于变了脸，因为他认出其中一人曾在事故前夜给自己发过空白讯息。",
                "坐标出现时，整艘巡逻舰短暂恢复供电，像某个被压住的亡魂终于睁开眼睛。",
            ]
        if chapter_number >= 3:
            stage = "审计塔数据库" if chapter_number == 3 else "边境校准总库"
            secondary = supporting_name if supporting_name != protagonist_name else "沈远"
            objects = [
                "一枚被拆封的许可章",
                "半张烧焦的航线批文",
                "审计塔底层的白色门禁卡",
                "写着旧舰队呼号的缴款单",
                "被调换序号的证据柜",
                "一段只剩背景噪声的听证录音",
                "夹在档案袋里的儿童照片",
                "总库外墙新刷的禁行编号",
                "二十七分钟前才生成的拘捕令",
                "没有签名却盖过章的调度函",
                "被人为降权的遇难者名单",
                "一支还带着温度的加密笔",
                "从备用电梯落下的蓝色灰尘",
                "指向总库地下层的纸质地图",
            ]
            obstacles = [
                "值守系统把他的权限降成访客",
                f"{secondary}要求先救被扣住的线人",
                "广播里开始重复他的旧罪名",
                "两名审计员同时改口说从未见过这份文件",
                "备用通道被临时焊死",
                "对方把证据拆成三段分别转移",
                "总库的温控突然降到会冻裂设备的程度",
                "一份伪造自白被投到公共频道",
                "封锁车队提前七分钟抵达",
                "档案管理员主动递来一份明显过新的口供",
                "旧案编号被系统改写成不存在",
                "一名证人隔着玻璃向他摇头",
                "地面震动暴露了地下层仍在运转",
                "最后一道门只接受死者权限",
            ]
            details = [
                (
                    label,
                    f"{protagonist_name}在{stage}追到{objects[(index + scene_number) % len(objects)]}，"
                    f"却发现{obstacles[(index + chapter_number) % len(obstacles)]}。",
                )
                for index, (label, _sentence) in enumerate(details)
            ]
            extensions = [
                (
                    f"{secondary}没有立刻表态，只把第{index + 1}份旁证推到灯下，"
                    f"让{protagonist_name}看见这不是上一章遗留的同一条线索。"
                )
                for index in range(len(details))
            ]
        if chapter_number >= 4:
            secondary = supporting_name if supporting_name != protagonist_name else "沈远"
            final_events = [
                f"{protagonist_name}关闭总库检索屏，改用公开频道播放第一段遇难者回声。",
                f"{secondary}把封存门的死者权限交到他手里，要求他先决定是否让全城听见真相。",
                "总库中庭的穹顶灯一盏盏熄灭，所有旁观者的终端却同时亮起事故坐标。",
                f"{protagonist_name}没有再追问谁批准了命令，而是把批准链逐级投到墙面上。",
                "封锁队冲进中庭时没有开枪，因为每支枪的执法记录都已经被同步到公共屏。",
                f"{secondary}承认自己曾经删掉一段求救包，声音不高，却让人群第一次停止后退。",
                f"{protagonist_name}把旧校准钥匙折断，露出里面藏着的原始航线种子。",
                "顾铭的远程影像试图切断直播，却被遇难者名单反向锁进同一个频道。",
                "一名曾经沉默的审计员走出队列，把第二枚许可章放在地上。",
                f"{protagonist_name}终于说出七年前自己没有说完的证词，每个字都压着迟来的怒意。",
                "总库地下层传来机械解锁声，真正的校准核心从地面升起。",
                f"{secondary}挡在他身前，替他接下第一份公开逮捕令。",
                f"{protagonist_name}没有逃，他把证据包拆成四份，分别交给敌人、证人、死者家属和自己。",
                "最后一条航线恢复原始编号，边境所有灯塔在同一秒转向真实坐标。",
            ]
            details = [
                (label, final_events[index % len(final_events)])
                for index, (label, _sentence) in enumerate(details)
            ]
            extensions = [
                f"这一刻的重点不再是寻找第{index + 1}件证据，而是让证据承担公开后的代价。"
                for index in range(len(details))
            ]
        details = [(label, sentence.replace("沈砚", protagonist_name)) for label, sentence in details]
        extensions = [extension.replace("沈砚", protagonist_name) for extension in extensions]
        rotation = (chapter_number * 3 + scene_number) % len(details)
        ordered = details[rotation:] + details[:rotation]
        lead = (
            f"{protagonist_name}在警报第一声落下时就接入底层日志，指尖贴着旧校准钥匙的裂口，"
            "他没有抬头看任何人，只盯住那枚新出现的异常签名。"
            f"如果第{scene_number}轮警戒切换前不能判断真伪，他会失去本章唯一能撬开真相的入口。"
        )
        if chapter_number == 2:
            lead = (
                f"{protagonist_name}踏进失踪巡逻舰的断裂舰桥时，先看见顾临没有放下的枪口，"
                "再看见主控台下仍在闪蓝灯的黑匣子。"
                "旧怨没有给他们寒暄的余地，门外切割枪已经贴上封闭舱门。"
            )
        elif chapter_number == 3:
            lead = (
                f"{protagonist_name}在审计塔数据库门口停住脚步，先把{supporting_name}递来的许可章翻到背面，"
                "再确认章柄里藏着一枚刚被抹除的航线尾码。"
                "这一章的危险不在枪口，而在每一份文件都可能替他说谎。"
            )
        elif chapter_number >= 4:
            lead = (
                f"{protagonist_name}抵达边境校准总库时，穹顶下所有灯带同时熄灭，"
                f"{supporting_name}在黑暗里报出最后一道门的死者权限。"
                "他终于明白，终局不是找到真相，而是决定让谁承担公开真相的代价。"
            )
        paragraphs = [lead]
        ordered_extensions = extensions[rotation:] + extensions[:rotation]
        connectors = [
            "这让局势从猜测变成必须立即处理的证据。",
            "短暂沉默后，场面里的每个人都明白退路正在缩窄。",
            "这条线索不再停在屏幕上，而是直接改变了下一步行动。",
            "他压住情绪，把注意力放回可验证的细节。",
            "危险没有爆开，却在每一次读数跳动里继续逼近。",
            "这一次判断会留下痕迹，也会暴露他的位置。",
            "没有人替他解释，事实只能靠行动抢回来。",
        ]
        decision_clauses = [
            f"{protagonist_name}必须在第{scene_number}轮警戒切换前做出判断。",
            f"{protagonist_name}把可疑字段和旧案时间线重新对齐。",
            f"{protagonist_name}选择先保留证据，再承受被追踪的风险。",
            f"{protagonist_name}没有争辩，只把下一步拆成能执行的动作。",
            f"{protagonist_name}逼自己记住每一个偏移值。",
            f"{protagonist_name}知道现在撤退只会让真相再次被封存。",
            f"{protagonist_name}把恐惧压低到不影响手指的程度。",
        ]
        consequence_clauses = [
            "于是证据链多出一枚可以落地的钉子。",
            "而门外的压力也因此提前变得可见。",
            "这一步没有解决全部问题，却切开了旧叙事的第一道缝。",
            "下一次阻拦来临时，他至少知道该质问谁。",
            "沉默多年的记录终于开始指向一个活人。",
            "局势被迫向更危险、也更清楚的方向移动。",
            "读数归零前，他已经把下一枚线索送了出去。",
        ]
        if chapter_number >= 4:
            connectors = [
                "这不是追查动作，而是一次无法撤回的公开选择。",
                "人群的反应立刻改变了权力现场的重量。",
                "每个旁观者都被迫从沉默位置上站出来。",
                "真相第一次不再依赖私人逃亡来保存。",
                "对方还能封锁出口，却封不住已经扩散的声音。",
                "旧案从个人罪名变成所有人必须回应的公共问题。",
                "终局压力因此落在选择谁来承担代价上。",
            ]
            decision_clauses = [
                f"{protagonist_name}选择把证据交给公共频道，而不是继续私藏。",
                f"{protagonist_name}让每一段记录都对应一个还活着的见证人。",
                f"{protagonist_name}不再请求相信，只要求所有人核验。",
                f"{protagonist_name}把逃生时间换成完整播放时间。",
                f"{protagonist_name}用自己的旧权限承担第一轮反噬。",
                f"{protagonist_name}知道这会毁掉退路，但也能毁掉伪证链。",
                f"{protagonist_name}把最后一次选择留给那些曾被迫沉默的人。",
            ]
            consequence_clauses = [
                "于是结局开始从追杀转向清算。",
                "权力结构第一次出现公开裂缝。",
                "被抹掉的人名重新回到航线记录里。",
                "这一步让胜利有了代价，也让代价有了见证。",
                "总库再也不能把事故伪装成技术故障。",
                "他失去安全身份，却换回真相的公共生命。",
                "边境灯塔转向时，旧案终于离开了黑箱。",
            ]
        for index, ((label, sentence), extension) in enumerate(
            zip(ordered, ordered_extensions, strict=True),
            start=1,
        ):
            connector = connectors[(index + scene_number) % len(connectors)]
            decision = decision_clauses[(index + chapter_number) % len(decision_clauses)]
            consequence = consequence_clauses[(index + chapter_number + scene_number) % len(consequence_clauses)]
            paragraphs.append(
                f"{sentence}"
                f"{label}带来的压力没有重复上一段，而是把场面推向新的选择。"
                f"{connector}"
                f"{decision}"
                f"{extension}"
                f"{consequence}"
            )
        return "\n\n".join(paragraphs)
    return content


def _primary_retry_settings_for_rate_limit_fallback(
    retry_settings: RetrySettings,
) -> RetrySettings:
    # If a fallback is configured, a provider 429 should fail over immediately
    # instead of waiting through the normal patient 429 retry budget.
    return retry_settings.model_copy(update={"rate_limit_max_attempts": 1})


def _provider_from_model(model_name: str) -> str:
    if "/" not in model_name:
        return "unknown"
    return model_name.split("/", maxsplit=1)[0]


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking_tokens(text: str) -> str:
    """Remove ``<think>…</think>`` blocks emitted by reasoning models (e.g. MiniMax-M2.7).

    These blocks contain the model's internal chain-of-thought and must not
    leak into planning artifacts or novel prose.
    """
    return _THINK_TAG_RE.sub("", text).strip()


def _extract_text_content(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return _strip_thinking_tokens(raw_content)
    if isinstance(raw_content, list):
        parts: list[str] = []
        for item in raw_content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return _strip_thinking_tokens("\n".join(part for part in parts if part))
    return ""


def _lookup_field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _extract_usage_fields(usage: Any) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    input_tokens = _lookup_field(usage, "prompt_tokens")
    output_tokens = _lookup_field(usage, "completion_tokens")
    if input_tokens is None:
        input_tokens = _lookup_field(usage, "input_tokens")
    if output_tokens is None:
        output_tokens = _lookup_field(usage, "output_tokens")
    return (
        int(input_tokens) if isinstance(input_tokens, int) else None,
        int(output_tokens) if isinstance(output_tokens, int) else None,
    )


async def _collect_streaming_content(
    response: Any,
) -> tuple[str, int | None, int | None, str | None]:
    parts: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None

    async for chunk in response:
        choices = _lookup_field(chunk, "choices") or []
        if choices:
            choice = choices[0]
            delta = _lookup_field(choice, "delta")
            if delta is None:
                delta = _lookup_field(choice, "message")
            raw_content = _lookup_field(delta, "content") if delta is not None else None
            text = _extract_text_content(raw_content)
            if text:
                parts.append(text)
            chunk_finish_reason = _lookup_field(choice, "finish_reason")
            if isinstance(chunk_finish_reason, str) and chunk_finish_reason:
                finish_reason = chunk_finish_reason

        chunk_input_tokens, chunk_output_tokens = _extract_usage_fields(_lookup_field(chunk, "usage"))
        if chunk_input_tokens is not None:
            input_tokens = chunk_input_tokens
        if chunk_output_tokens is not None:
            output_tokens = chunk_output_tokens

    content = "".join(parts).strip()
    if not content:
        raise ValueError("LLM streaming response content is empty.")
    return content, input_tokens, output_tokens, finish_reason


def _extract_tool_calls(message: Any) -> list[dict[str, Any]] | None:
    """Normalise an LLM assistant message's ``tool_calls`` into plain dicts.

    Providers return tool_calls in different shapes (pydantic models, dicts,
    None).  We produce a uniform list[dict] of the form::

        [{"id": "...", "type": "function",
          "function": {"name": "...", "arguments": "{...json-string...}"}}]

    or ``None`` if the model returned plain text with no tool calls.
    """
    if message is None:
        return None
    raw = _lookup_field(message, "tool_calls")
    if not raw:
        return None
    if not isinstance(raw, list):
        return None
    normalised: list[dict[str, Any]] = []
    for call in raw:
        call_id = _lookup_field(call, "id")
        call_type = _lookup_field(call, "type") or "function"
        fn = _lookup_field(call, "function")
        fn_name = _lookup_field(fn, "name") if fn is not None else None
        fn_args = _lookup_field(fn, "arguments") if fn is not None else None
        if not isinstance(fn_name, str) or not fn_name:
            continue
        if fn_args is None:
            fn_args = ""
        elif not isinstance(fn_args, str):
            # Some providers occasionally return pre-parsed dicts; normalise
            # to JSON string so downstream consumers have a single contract.
            import json as _json  # local import to avoid top-level noise
            try:
                fn_args = _json.dumps(fn_args, ensure_ascii=False)
            except Exception:
                fn_args = str(fn_args)
        normalised.append(
            {
                "id": call_id if isinstance(call_id, str) else "",
                "type": call_type if isinstance(call_type, str) else "function",
                "function": {"name": fn_name, "arguments": fn_args},
            }
        )
    return normalised or None


def _build_raw_assistant_message(
    content: str,
    tool_calls: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Construct an OpenAI-shaped assistant message for tool-loop replay."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


async def _release_session_before_external_llm_call(session: AsyncSession) -> None:
    """Commit pending DB work before a potentially long provider call.

    Chapter generation can spend 60s+ inside a single LLM request. Holding an
    open DB transaction during that wait makes the next flush vulnerable to a
    stale/closed connection. Pipeline checkpoints already commit between major
    stages; this does the same at the LLM boundary.
    """

    commit = getattr(session, "commit", None)
    if commit is None:
        return
    in_nested_transaction = getattr(session, "in_nested_transaction", None)
    try:
        if callable(in_nested_transaction) and in_nested_transaction():
            return
        await commit()
    except Exception:
        logger.debug(
            "LLM pre-call DB checkpoint failed; continuing with existing session",
            exc_info=True,
        )


async def _persist_llm_run_safely(
    session: AsyncSession,
    llm_run: LlmRunModel,
) -> UUID | None:
    """Persist LLM telemetry without turning a good completion into a failure."""

    try:
        session.add(llm_run)
        await session.flush()
        return llm_run.id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to persist llm_run telemetry for role=%s model=%s; "
            "continuing without llm_run_id: %s: %s",
            getattr(llm_run, "logical_role", None),
            getattr(llm_run, "model_name", None),
            type(exc).__name__,
            exc,
        )
        rollback = getattr(session, "rollback", None)
        if rollback is not None:
            try:
                await rollback()
            except Exception:
                logger.debug(
                    "Rollback after llm_run telemetry failure also failed",
                    exc_info=True,
                )
        return None


async def _call_litellm(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
) -> tuple[str, int | None, int | None, str | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Invoke litellm.acompletion and return content + tokens + tool_calls.

    Returns a 6-tuple: ``(content, input_tokens, output_tokens,
    finish_reason, tool_calls, raw_assistant_message)``.  The last two are
    ``None`` when the caller did not request tools, preserving prior
    semantics for existing callers.
    """
    # Opt-C: install a shared httpx.AsyncClient into litellm on first use, so
    # subsequent calls reuse keep-alive connections to the model provider and
    # avoid per-request TLS handshakes.
    _ensure_shared_litellm_http_client()
    litellm = _get_litellm()
    acompletion = getattr(litellm, "acompletion", None)
    if acompletion is None:
        raise RuntimeError("litellm.acompletion is not available.")

    # ── Assemble messages ─────────────────────────────────────────────────
    if request.messages_override is not None:
        # Caller provides the complete message array (including system +
        # assistant + tool turns for a multi-round tool loop).  We trust
        # it and pass through verbatim.
        messages = list(request.messages_override)
    else:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]

    max_tokens = _effective_request_max_tokens(role_settings, request)

    completion_kwargs: dict[str, Any] = {
        "model": role_settings.model,
        "messages": messages,
        "temperature": role_settings.temperature,
        "max_tokens": max_tokens,
        "timeout": role_settings.timeout_seconds,
        "stream": role_settings.stream,
    }

    # ── Tool-use wiring (Batch 1 Stage 0) ─────────────────────────────────
    # Pass tools/tool_choice straight through to litellm.  When tools are
    # present we force stream=False: streaming tool_call deltas would
    # require a very different accumulator than ``_collect_streaming_content``
    # currently does, and tool-loop callers do not need token streaming.
    if request.tools:
        completion_kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            completion_kwargs["tool_choice"] = request.tool_choice
        completion_kwargs["stream"] = False

    # Only pass n when >1 — many providers (MiniMax, Gemini) ignore or
    # reject the parameter, and n=1 is the default anyway.
    if role_settings.n_candidates > 1 and not request.tools:
        # n>1 + tools is rarely meaningful and more likely to confuse
        # providers; keep n=1 whenever tools are involved.
        completion_kwargs["n"] = role_settings.n_candidates
    if role_settings.api_base:
        completion_kwargs["api_base"] = role_settings.api_base
    if role_settings.api_key_env:
        api_key = get_runtime_env_value(role_settings.api_key_env)
        if api_key:
            completion_kwargs["api_key"] = api_key

    # Enforce a hard wall-clock deadline via asyncio.wait_for.  litellm
    # passes ``timeout`` to httpx, but when a shared ``aclient_session`` is
    # installed, httpx may ignore per-request timeouts and use the client
    # default instead — allowing calls to hang far beyond the configured
    # role timeout.  The asyncio deadline guarantees cancellation.
    hard_timeout = float(role_settings.timeout_seconds) + 5.0  # small grace
    response = await asyncio.wait_for(
        acompletion(**completion_kwargs),
        timeout=hard_timeout,
    )

    if completion_kwargs["stream"]:
        content, in_tok, out_tok, finish = await asyncio.wait_for(
            _collect_streaming_content(response),
            timeout=hard_timeout,
        )
        return content, in_tok, out_tok, finish, None, None

    # When multiple candidates are returned, pick the longest (most
    # detailed) response instead of blindly using choices[0].
    choices = response.choices or []
    if not choices:
        raise ValueError("LLM response contains no choices.")
    if len(choices) == 1:
        choice = choices[0]
    else:
        choice = max(
            choices,
            key=lambda c: len(_extract_text_content(c.message.content)),
        )
    message = getattr(choice, "message", None)
    content = _extract_text_content(_lookup_field(message, "content"))
    tool_calls = _extract_tool_calls(message)
    input_tokens, output_tokens = _extract_usage_fields(getattr(response, "usage", None))
    finish_reason = getattr(choice, "finish_reason", None)

    # With tools, an empty content + non-empty tool_calls is the normal
    # "model wants to call a tool" state — do NOT raise on empty content.
    if not content.strip() and not tool_calls:
        raise ValueError(
            f"LLM response content is empty (finish_reason={finish_reason!r}, "
            f"output_tokens={output_tokens!r})."
        )
    raw_message = _build_raw_assistant_message(content.strip(), tool_calls)
    return content.strip(), input_tokens, output_tokens, finish_reason, tool_calls, raw_message


async def _call_litellm_with_retry(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
    retry_settings: RetrySettings,
) -> tuple[str, int | None, int | None, str | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Invoke ``_call_litellm`` with exponential back-off retry.

    Separate budgets for generic failures and rate-limit (HTTP 429)
    responses.  429 is transient — we retry it much more patiently,
    honour ``Retry-After`` when present, and deliberately do NOT count
    it against the circuit breaker (otherwise a burst of 429s would
    open the breaker for 60s on top of the provider's throttle).
    """
    max_attempts = max(1, retry_settings.max_attempts)
    wait_min = retry_settings.wait_min_seconds
    wait_max = retry_settings.wait_max_seconds

    rl_max_attempts = max(1, retry_settings.rate_limit_max_attempts)
    rl_wait_min = retry_settings.rate_limit_wait_min_seconds
    rl_wait_max = retry_settings.rate_limit_wait_max_seconds

    generic_attempt = 0
    rate_limit_attempt = 0

    while True:
        try:
            result = await _call_litellm(request, role_settings)
            _llm_breaker.record_success()
            return result
        except Exception as exc:
            if _is_rate_limit_error(exc):
                rate_limit_attempt += 1
                if rate_limit_attempt >= rl_max_attempts:
                    logger.error(
                        "LLM rate-limit persisted across %d attempts (%s: %s) — giving up",
                        rl_max_attempts,
                        type(exc).__name__,
                        exc,
                    )
                    raise
                retry_after = _extract_retry_after_seconds(exc)
                if retry_after is not None:
                    backoff = min(rl_wait_max, max(rl_wait_min, retry_after))
                else:
                    backoff = min(
                        rl_wait_max,
                        rl_wait_min * (2 ** (rate_limit_attempt - 1)),
                    )
                logger.warning(
                    "LLM rate-limited (429) attempt %d/%d (%s: %s) — waiting %.1fs%s",
                    rate_limit_attempt,
                    rl_max_attempts,
                    type(exc).__name__,
                    exc,
                    backoff,
                    " [Retry-After]" if retry_after is not None else "",
                )
                await asyncio.sleep(backoff)
                continue

            generic_attempt += 1
            _llm_breaker.record_failure()
            if generic_attempt >= max_attempts:
                logger.error(
                    "LLM call failed after %d attempts (%s: %s) — falling back",
                    max_attempts,
                    type(exc).__name__,
                    exc,
                )
                raise
            backoff = min(wait_max, wait_min * (2 ** (generic_attempt - 1)))
            logger.warning(
                "LLM call attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                generic_attempt,
                max_attempts,
                type(exc).__name__,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)


async def complete_text(
    session: AsyncSession,
    settings: AppSettings,
    request: LLMCompletionRequest,
) -> LLMCompletionResult:
    role_settings = _get_role_settings(settings, request.logical_role)
    if request.model_tier == "strong" and role_settings.model_override:
        role_settings = role_settings.model_copy(
            update={"model": role_settings.model_override}
        )
    rate_limit_fallback_settings = (
        _build_rate_limit_fallback_settings(role_settings)
        if settings.llm.retry.rate_limit_fallback_enabled
        else None
    )
    rate_limit_fallback_key = _rate_limit_fallback_key(
        request.logical_role,
        role_settings,
    )
    prompt_hash = _hash_prompt(request.system_prompt, request.user_prompt)
    metadata = dict(request.metadata)
    if request.max_tokens_override is not None:
        metadata["max_tokens_override"] = int(request.max_tokens_override)
    latency_ms: int | None = None
    provider = "mock"
    model_name = f"mock-{request.logical_role}"
    content = _mock_content_for_request(request)
    input_tokens = _estimate_tokens(request.system_prompt) + _estimate_tokens(request.user_prompt)
    output_tokens = _estimate_tokens(content)
    finish_reason = "mock"

    tool_calls: list[dict[str, Any]] | None = None
    raw_message: dict[str, Any] | None = None
    started_at = perf_counter()
    if not settings.llm.mock:
        try:
            await _release_session_before_external_llm_call(session)
            call_settings = role_settings
            retry_settings = settings.llm.retry
            if rate_limit_fallback_settings and _is_rate_limit_fallback_active(
                rate_limit_fallback_key
            ):
                call_settings = rate_limit_fallback_settings
                metadata["rate_limit_fallback_active"] = True
                metadata["rate_limit_fallback_primary_model"] = role_settings.model
            elif rate_limit_fallback_settings:
                retry_settings = _primary_retry_settings_for_rate_limit_fallback(
                    settings.llm.retry
                )

            provider = _provider_from_model(call_settings.model)
            model_name = call_settings.model
            (
                content,
                input_tokens,
                output_tokens,
                finish_reason,
                tool_calls,
                raw_message,
            ) = await _call_litellm_with_retry(
                request, call_settings, retry_settings,
            )
            if call_settings is role_settings:
                _clear_rate_limit_fallback(rate_limit_fallback_key)
        except Exception as exc:
            if (
                call_settings is role_settings
                and rate_limit_fallback_settings
                and _is_rate_limit_error(exc)
            ):
                _mark_rate_limit_fallback_active(
                    rate_limit_fallback_key,
                    settings.llm.retry.rate_limit_fallback_cooldown_seconds,
                )
                metadata["rate_limit_fallback_primary_model"] = role_settings.model
                metadata["rate_limit_fallback_reason"] = f"{type(exc).__name__}: {exc}"
                try:
                    provider = _provider_from_model(rate_limit_fallback_settings.model)
                    model_name = rate_limit_fallback_settings.model
                    (
                        content,
                        input_tokens,
                        output_tokens,
                        finish_reason,
                        tool_calls,
                        raw_message,
                    ) = await _call_litellm_with_retry(
                        request,
                        rate_limit_fallback_settings,
                        settings.llm.retry,
                    )
                except Exception as fallback_exc:
                    provider = "fallback"
                    model_name = f"fallback-{request.logical_role}"
                    metadata["configured_model"] = role_settings.model
                    metadata["fallback_model"] = rate_limit_fallback_settings.model
                    metadata["fallback_reason"] = (
                        f"{type(fallback_exc).__name__}: {fallback_exc}"
                    )
                    metadata["primary_rate_limit_reason"] = f"{type(exc).__name__}: {exc}"
                    metadata["retry_exhausted"] = True
                    finish_reason = "fallback"
                    logger.error(
                        "LLM rate-limit fallback FAILED for role=%s primary=%s fallback=%s "
                        "template=%s — using fallback content. Error: %s: %s",
                        request.logical_role,
                        role_settings.model,
                        rate_limit_fallback_settings.model,
                        request.prompt_template,
                        type(fallback_exc).__name__,
                        fallback_exc,
                    )
            else:
                provider = "fallback"
                model_name = f"fallback-{request.logical_role}"
                metadata["configured_model"] = role_settings.model
                metadata["fallback_reason"] = f"{type(exc).__name__}: {exc}"
                metadata["retry_exhausted"] = True
                finish_reason = "fallback"
                logger.error(
                    "LLM call FAILED for role=%s model=%s template=%s — using fallback content. "
                    "Error: %s: %s",
                    request.logical_role,
                    role_settings.model,
                    request.prompt_template,
                    type(exc).__name__,
                    exc,
                )
    latency_ms = int((perf_counter() - started_at) * 1000)

    llm_run = LlmRunModel(
        project_id=request.project_id,
        workflow_run_id=request.workflow_run_id,
        step_run_id=request.step_run_id,
        logical_role=request.logical_role,
        provider=provider,
        model_name=model_name,
        prompt_template=request.prompt_template,
        prompt_version=request.prompt_version,
        prompt_hash=prompt_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        metadata_json=metadata,
    )
    llm_run_id = await _persist_llm_run_safely(session, llm_run)

    return LLMCompletionResult(
        content=content,
        provider=provider,
        model_name=model_name,
        llm_run_id=llm_run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        raw_message=raw_message,
    )
