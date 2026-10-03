"""翻译层：provider 抽象 + DeepSeek 实现 + SSE 流式解析。

加新后端（本地模型、网页反代……）只需：
  1. 写一个类，实现 translate() / translate_stream()
  2. 打上 @register_provider("名字")
  3. 配置里把 provider 改成那个名字

其余代码一行都不用动。
"""

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

import requests

# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class TranslateRequest:
    image_data: bytes
    media_type: str = "image/jpeg"
    prompt: str = ""
    # 术语表条目列表，每条自带「翻不翻」开关
    glossary: list = field(default_factory=list)
    # 说话人的名字要不要也翻
    translate_names: bool = False
    model: str = "deepseek-flash"
    max_tokens: int = 1024
    temperature: float = 0.2
    timeout: tuple = (5.0, 60.0)
    content_hash: str = ""


@dataclass
class TranslateResult:
    text: str
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    from_cache: bool = False
    usage: dict | None = None
    incomplete: bool = False
    reasoning_only: bool = False  # 只收到思考链、正文为空（thinking 没关掉的征兆）


@dataclass(frozen=True)
class Delta:
    """流式增量文本。"""

    text: str


@dataclass(frozen=True)
class Done:
    result: TranslateResult


@dataclass(frozen=True)
class Failed:
    error: Exception


StreamEvent = Delta | Done | Failed


# ---------------------------------------------------------------- 异常


class TranslateError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(TranslateError):
    """key 无效或没权限。"""


class RateLimitError(TranslateError):
    """限流。"""


class NetworkError(TranslateError):
    """网络或服务端问题。"""


def map_http_error(status: int, body: str) -> TranslateError:
    try:
        msg = json.loads(body)["error"]["message"]
    except Exception:
        msg = (body or "").strip()[:300] or "(空响应)"

    if status in (401, 403):
        return AuthError(f"API key 无效或没权限：{msg}", status)
    if status == 429:
        return RateLimitError(f"请求太频繁：{msg}", status)
    if status >= 500:
        return NetworkError(f"服务端错误 {status}：{msg}", status)
    return TranslateError(f"HTTP {status}：{msg}", status)


# ---------------------------------------------------------------- SSE 解析
# 这一整段是中文乱码风险最集中的地方，全是纯函数，好单测。


def iter_sse_payloads(byte_lines) -> Iterator[str]:
    """把 SSE 的字节行流合并成一条条事件负载。

    ⚠️ 调用方必须传 bytes（iter_lines(decode_unicode=False)）。
    OpenAI 兼容接口的响应头是 text/event-stream 且不带 charset，
    requests 会按 RFC 默认猜 ISO-8859-1，中文译文会全变乱码。
    """
    buf: list[str] = []
    for raw in byte_lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r")

        if line == "":  # 空行 = 事件结束
            if buf:
                yield "\n".join(buf)
                buf = []
            continue

        if line.startswith(":"):  # 心跳 / 注释行，必须跳过
            continue

        field, _, value = line.partition(":")
        if value.startswith(" "):  # 规范：冒号后可选一个空格
            value = value[1:]

        if field == "data":
            buf.append(value)
        # event: / id: / retry: 暂不处理

    if buf:
        yield "\n".join(buf)


def parse_chat_json(text: str) -> list[dict] | None:
    """解析模型返回的聊天 JSON，解析不了返回 None。

    为什么非得让模型返结构化数据：客户端要靠 source（原文）去重。
    用译文去重是不行的 —— 同一句 hello，这次翻"你好"下次翻"您好"，
    就会被当成两条不同的消息。

    容错要点（模型不总听话）：
      · 可能用 ```json ... ``` 包起来
      · 可能前后带解释文字
      · 可能根本不是合法 JSON —— 那就返回 None，让调用方退回纯文本处理
    """
    if not text:
        return None

    raw = text.strip()

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]

    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        return None

    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        return None

    items = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        if not source:
            continue
        items.append({
            "user": str(entry.get("user") or "").strip(),
            "source": source,
            "translation": str(entry.get("translation") or "").strip(),
        })

    return items or None


def format_chat_items(items: list[dict]) -> str:
    """把结构化条目拼成给人看的文本。"""
    lines = []
    for item in items:
        user = item.get("user", "")
        text = item.get("translation") or item.get("source", "")
        lines.append(f"[{user}] {text}" if user else text)
    return "\n".join(lines)


