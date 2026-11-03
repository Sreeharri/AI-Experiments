#!/usr/bin/env python3
"""Small client for the Qwen3-30B-A3B vLLM endpoint (OpenAI-compatible API).

Python 3.10+, standard library only. Used by qwen_endpoint_check.py and meant to
be reused unchanged in the Legal Radar prototype, so the code that was tested is
the code that runs.

What it handles:
- finding the API base (/v1) and the served model ID
- switching thinking on/off per request (chat_template_kwargs.enable_thinking)
- reading the thinking text from its own field (reasoning or reasoning_content)
- JSON schema answers (response_format json_schema)
- tool calls, including a fallback that reads <tool_call> text when the server
  did not convert it (the result says when the fallback was needed)
- streaming, token counting (/tokenize) and the server version (/version)
"""
from __future__ import annotations

import copy
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Sampling values recommended on the Qwen3 model card.
SAMPLING_THINKING = {'temperature': 0.6, 'top_p': 0.95, 'top_k': 20, 'min_p': 0.0}
SAMPLING_NO_THINKING = {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'min_p': 0.0}

RETRYABLE_STATUS = {429, 502, 503, 504}

_HERMES_CALL = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.S)
_XML_CALL = re.compile(r'<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*</tool_call>', re.S)
_XML_PARAM = re.compile(r'<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>', re.S)


