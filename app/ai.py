"""智谱（BigModel）接口封装：语音转文字 + 把一段话拆成结构化待办。

* 转写：POST /paas/v4/audio/transcriptions，模型 glm-asr-2512，
  只收 wav/mp3、单次 ≤30 秒 —— 所以长录音先切段再逐段转写，用 prompt 串上下文。
* 拆解：chat/completions（默认 glm-4.7-flash，目前免费），要求返回 JSON。

接口是 OpenAI 兼容的，直接复用已装好的 openai SDK。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from . import audio, nlp

DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4/"
DEFAULT_ASR = "glm-asr-2512"
DEFAULT_CHAT = "glm-4.7-flash"

PRIORITY_FROM_CN = {"高": 3, "紧急": 3, "中": 2, "低": 1, "无": 0, "": 0}

SYSTEM_PROMPT = """你是一个待办事项整理助手。用户会给你一段口语化的文字（可能是语音转写的结果，
也可能是聊天记录或会议纪要），请提取出其中所有需要"去做"的事情，整理成结构化待办。

要求：
1. 每条任务的 title 要简短明确（不超过 20 字），用动词开头，去掉口语中的填充词。
2. 判断优先级 priority：只有明显紧急或重要的才给"高"，日常琐事给"中"或"低"，无法判断给"无"。
3. 时间是关键：如果原文提到时间（如"明天下午三点"、"下周一"、"周五前"），
   请结合今天的日期换算成绝对时间，格式 "YYYY-MM-DDTHH:MM"，只到日期就填 "YYYY-MM-DD"。
   原文没有任何时间线索时 due 填 null，不要瞎猜。
4. category 从给定的分类列表里选一个最贴切的；都不合适就填 null。
5. 不要编造原文里没有的任务。原文只是闲聊而没有待办时，返回空数组。

只输出 JSON，不要解释，不要 markdown 代码块。格式：
{"tasks": [{"title": "交暑假作业", "priority": "高", "due": "2026-10-07T18:00", "category": "学习", "note": ""}]}"""


class AIError(Exception):
    pass


def _extract_json(text: str) -> Optional[dict]:
    """尽量从模型回复里抠出 JSON（有时它会包一层代码块或加几句解释）。"""
    text = (text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return None


def _fallback_lines(text: str) -> list[dict]:
    """模型没按格式返回时的兜底：按行拆成任务，起码不丢东西。"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip(" -—•*\t")
        if len(line) >= 2 and not line.startswith("{") and not line.startswith('"'):
            parsed = nlp.parse(line)
            out.append({"title": parsed.title,
                        "priority": "无",
                        "due": parsed.due.strftime("%Y-%m-%dT%H:%M") if parsed.due else None,
                        "category": parsed.list_name,
                        "note": ""})
    return out[:20]