def format_glossary(entries) -> str:
    """条目列表 -> "ty=谢谢; mb=我的错"，只取勾了「翻译」的那些。"""
    if not entries:
        return "（无）"
    parts = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("translate", True):
            continue
        term = str(entry.get("term") or "").strip()
        if not term:
            continue
        translation = str(entry.get("translation") or "").strip() or term
        parts.append(f"{term}={translation}")
    return "; ".join(parts) if parts else "（无）"


def format_name_rule(translate_names: bool) -> str:
    """说话人名字怎么处理 —— 拼进提示词的那一句。"""
    if translate_names:
        return (
            "说话人的名字若是非中文，也翻成中文 —— 意译优先，"
            "比如「ウサギ」译作「兔子」、「Shadow」译作「暗影」；"
            "纯 ID、随机字符串或看不懂的就保持原样。"
        )
    return "说话人的名字保持原样，不要翻译。"


def format_keep_english(entries) -> str:
    """条目列表 -> "gg, rush, ..."，取没勾「翻译」的那些。"""
    if not entries:
        return "（无）"
    words = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("translate", True):
            continue
        term = str(entry.get("term") or "").strip()
        if term:
            words.append(term)
    return ", ".join(words) if words else "（无）"


# ---------------------------------------------------------------- provider


_PROVIDERS: dict[str, type] = {}


# ---------------------------------------------------------------- 回话提示词


_KANA = re.compile("[぀-ゟ゠-ヿ]")  # 平假名 / 片假名


def _is_japanese(target: str) -> bool:
    """目标语言是不是日语 —— 「日文」「日语」「Japanese」都算。"""
    t = (target or "").lower()
    return "日" in t or "jp" in t or "japan" in t


def reverse_glossary(
    glossary: list | None, target_japanese: bool = False
) -> list[tuple[str, str]]:
    """把术语表倒过来：中文 → 外文。

    术语表本身是给「看别人的话」用的，方向是 外文 → 中文。
    回话时方向正好相反，同一张表照样能用，倒着查就行：
        {"term": "おつ", "translation": "辛苦了"} → ("辛苦了", "おつ")

    两件按目标语言区分的事：

    · 同一个中文常对上好几条 ——「打得好」既是 wp 也是 上手い。翻日文时
      优先挑带假名的那个，否则会被映射成 wp，旁边明明有个更地道的 上手い。
    · 反过来，翻非日文时得把日文条目**整个排除**。不然「辛苦了」带着
      「→ おつ」进了提示词，翻成英文也给你吐一个 おつ 出来。
    """
    best: dict[str, tuple[int, str]] = {}  # 中文 -> (优先级, 外文)
    for entry in glossary or []:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term") or "").strip()
        trans = str(entry.get("translation") or "").strip()
        if not term or not trans or term == trans:
            continue
        is_kana = bool(_KANA.search(term))
        if is_kana and not target_japanese:
            continue
        priority = 1 if is_kana else 0
        current = best.get(trans)
        if current is None or priority > current[0]:
            best[trans] = (priority, term)
    return [(cn, foreign) for cn, (_p, foreign) in best.items()]


def build_reply_prompt(text: str, target: str, glossary: list | None = None) -> str:
    """造「把你说的话翻成外语」的提示词。

    前缀（指令 + 术语表）每个请求都一模一样，只有末尾要翻的内容在变 ——
    DeepSeek 的前缀缓存正好命中，输入按 1/50 计价。
    """
    lines = [
        f"把下面的内容翻译成{target}。",
        "",
        "要求：",
        f"1. 整句翻成{target}，要是一句完整、地道的话，"
        f"像{target}玩家在聊天框里打字的样子。",
        f"2. 句子必须完整：{target}该有的动词、助词一个都不能少，"
        "不能只把词换成外语、句子骨架还是中文。",
        "3. 只输出译文本身：不要解释，不要引号，不要标注读音，不要写原文。",
        "4. 能一句说完就别写两句 —— 聊天框里没人看长句。",
    ]

    pairs = reverse_glossary(glossary, target_japanese=_is_japanese(target))
    if pairs:
        lines += [
            "",
            "术语表：这只管「某个词该怎么说」，不是让你逐词替换。",
            "碰到下面这些中文词就用右边的说法，句子其余部分照常完整翻译：",
        ]
        lines += [f"  {cn} → {foreign}" for cn, foreign in pairs]

    lines += ["", "要翻译的内容：", text]
    return "\n".join(lines)