def load_env(path):
    """Minimal .env reader. Values already set in the environment win."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError('Invalid .env line: ' + line[:40])
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        os.environ.setdefault(key, value)


def error_kind(status, text):
    text = (text or '').lower()
    if status in (401, 403):
        return 'authentication'
    if status == 429:
        return 'rate_limited'
    if status in (400, 413, 422) and re.search(r'context length|maximum context|too long|too large|max_tokens|max_model_len', text):
        return 'context_limit'
    if status and status >= 500:
        return 'server_error'
    if status:
        return 'http_' + str(status)
    if 'timed out' in text or 'timeout' in text:
        return 'timeout'
    return 'connection'


def error_message(body):
    """The readable message from an OpenAI/vLLM error body, or the body itself."""
    try:
        obj = json.loads(body)
    except ValueError:
        return body
    if isinstance(obj, dict):
        inner = obj.get('error')
        if isinstance(inner, dict) and isinstance(inner.get('message'), str):
            return inner['message']
        for key in ('message', 'detail', 'error'):
            if isinstance(obj.get(key), str):
                return obj[key]
    return body


def parse_text_tool_calls(text):
    """Read tool calls written as text, in the two formats Qwen models use.

    Hermes/Qwen3 format:   <tool_call>{"name": ..., "arguments": {...}}</tool_call>
    Qwen3-Coder XML format: <tool_call><function=name><parameter=x>v</parameter></function></tool_call>
    """
    calls = []
    for match in _HERMES_CALL.finditer(text or ''):
        try:
            obj = json.loads(match.group(1))
        except ValueError:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
            continue
        args = obj.get('arguments', obj.get('parameters', {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                pass
        calls.append({'name': obj['name'], 'arguments': args, 'format': 'hermes_json'})
    for match in _XML_CALL.finditer(text or ''):
        args = {}
        for param in _XML_PARAM.finditer(match.group(2)):
            value = param.group(2)
            try:
                value = json.loads(value)
            except ValueError:
                pass
            args[param.group(1)] = value
        calls.append({'name': match.group(1), 'arguments': args, 'format': 'qwen3_xml'})
    return calls


@dataclass
class ToolCall:
    name: str
    arguments: object            # dict when the arguments were valid JSON
    arguments_raw: str
    id: str
    source: str                  # 'server' (API tool_calls) or 'text_fallback'

    @property
    def args_ok(self):
        return isinstance(self.arguments, dict)

    def as_api(self):
        raw = self.arguments_raw if self.source == 'server' else json.dumps(self.arguments, ensure_ascii=False)
        return {'id': self.id, 'type': 'function', 'function': {'name': self.name, 'arguments': raw}}


@dataclass
class ChatResult:
    ok: bool = False                 # complete HTTP exchange with a finish_reason
    http_status: int | None = None
    error_kind: str | None = None
    error: str | None = None
    content: str = ''                # final answer only (thinking removed)
    reasoning: str = ''              # thinking text
    reasoning_field: str | None = None
    inline_think: bool = False       # <think> markup arrived inside content
    server_tool_calls: list = field(default_factory=list)
    text_tool_calls: list = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    ttft_seconds: float | None = None
    ttft_content_seconds: float | None = None
    stream_requested: bool = False
    streamed: bool = False
    message_keys: list = field(default_factory=list)
    attempts: int = 1

    @property
    def prompt_tokens(self):
        return self.usage.get('prompt_tokens')

    @property
    def completion_tokens(self):
        return self.usage.get('completion_tokens')

    @property
    def truncated(self):
        return self.finish_reason == 'length'

    @property
    def has_answer(self):
        return bool(self.content.strip())

    def tool_calls(self, allow_fallback=True):
        """Server tool calls; text fallback only if the server returned none."""
        if self.server_tool_calls:
            return self.server_tool_calls
        if allow_fallback:
            return [ToolCall(c['name'], c['arguments'], json.dumps(c['arguments'], ensure_ascii=False),
                             'call_' + uuid.uuid4().hex[:12], 'text_fallback') for c in self.text_tool_calls]
        return []

    def parse_json(self):
        """Strict JSON parse of the final answer. Returns (object, error_text)."""
        if not self.ok:
            return None, 'request failed: ' + str(self.error_kind)
        if self.truncated:
            return None, 'answer cut off (finish_reason=length)'
        text = self.content.strip()
        if not text:
            return None, 'no final answer'
        try:
            return json.loads(text), None
        except ValueError as exc:
            return None, 'not valid JSON: ' + str(exc)[:80]

    def summary(self, preview=300):
        """Compact record for reports. Thinking text is shortened."""
        return {
            'ok': self.ok, 'http_status': self.http_status, 'error_kind': self.error_kind,
            'error': (self.error or '')[:600] or None, 'finish_reason': self.finish_reason,
            'seconds': round(self.seconds, 3),
            'ttft_seconds': None if self.ttft_seconds is None else round(self.ttft_seconds, 3),
            'ttft_content_seconds': None if self.ttft_content_seconds is None else round(self.ttft_content_seconds, 3),
            'prompt_tokens': self.prompt_tokens, 'completion_tokens': self.completion_tokens,
            'stream_requested': self.stream_requested, 'streamed': self.streamed,
            'reasoning_field': self.reasoning_field, 'reasoning_chars': len(self.reasoning),
            'reasoning_preview': self.reasoning[:preview], 'inline_think': self.inline_think,
            'content': self.content[:4000],
            'server_tool_calls': [{'name': c.name, 'arguments': c.arguments} for c in self.server_tool_calls],
            'text_tool_calls': self.text_tool_calls, 'message_keys': self.message_keys,
            'attempts': self.attempts,
        }


def assistant_message(result, calls):
    """Assistant turn to send back after tool calls (thinking is not sent back)."""
    content = result.content
    if calls and calls[0].source == 'text_fallback':
        content = content.split('<tool_call>', 1)[0]
    return {'role': 'assistant', 'content': content.strip() or None, 'tool_calls': [c.as_api() for c in calls]}


class QwenClient:
    def __init__(self, base_url, model='auto', api_key=None, timeout=300, ca_file=None, retries=0):
        self.base_url = base_url.rstrip('/')
        self.api_base = None
        self.model = model or 'auto'
        self.api_key = api_key or None
        self.timeout = timeout
        self.ca_file = ca_file or None
        self.retries = retries
        self.max_model_len = None

    @classmethod
    def from_env(cls, env_file=None, **overrides):
        if env_file:
            load_env(env_file)
        base = os.getenv('QWEN_BASE_URL', '').strip()
        if not base:
            raise ValueError('QWEN_BASE_URL is not set (see .env.example)')
        settings = {'base_url': base, 'model': os.getenv('QWEN_MODEL', '').strip() or 'auto',
                    'api_key': os.getenv('QWEN_API_KEY', '').strip() or None,
                    'ca_file': os.getenv('SSL_CERT_FILE', '').strip() or None,
                    'timeout': float(os.getenv('QWEN_TIMEOUT_SECONDS', '0') or 0) or 300}
        settings.update(overrides)
        return cls(**settings)

    # ------------------------------------------------------------------ HTTP
    def _headers(self):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key
        return headers

    def _open(self, url, payload=None, timeout=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(url, data=data, headers=self._headers(),
                                         method='GET' if payload is None else 'POST')
        context = ssl.create_default_context(cafile=self.ca_file)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None   # never forward the key to another address
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
        return opener.open(request, timeout=timeout or self.timeout)

    def _get_json(self, url, timeout=15):
        try:
            with self._open(url, timeout=timeout) as response:
                return response.status, json.load(response), None
        except urllib.error.HTTPError as exc:
            return exc.code, None, exc.read(2000).decode('utf-8', 'replace')
        except Exception as exc:  # noqa: BLE001 - report every network problem as text
            return None, None, str(exc)

    def clean(self, text):
        if self.api_key and text:
            text = text.replace(self.api_key, '[REDACTED]')
        return text

    # ------------------------------------------------------------- discovery
    def root_url(self):
        base = self.api_base or self.base_url
        base = base[:-3] if base.endswith('/v1') else base
        return base[:-7] if base.endswith('/openai') else base

    def discover(self):
        """Find the API base and model ID with GET /v1/models (or /models)."""
        if self.base_url.endswith('/v1'):
            candidates = [self.base_url, self.base_url[:-3]]
        else:   # vLLM serves /v1; the KServe Hugging Face runtime serves /openai/v1
            candidates = [self.base_url + '/v1', self.base_url, self.base_url + '/openai/v1']
        probes = []
        for base in candidates:
            status, body, err = self._get_json(base + '/models')
            probes.append({'path': base.replace(self.root_url_of(base), '') + '/models', 'http_status': status,
                           'error': self.clean(err)[:300] if err else None})
            if status == 200 and isinstance(body, dict) and isinstance(body.get('data'), list):
                models = [m for m in body['data'] if isinstance(m, dict) and isinstance(m.get('id'), str)]
                ids = [m['id'] for m in models]
                if self.model == 'auto':
                    if len(ids) != 1:
                        return {'ok': False, 'probes': probes, 'model_ids': ids,
                                'error': 'The server lists %d models; set QWEN_MODEL in .env to the exact ID.' % len(ids)}
                    self.model = ids[0]
                chosen = next((m for m in models if m['id'] == self.model), None)
                if chosen is None:
                    return {'ok': False, 'probes': probes, 'model_ids': ids,
                            'error': 'QWEN_MODEL is not in the server model list.'}
                self.api_base = base
                self.max_model_len = chosen.get('max_model_len') or chosen.get('context_length')
                return {'ok': True, 'api_base_suffix': '/v1' if base.endswith('/v1') else '(none)',
                        'model': self.model, 'model_ids': ids, 'max_model_len': self.max_model_len,
                        'owned_by': chosen.get('owned_by'), 'root': chosen.get('root'), 'probes': probes}
            if status in (401, 403):
                return {'ok': False, 'probes': probes, 'error': 'Access denied (HTTP %s). Check QWEN_API_KEY.' % status}
        return {'ok': False, 'probes': probes,
                'error': 'No model list found. Check QWEN_BASE_URL, company network/VPN and certificates.'}

    def root_url_of(self, base):
        parts = urllib.parse.urlsplit(base)
        return parts.scheme + '://' + parts.netloc

    def version(self):
        status, body, _ = self._get_json(self.root_url() + '/version')
        return body.get('version') if status == 200 and isinstance(body, dict) else None

    def tokenize(self, text=None, messages=None):
        """Token count from vLLM's /tokenize, or None if it is not reachable."""
        payload = {'model': self.model}
        if messages is not None:
            payload.update({'messages': messages, 'add_generation_prompt': True})
        else:
            payload['prompt'] = text
        try:
            with self._open(self.root_url() + '/tokenize', payload, timeout=30) as response:
                body = json.load(response)
            count = body.get('count')
            if count is None and isinstance(body.get('tokens'), list):
                count = len(body['tokens'])
            return count if isinstance(count, int) else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ chat
    def build_payload(self, messages, *, thinking=None, max_tokens=1024, schema=None, schema_name='answer',
                      json_object=False, tools=None, tool_choice=None, stream=False, sampling=None, seed=None, extra=None):
        payload = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens, 'stream': stream}
        if sampling is None:
            sampling = SAMPLING_THINKING if thinking in (True, None) else SAMPLING_NO_THINKING
        payload.update(sampling)
        if thinking is not None:
            payload['chat_template_kwargs'] = {'enable_thinking': bool(thinking)}
        if schema is not None:
            payload['response_format'] = {'type': 'json_schema',
                                          'json_schema': {'name': schema_name, 'schema': schema, 'strict': True}}
        elif json_object:
            payload['response_format'] = {'type': 'json_object'}
        if tools:
            payload['tools'] = tools
            if tool_choice is not None:
                payload['tool_choice'] = tool_choice
        if seed is not None:
            payload['seed'] = seed
        if stream:
            payload['stream_options'] = {'include_usage': True}
        if extra:
            payload.update(extra)
        return payload

    def chat(self, messages, **options):
        if not self.api_base:
            raise RuntimeError('Call discover() first')
        payload = self.build_payload(messages, **options)
        attempt = 0
        while True:
            attempt += 1
            result = self._send(payload)
            result.attempts = attempt
            retry = (not result.ok and attempt <= self.retries and
                     (result.http_status in RETRYABLE_STATUS or result.error_kind in ('timeout', 'connection')))
            if not retry:
                return result
            time.sleep(2 ** attempt)

    def _send(self, payload):
        start = time.perf_counter()
        result = ChatResult(stream_requested=bool(payload.get('stream')))
        tool_parts = {}

        def take(obj, streamed):
            if 'error' in obj:
                raise ValueError(json.dumps(obj['error'], ensure_ascii=False)[:1000])
            if obj.get('usage'):
                result.usage = obj['usage']
            for choice in obj.get('choices', []):
                if choice.get('index', 0) != 0:
                    continue
                part = choice.get('delta' if streamed else 'message') or {}
                if not streamed:
                    result.message_keys = sorted(k for k, v in part.items() if v not in (None, '', []))
                content = part.get('content') or ''
                reasoning = ''
                for name in ('reasoning', 'reasoning_content'):
                    if part.get(name):
                        reasoning = part[name]
                        result.reasoning_field = result.reasoning_field or name
                        break
                calls = part.get('tool_calls') or []
                now = time.perf_counter() - start
                if streamed and (content or reasoning or calls) and result.ttft_seconds is None:
                    result.ttft_seconds = now
                if streamed and content and result.ttft_content_seconds is None:
                    result.ttft_content_seconds = now
                result.content += content
                result.reasoning += reasoning
                for i, call in enumerate(calls):
                    slot = tool_parts.setdefault(call.get('index', i), {'id': '', 'name': '', 'arguments': ''})
                    slot['id'] = call.get('id') or slot['id']
                    fn = call.get('function') or {}
                    slot['name'] += fn.get('name') or ''
                    args = fn.get('arguments')
                    slot['arguments'] += args if isinstance(args, str) else (json.dumps(args) if args else '')
                if choice.get('finish_reason'):
                    result.finish_reason = choice['finish_reason']

        try:
            with self._open(self.api_base + '/chat/completions', payload) as response:
                result.http_status = response.status
                result.streamed = 'text/event-stream' in response.headers.get('Content-Type', '')
                if result.streamed:
                    done = False
                    for raw in response:
                        line = raw.decode('utf-8').strip()
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            done = True
                            break
                        take(json.loads(data), True)
                    if not (done or result.finish_reason):
                        raise ValueError('Stream ended early')
                else:
                    take(json.load(response), False)
                if not result.finish_reason:
                    raise ValueError('Response has no finish_reason')
                result.ok = True
        except urllib.error.HTTPError as exc:
            result.http_status = exc.code
            body = exc.read(4000).decode('utf-8', 'replace')
            result.error_kind = error_kind(exc.code, body)
            result.error = self.clean(error_message(body))
        except Exception as exc:  # noqa: BLE001
            result.error_kind = error_kind(None, str(exc))
            result.error = self.clean(str(exc))
        result.seconds = time.perf_counter() - start

        for index in sorted(tool_parts):
            part = tool_parts[index]
            try:
                args = json.loads(part['arguments']) if part['arguments'].strip() else {}
            except ValueError:
                args = None
            result.server_tool_calls.append(ToolCall(part['name'], args, part['arguments'],
                                                     part['id'] or 'call_' + uuid.uuid4().hex[:12], 'server'))
        _split_inline_thinking(result)
        result.text_tool_calls = parse_text_tool_calls(result.content)
        return result


def _split_inline_thinking(result):
    """If <think> markup arrived inside content, move it to reasoning and flag it."""
    text = result.content
    if '<think>' not in text and '</think>' not in text:
        return
    result.inline_think = True
    if '</think>' in text:
        before, _, after = text.rpartition('</think>')
        result.reasoning += before.replace('<think>', '')
        result.content = after.lstrip()
    else:
        before, _, after = text.partition('<think>')
        result.reasoning += after
        result.content = before
    result.ttft_content_seconds = None