class ZhipuAI:
    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE,
                 asr_model: str = DEFAULT_ASR, chat_model: str = DEFAULT_CHAT,
                 hotwords: str = ""):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE).strip()
        if not self.base_url.endswith("/"):
            self.base_url += "/"
        self.asr_model = asr_model or DEFAULT_ASR
        self.chat_model = chat_model or DEFAULT_CHAT
        self.hotwords = [w.strip() for w in (hotwords or "").replace("，", ",").split(",") if w.strip()]

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _client(self):
        if not self.configured:
            raise AIError("还没有填写智谱 API Key（点右上角齿轮 → 语音与 AI 里填）")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise AIError(f"缺少 openai 库：{exc}") from exc
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=60.0,
                      max_retries=1)

    @staticmethod
    def _friendly(exc: Exception) -> str:
        msg = str(exc)
        if "401" in msg or "authentication" in msg.lower() or "invalid_api_key" in msg.lower():
            return "API Key 无效或已过期，请到 open.bigmodel.cn 重新生成"
        if "403" in msg:
            return "没有权限调用该模型（可能未开通）"
        if "429" in msg:
            return "调用太频繁或额度不足，稍后再试"
        if "timeout" in msg.lower():
            return "网络超时，检查网络或代理"
        m = re.search(r'"message"\s*:\s*"([^"]+)"', msg)
        if m:
            return m.group(1)
        return msg[:200]

    # ---------------- 转写 ----------------
    def transcribe_file(self, path: str, prompt: str = "") -> str:
        client = self._client()
        try:
            with open(path, "rb") as f:
                kwargs: dict[str, Any] = {"model": self.asr_model, "file": f}
                if prompt:
                    kwargs["prompt"] = prompt[:8000]
                if self.hotwords:
                    kwargs["hotwords"] = self.hotwords
                resp = client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            raise AIError(self._friendly(exc)) from exc
        return (getattr(resp, "text", None) or "").strip()

    def transcribe(self, path: str, progress=None) -> str:
        """长时间录音自动切段，逐段转写并用前文做上下文。"""
        parts = audio.split_wav(path, chunk_seconds=28)
        texts: list[str] = []
        for i, part in enumerate(parts, 1):
            if progress:
                progress(f"正在转写第 {i}/{len(parts)} 段…")
            chunk = self.transcribe_file(part, prompt="".join(texts)[-2000:])
            if chunk:
                texts.append(chunk)
        result = "".join(texts).strip()
        if not result:
            raise AIError("没有识别到内容（录音可能太短或太安静）")
        return result

    # ---------------- 拆解 ----------------
    def extract_tasks(self, text: str, categories: Optional[list[str]] = None) -> list[dict]:
        text = (text or "").strip()
        if not text:
            return []
        today = datetime.now()
        cats = "、".join(categories or []) or "（无预设分类）"
        user = (f"今天是 {today.strftime('%Y-%m-%d')}（星期{'一二三四五六日'[today.weekday()]}）。\n"
                f"可用分类：{cats}\n\n待整理的内容：\n{text}")
        client = self._client()
        try:
            resp = client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
        except Exception as exc:
            # 有些模型不支持 response_format，退一步再试一次
            try:
                resp = client.chat.completions.create(
                    model=self.chat_model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": user}],
                    temperature=0.2)
                content = resp.choices[0].message.content or ""
            except Exception as exc2:
                raise AIError(self._friendly(exc2 if exc2 else exc)) from exc2
        return self.parse_tasks(content)

    @staticmethod
    def parse_tasks(content: str) -> list[dict]:
        """把模型返回的内容规整成任务列表（格式不对也有兜底）。"""
        data = _extract_json(content)
        raw = []
        if isinstance(data, dict):
            raw = data.get("tasks") or data.get("items") or []
            if not raw and isinstance(data.get("title"), str):
                raw = [data]
        elif isinstance(data, list):
            raw = data
        if not raw:
            return _fallback_lines(content)

        tasks: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                item = {"title": item}
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("task") or "").strip()
            if not title:
                continue
            pr = item.get("priority")
            if isinstance(pr, int):
                priority = max(0, min(3, pr))
            else:
                priority = PRIORITY_FROM_CN.get(str(pr or "").strip(), 0)
            due = item.get("due") or item.get("deadline")
            due = _normalize_due(due)
            cat = item.get("category") or item.get("list") or item.get("list_name")
            tasks.append({
                "title": title[:80],
                "priority": priority,
                "due": due,
                "category": (str(cat).strip() if cat else None),
                "note": str(item.get("note") or "").strip()[:500],
            })
        return tasks[:20]

    # ---------------- 自检 ----------------
    def test_connection(self) -> tuple[bool, str]:
        """测连通性：既测对话模型，也报告转写模型配置。"""
        try:
            client = self._client()
        except AIError as exc:
            return False, str(exc)
        try:
            resp = client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "user", "content": "回复两个字：正常"}],
                max_tokens=8, temperature=0)
            reply = (resp.choices[0].message.content or "").strip()
            return True, f"连接正常（{self.chat_model} 回复：{reply or '空'}）"
        except Exception as exc:
            return False, self._friendly(exc)


def _normalize_due(value: Any) -> Optional[str]:
    """把模型给的时间统一成 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD'。"""
    if not value:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "无", "没有"):
        return None
    text = text.replace("Z", "").replace("T", " ").strip()
    text = re.sub(r"\s+", " ", text)

    # 1) 带年份的完整格式
    for fmt, has_time in (("%Y-%m-%d %H:%M:%S", True), ("%Y-%m-%d %H:%M", True),
                          ("%Y-%m-%d", False), ("%Y/%m/%d %H:%M", True),
                          ("%Y/%m/%d", False), ("%Y年%m月%d日 %H:%M", True),
                          ("%Y年%m月%d日", False)):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%dT%H:%M") if has_time else dt.strftime("%Y-%m-%d")

    # 2) 只有月日（模型经常省略年份）：补今年，已过去则算明年。
    #    单独处理而不是交给 strptime，是因为不带年份的 strptime 在 3.15 会改行为。
    m = re.match(r"^(\d{1,2})[-/月](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?$", text)
    if m:
        now = datetime.now()
        month, day = int(m.group(1)), int(m.group(2))
        hh, mm = int(m.group(3) or 0), int(m.group(4) or 0)
        has_time = m.group(3) is not None
        for year in (now.year, now.year + 1):
            try:
                dt = datetime(year, month, day, hh, mm)
            except ValueError:
                return None
            if dt >= now or year == now.year + 1:
                return dt.strftime("%Y-%m-%dT%H:%M") if has_time else dt.strftime("%Y-%m-%d")
    # 3) 兜底交给自然语言解析器（"明天下午三点" 这类）
    parsed = nlp.parse(text)
    if parsed.due:
        return parsed.due.strftime("%Y-%m-%dT%H:%M")
    return None


def from_config(cfg) -> ZhipuAI:
    return ZhipuAI(api_key=cfg.get("ai_api_key", ""),
                   base_url=cfg.get("ai_base_url", DEFAULT_BASE),
                   asr_model=cfg.get("ai_asr_model", DEFAULT_ASR),
                   chat_model=cfg.get("ai_chat_model", DEFAULT_CHAT),
                   hotwords=cfg.get("ai_hotwords", ""))