def build_inline_prompt(target: str, glossary: list | None = None) -> str:
    """造「读出图里的中文，翻成外语」的提示词。

    用在「你已经在游戏聊天框里打了中文」的流程上：截的是聊天输入框，
    要的是一句能直接发出去的外文，不需要聊天栏那套 JSON 结构。
    """
    lines = [
        "图片是一行游戏聊天输入框的截图，里面是玩家正在打的字。",
        f"读出图中的文字，翻译成{target}。",
        "",
        "要求：",
        f"1. 整句翻成{target}，要是一句完整、地道的话，"
        f"像{target}玩家在聊天框里打字的样子。",
        f"2. 句子必须完整：{target}该有的动词、助词一个都不能少，"
        "不能只把词换成外语、句子骨架还是中文。",
        "3. 只输出译文本身：不要引号，不要解释，不要标注读音，不要写原文。",
        "4. 图里没有文字、或者不是中文，就输出空字符串，别的什么都别写。",
    ]

    pairs = reverse_glossary(glossary, target_japanese=_is_japanese(target))
    if pairs:
        lines += [
            "",
            "术语表：这只管「某个词该怎么说」，不是让你逐词替换。",
            "碰到下面这些中文词就用右边的说法，句子其余部分照常完整翻译：",
        ]
        lines += [f"  {cn} → {foreign}" for cn, foreign in pairs]

    return "\n".join(lines)


def register_provider(name: str):
    def deco(cls):
        _PROVIDERS[name] = cls
        return cls

    return deco


class Translator:
    """所有翻译后端的基类。"""

    name = "base"
    supports_vision = True
    supports_stream = True

    def translate(self, req: TranslateRequest) -> TranslateResult:
        """非流式，一次性返回。"""
        raise NotImplementedError

    def translate_text(
        self,
        text: str,
        target: str = "英文",
        max_tokens: int = 512,
        glossary: list | None = None,
    ) -> TranslateResult:
        """纯文本翻译（不带图）。用于把你要说的话翻成外语。"""
        raise NotImplementedError

    def translate_image_text(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        target: str = "日文",
        glossary: list | None = None,
    ) -> TranslateResult:
        """看图并翻成外语，返回一行纯文本。用于「你在游戏里打了中文」那条路。"""
        raise NotImplementedError

    def translate_stream(
        self, req: TranslateRequest, cancel: threading.Event | None = None
    ) -> Iterator[StreamEvent]:
        """流式，逐个 Delta，最后 Done 或 Failed。"""
        raise NotImplementedError

    def close(self) -> None:
        pass


@register_provider("deepseek")
class DeepSeekTranslator(Translator):
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._session: requests.Session | None = None

    @property
    def session(self) -> requests.Session:
        """注意：requests.Session 不是线程安全的。

        本项目全程序只有 1 个 worker 线程做网络 IO，所以这么用是安全的。
        以后若要加第二个网络线程，这里必须改成每线程一个 Session。
        """
        if self._session is None:
            self._session = requests.Session()
        return self._session

    # ------------------------------------------------------ 请求构造

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _thinking_off(self) -> dict:
        """要不要带「关掉思考模式」那个参数 —— 只有 DeepSeek 需要。

        别的服务不认这个字段，带上会被当成非法参数拒掉（400），
        表现就是"换了地址就连不上"。所以按地址判断：里面含 deepseek 才带，
        中转地址里通常也带着这个词。
        """
        if "deepseek" in self.base_url.lower():
            return {"thinking": {"type": "disabled"}}
        return {}

    def _payload(self, req: TranslateRequest, stream: bool) -> dict:
        b64 = base64.b64encode(req.image_data).decode("ascii")
        prompt = (
            req.prompt
            .replace("{glossary}", format_glossary(req.glossary))
            .replace("{keep_english}", format_keep_english(req.glossary))
            .replace("{name_rule}", format_name_rule(req.translate_names))
        )

        return {
            "model": req.model or self.model,
            "messages": [
                {
                    # ⚠️ 图片只能放 user 消息里。放 system / assistant 会直接返回 400。
                    "role": "user",
                    "content": [
                        # 文本在前、图片在后：模型先读规则再看图，输出更稳，
                        # 而且前缀固定，能吃到 DeepSeek 的上下文缓存。
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{req.media_type};base64,{b64}",
                            },
                        },
                    ],
                }
            ],
            "stream": stream,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            # ⚠️ DeepSeek 必须显式关掉思考模式：开着的话模型把 token 先花在
            # 思考链上，实测一张终端截图 300 token 全被吃光、正文输出为空 ——
            # 用户看到的就是"按了热键但什么都没翻译出来"。关掉后同样的图：
            # 0.6s 出 209 字 vs 2.0s 出 0 字，快 3 倍、省 4 倍 token。
            # 别家不认这个参数，所以只对 DeepSeek 带，见 _thinking_off。
            **self._thinking_off(),
        }

    def _url(self) -> str:
        """拼出 chat/completions 的完整地址。

        ⚠️ 不要把结尾的 /v1 剥掉。以前剥，是因为 DeepSeek 带不带 /v1
        都能用，看着像"多写的那截"；但 OpenAI 的端点**必须**是
        api.openai.com/v1/chat/completions，剥掉就连不上。
        各家路径规则不一样，所以用户填什么前缀就照什么拼，不自作聪明。

        （填了完整地址也不会重复，直接用它。）
        """
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    # ------------------------------------------------------ 非流式

    def translate(self, req: TranslateRequest) -> TranslateResult:
        started = time.time()
        try:
            resp = self.session.post(
                self._url(),
                headers=self._headers(),
                json=self._payload(req, stream=False),
                timeout=req.timeout,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"请求失败：{exc}") from exc

        if resp.status_code != 200:
            raise map_http_error(resp.status_code, resp.text)

        obj = resp.json()
        choices = obj.get("choices") or []
        text = (choices[0].get("message") or {}).get("content", "") if choices else ""

        return TranslateResult(
            text=text,
            provider=self.name,
            model=req.model or self.model,
            latency_ms=int((time.time() - started) * 1000),
            usage=obj.get("usage"),
        )

    # ------------------------------------------------------ 纯文本

    def translate_text(
        self,
        text: str,
        target: str = "英文",
        max_tokens: int = 512,
        glossary: list | None = None,
    ) -> TranslateResult:
        """把一段文字翻成目标语言。

        和看图翻译分开走：这里不需要图，也不该带入聊天栏那套
        "忽略 UI 元素"的规则 —— 你说的话就是你说的话。
        """
        started = time.time()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": build_reply_prompt(text, target, glossary),
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            **self._thinking_off(),
        }

        try:
            resp = self.session.post(
                self._url(),
                headers=self._headers(),
                json=payload,
                timeout=(5.0, 30.0),
            )
        except requests.RequestException as exc:
            raise NetworkError(f"请求失败：{exc}") from exc

        if resp.status_code != 200:
            raise map_http_error(resp.status_code, resp.text)

        obj = resp.json()
        choices = obj.get("choices") or []
        text_out = (choices[0].get("message") or {}).get("content", "") if choices else ""

        return TranslateResult(
            text=text_out,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.time() - started) * 1000),
            usage=obj.get("usage"),
        )

    def translate_image_text(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        target: str = "日文",
        glossary: list | None = None,
    ) -> TranslateResult:
        """看图 + 翻成外语，返回一行纯文本。

        和 translate() 的分工：那个读聊天栏、吐一批 JSON 条目（别人说了啥）；
        这个读输入框、吐一句能直接发出去的外文（你要说啥）。
        """
        started = time.time()
        b64 = base64.b64encode(image_data).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    # ⚠️ 图片只能挂在 user 消息里，放 system/assistant 会 400
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": build_inline_prompt(target, glossary),
                        },
                    ],
                }
            ],
            "max_tokens": 256,
            "temperature": 0.3,
            **self._thinking_off(),
        }

        try:
            resp = self.session.post(
                self._url(),
                headers=self._headers(),
                json=payload,
                timeout=(5.0, 30.0),
            )
        except requests.RequestException as exc:
            raise NetworkError(f"请求失败：{exc}") from exc

        if resp.status_code != 200:
            raise map_http_error(resp.status_code, resp.text)

        obj = resp.json()
        choices = obj.get("choices") or []
        text_out = (choices[0].get("message") or {}).get("content", "") if choices else ""

        return TranslateResult(
            text=text_out,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.time() - started) * 1000),
            usage=obj.get("usage"),
        )

    # ------------------------------------------------------ 流式

    def translate_stream(
        self, req: TranslateRequest, cancel: threading.Event | None = None
    ) -> Iterator[StreamEvent]:
        started = time.time()

        try:
            resp = self.session.post(
                self._url(),
                headers=self._headers(),
                json=self._payload(req, stream=True),
                stream=True,
                timeout=req.timeout,
            )
        except requests.RequestException as exc:
            yield Failed(NetworkError(f"请求失败：{exc}"))
            return

        if resp.status_code != 200:
            err = map_http_error(resp.status_code, resp.text)
            resp.close()
            yield Failed(err)
            return

        collected: list[str] = []
        usage = None
        saw_reasoning = False  # 看到思考链 = thinking 没关掉，正文必然是空的

        try:
            # chunk_size=1 换最低延迟：默认的 512 会把小响应缓冲起来，
            # 视觉上就是"卡一下才出字"。
            lines = resp.iter_lines(decode_unicode=False, chunk_size=1)

            for payload in iter_sse_payloads(lines):
                if cancel is not None and cancel.is_set():
                    break
                if payload.strip() == "[DONE]":
                    break

                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue  # 代理插入的杂质很常见，跳过而不是炸掉

                if obj.get("usage"):
                    usage = obj["usage"]

                choices = obj.get("choices") or []
                if not choices:
                    continue  # 可能是只有 usage 的尾块

                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    collected.append(piece)
                    yield Delta(piece)
                elif delta.get("reasoning_content"):
                    # 思考链，翻译用不上。记一笔，好在结果为空时说清楚原因。
                    saw_reasoning = True

        except requests.exceptions.ChunkedEncodingError:
            # 断流：已收到的部分给出去，比整条丢掉强
            yield Done(
                TranslateResult(
                    text="".join(collected),
                    provider=self.name,
                    model=req.model or self.model,
                    latency_ms=int((time.time() - started) * 1000),
                    usage=usage,
                    incomplete=True,
                    reasoning_only=saw_reasoning and not collected,
                )
            )
            return
        finally:
            resp.close()

        yield Done(
            TranslateResult(
                text="".join(collected),
                provider=self.name,
                model=req.model or self.model,
                latency_ms=int((time.time() - started) * 1000),
                usage=usage,
                reasoning_only=saw_reasoning and not collected,
            )
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


# ---------------------------------------------------------------- 工厂


def get_provider(cfg, api_key: str, profile=None) -> Translator:
    """按配置造一个 provider。以后换后端只改这里对应的配置项。"""
    cls = _PROVIDERS.get(cfg.provider)
    if cls is None:
        raise TranslateError(
            f"未知的 provider：{cfg.provider}（可用：{', '.join(sorted(_PROVIDERS))}）"
        )
    p = profile or cfg.profile()
    return cls(api_key=api_key, base_url=cfg.api_base, model=p.model)


def verify_api_key(
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-flash",
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """发一个最小的纯文本请求，验证 key 能不能用。

    返回 (是否有效, 说明文字)。只花几个 token。
    """
    # ⚠️ 同样不能剥 /v1 —— 见 DeepSeekTranslator._url 的说明
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    else:
        url = f"{base}/chat/completions"

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                # 不写 1：有的模型不接受这么小的上限，会报参数错，
                # 那看起来跟"key 无效"一模一样，白折腾。
                "max_tokens": 16,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return False, f"连不上服务器：{exc}"

    if resp.status_code == 200:
        return True, "key 有效"
    return False, str(map_http_error(resp.status_code, resp.text))


if __name__ == "__main__":
    # 手动验证：python translate.py [图片路径]
    import sys
    from pathlib import Path

    import config as config_mod

    image_path = sys.argv[1] if len(sys.argv) > 1 else "debug_capture.jpg"
    data = Path(image_path).read_bytes()

    store = config_mod.ConfigStore()
    cfg = store.load()
    key = config_mod.resolve_api_key(cfg)

    if not key:
        print("没有 API key。请填进 config.json 的 api_key，或设置环境变量 GCT_API_KEY。")
        raise SystemExit(1)

    profile = cfg.profile()
    req = TranslateRequest(
        image_data=data,
        prompt=profile.prompt,
        glossary=profile.glossary,
        model=profile.model,
        max_tokens=profile.max_tokens,
        temperature=profile.temperature,
    )
    translator = get_provider(cfg, key)

    print(f"图片：{image_path}（{len(data) / 1024:.1f} KB）")
    print("--- 流式输出 ---")
    usage = None
    for event in translator.translate_stream(req):
        if isinstance(event, Delta):
            print(event.text, end="", flush=True)
        elif isinstance(event, Failed):
            print(f"\n[失败] {event.error}")
            break
        elif isinstance(event, Done):
            usage = event.result.usage
            print(f"\n--- 完成，{event.result.latency_ms} ms ---")
    if usage:
        print("usage:", usage)
    translator.close()
