#!/usr/bin/env python3
"""Ask My Agent — Minimal Apple-Style Substack to LinkedIn & X Converter.

Takes any Substack article URL (e.g. https://zahed.substack.com/p/...),
fetches the live article JSON via Substack's API, parses all formatted text,
inline images, and <figcaption> captions, automatically converts embedded
Substack videos into crisp animated GIFs via 2-pass FFmpeg, and formats the
article for 1-click copy-paste into LinkedIn Newsletters and X Articles.
"""

import collections
from html import parser as html_parser
import html
from http import cookies
from http import server
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

HOST = '127.0.0.1'
PORT = int(os.environ.get('PORT', '8765'))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, 'public')
VAULT_DIR = os.path.join(BASE_DIR, 'media_vault')
os.makedirs(VAULT_DIR, exist_ok=True)


def get_session_secret() -> str:
  """Multi-tiered secret resolution persisted to disk across restarts."""
  env_secret = os.getenv('APP_CSRF_SECRET')
  if env_secret:
    return env_secret
  secret_file = os.path.join(BASE_DIR, '.csrf_secret')
  if os.path.exists(secret_file):
    with open(secret_file, 'r', encoding='utf-8') as f:
      val = f.read().strip()
      if val:
        return val
  generated = secrets.token_hex(32)
  try:
    with open(secret_file, 'w', encoding='utf-8') as f:
      f.write(generated)
  except OSError:
    pass
  return generated


CSRF_TOKEN = get_session_secret()

RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 120
REQUEST_LOG = collections.defaultdict(collections.deque)

SUBSTACK_URL_RE = re.compile(
    r'^https://([a-zA-Z0-9-]+)\.substack\.com/p/([a-zA-Z0-9-]+)'
)
SAFE_GIF_NAME_RE = re.compile(
    r'^(sample_agent_demo\.gif|substack_[a-f0-9-]+(?:_micro|_mini)?\.gif|gif_[a-f0-9]{16}\.gif)$'
)
SAFE_MP4_NAME_RE = re.compile(r'^substack_[a-f0-9-]+\.mp4$')
UUID_RE = re.compile(r'^[a-f0-9-]{20,40}$')


def to_master_png_url(url: str) -> str:
  """Returns the unscaled, 100% lossless master PNG URL on Substack CDN."""
  s = str(url or '').strip()
  idx = s.find('https%3A%2F%2F')
  if idx != -1:
    return 'https://substackcdn.com/image/fetch/f_png,q_100/' + s[idx:]
  if s.startswith('https://substack-post-media.s3.amazonaws.com/'):
    return (
        'https://substackcdn.com/image/fetch/f_png,q_100/'
        + urllib.parse.quote(s, safe='')
    )
  return s

ARTICLE_CACHE = {}
CACHE_FILE_PATH = os.path.join(BASE_DIR, 'full_articles_cache.json')
if os.path.isfile(CACHE_FILE_PATH):
  try:
    with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as _cf:
      ARTICLE_CACHE.update(json.load(_cf))
    logging.info('Preloaded %d Substack articles from cache.', len(ARTICLE_CACHE))
  except (OSError, ValueError) as _err:
    logging.warning('Could not preload article cache: %s', _err)

RECENT_SUBSTACK_POSTS = [
    {
        'title': 'Why Smart AI Agents Still Get Your Work Wrong',
        'url': 'https://zahed.substack.com/p/why-smart-ai-agents-still-get-your',
    },
    {
        'title': 'Your AI Has a Brain. Agents Give It a Body.',
        'url': 'https://zahed.substack.com/p/your-ai-has-a-brain-agents-give-it',
    },
    {
        'title': 'Why Claude in Your Browser Can’t Open Your Files',
        'url': 'https://zahed.substack.com/p/why-claude-in-your-browser-cant-open',
    },
    {
        'title': 'Everyone’s Asking the Wrong Wrapper Question',
        'url': 'https://zahed.substack.com/p/what-to-ask-startups',
    },
    {
        'title': 'AI Has No Gut Feeling',
        'url': 'https://zahed.substack.com/p/ai-has-no-gut-feeling',
    },
    {
        'title': 'The Assembly Line of AI Logic',
        'url': 'https://zahed.substack.com/p/the-assembly-line-of-ai-logic',
    },
    {
        'title': 'Stop Treating LLMs Like People. They’re Statistical Engines.',
        'url': 'https://zahed.substack.com/p/stop-treating-llms-like-people-theyre',
    },
]


class SubstackBodyParser(html_parser.HTMLParser):
  """Parses Substack body_html into structured blocks with captions & videos."""

  def __init__(self) -> None:
    super().__init__()
    self.blocks = []
    self.in_ignore_div = 0
    self.in_figure = False
    self.in_figcaption = False
    self.current_fig_src = ''
    self.current_fig_alt = ''
    self.current_fig_caption_runs = []
    self.current_block_type = None
    self.current_runs = []
    self.current_list_type = None
    self.current_list_items = []
    self.bold_depth = 0
    self.italic_depth = 0
    self.code_depth = 0
    self.current_href = ''

  def _flush_text_block(self) -> None:
    if self.current_block_type and self.current_runs:
      full_text = ''.join(r.get('t', '') for r in self.current_runs).strip()
      if full_text and full_text.lower() != 'subscribe now':
        self.blocks.append({
            'type': self.current_block_type,
            'runs': list(self.current_runs),
            'text': full_text,
        })
    self.current_block_type = None
    self.current_runs = []

  def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
    attr_dict = {k: (v or '') for k, v in attrs}
    cls = attr_dict.get('class', '')
    comp = attr_dict.get('data-component-name', '')

    if self.in_ignore_div > 0:
      if tag == 'div':
        self.in_ignore_div += 1
      return

    if tag == 'div' and (
        'captioned-button-wrap' in cls
        or 'subscription-widget-wrap' in cls
        or comp in ('CaptionedButtonToDOM', 'SubscribeWidgetToDOM')
    ):
      self.in_ignore_div = 1
      return

    if tag == 'div' and (
        'native-video-embed' in cls
        or comp in ('VideoPlaceholder', 'VideoEmbedPlayer')
    ):
      self._flush_text_block()
      vid_id = attr_dict.get('data-media-upload-id', '')
      caption_str = ''
      data_attrs = attr_dict.get('data-attrs', '')
      if data_attrs:
        unescaped = html.unescape(data_attrs)
        if not vid_id:
          m = re.search(r'"mediaUploadId"\s*:\s*"([a-f0-9-]+)"', unescaped)
          if m:
            vid_id = m.group(1)
        cap_m = re.search(r'"caption"\s*:\s*"([^"]+)"', unescaped)
        if cap_m:
          caption_str = cap_m.group(1)
      if vid_id and UUID_RE.match(vid_id):
        self.blocks.append({
            'type': 'video_gif',
            'mediaUploadId': vid_id,
            'caption': caption_str,
        })
      return

    if tag == 'figure':
      self._flush_text_block()
      self.in_figure = True
      self.current_fig_src = ''
      self.current_fig_alt = ''
      self.current_fig_caption_runs = []
      return

    if self.in_figure:
      if tag == 'a' and not self.current_fig_src:
        href = attr_dict.get('href', '')
        if href.startswith('https://') and (
            'substackcdn.com' in href or 'substack-post-media' in href
        ):
          self.current_fig_src = href
      elif tag == 'img':
        src = attr_dict.get('src', '')
        if src.startswith('https://') and not self.current_fig_src:
          self.current_fig_src = src
        self.current_fig_alt = attr_dict.get('alt', '')
      elif tag == 'figcaption':
        self.in_figcaption = True
      elif self.in_figcaption:
        if tag in ('strong', 'b'):
          self.bold_depth += 1
        elif tag in ('em', 'i'):
          self.italic_depth += 1
        elif tag == 'a':
          self.current_href = attr_dict.get('href', '')
      return

    if tag in ('h1', 'h2', 'h3', 'h4'):
      self._flush_text_block()
      if tag == 'h1':
        self.current_block_type = 'h1'
      elif tag == 'h2':
        self.current_block_type = 'h2'
      else:
        self.current_block_type = 'h3'
      self.current_runs = []
    elif tag == 'blockquote':
      self._flush_text_block()
      self.current_block_type = 'quote'
      self.current_runs = []
    elif tag in ('ul', 'ol'):
      self._flush_text_block()
      self.current_list_type = tag
      self.current_list_items = []
    elif tag == 'li':
      self.current_runs = []
    elif tag == 'p':
      if self.current_block_type != 'quote' and not self.current_list_type:
        self._flush_text_block()
        self.current_block_type = 'p'
        self.current_runs = []
    elif tag in ('strong', 'b'):
      self.bold_depth += 1
    elif tag in ('em', 'i'):
      self.italic_depth += 1
    elif tag == 'code':
      self.code_depth += 1
    elif tag == 'a':
      self.current_href = attr_dict.get('href', '')

  def handle_endtag(self, tag: str) -> None:
    if self.in_ignore_div > 0:
      if tag == 'div':
        self.in_ignore_div -= 1
      return

    if self.in_figure:
      if tag == 'figcaption':
        self.in_figcaption = False
        self.bold_depth = 0
        self.italic_depth = 0
        self.current_href = ''
      elif tag == 'figure':
        self.in_figure = False
        caption_text = ''.join(
            r.get('t', '') for r in self.current_fig_caption_runs
        ).strip()
        if self.current_fig_src:
          self.blocks.append({
              'type': 'image',
              'src': to_master_png_url(self.current_fig_src),
              'alt': self.current_fig_alt,
              'caption': caption_text,
              'captionRuns': list(self.current_fig_caption_runs),
          })
      elif self.in_figcaption:
        if tag in ('strong', 'b'):
          self.bold_depth = max(0, self.bold_depth - 1)
        elif tag in ('em', 'i'):
          self.italic_depth = max(0, self.italic_depth - 1)
        elif tag == 'a':
          self.current_href = ''
      return

    if tag in ('strong', 'b'):
      self.bold_depth = max(0, self.bold_depth - 1)
    elif tag in ('em', 'i'):
      self.italic_depth = max(0, self.italic_depth - 1)
    elif tag == 'code':
      self.code_depth = max(0, self.code_depth - 1)
    elif tag == 'a':
      self.current_href = ''
    elif tag == 'li' and self.current_list_type:
      txt = ''.join(r.get('t', '') for r in self.current_runs).strip()
      if txt:
        self.current_list_items.append({
            'runs': list(self.current_runs),
            'text': txt,
        })
      self.current_runs = []
    elif tag in ('ul', 'ol') and self.current_list_type:
      if self.current_list_items:
        self.blocks.append({
            'type': self.current_list_type,
            'items': list(self.current_list_items),
        })
      self.current_list_type = None
      self.current_list_items = []
    elif tag in ('h1', 'h2', 'h3', 'h4', 'blockquote'):
      self._flush_text_block()
    elif tag == 'p' and self.current_block_type == 'p':
      self._flush_text_block()

  def handle_data(self, data: str) -> None:
    if self.in_ignore_div > 0 or not data:
      return
    run = {'t': data}
    if self.bold_depth > 0:
      run['bold'] = True
    if self.italic_depth > 0:
      run['italic'] = True
    if self.code_depth > 0:
      run['code'] = True
    if self.current_href and self.current_href.startswith('https://'):
      run['href'] = self.current_href

    if self.in_figure and self.in_figcaption:
      self.current_fig_caption_runs.append(run)
    elif self.current_block_type or self.current_list_type:
      self.current_runs.append(run)


def _is_safe_public_host(host: str) -> bool:
  """Ensures host is a valid public domain name and not a private/internal IP or host."""
  h = (host or '').strip().lower()
  if not h or not re.match(r'^[a-z0-9._-]+$', h) or '.' not in h:
    return False
  blocked_prefixes = ('localhost', '127.', '10.', '192.168.', '169.254.', '0.')
  blocked_suffixes = ('.internal', '.local')
  if h.startswith(blocked_prefixes) or h.endswith(blocked_suffixes):
    return False
  if re.match(r'^172\.(1[6-9]|2\d|3[0-1])\.', h):
    return False
  return True


VIDEO_LOCKS_GUARD = threading.Lock()
VIDEO_LOCKS: dict[str, threading.Lock] = {}


def _get_video_lock(media_upload_id: str) -> threading.Lock:
  with VIDEO_LOCKS_GUARD:
    if media_upload_id not in VIDEO_LOCKS:
      VIDEO_LOCKS[media_upload_id] = threading.Lock()
    return VIDEO_LOCKS[media_upload_id]


LAST_LLM_STATUS = 'uninitialized'


def _call_gemini_json(system_prompt: str, user_prompt: str, timeout_sec: int = 22) -> dict[str, object] | None:
  """Calls the public Gemini API (or OpenAI/Groq fallback) with strict JSON output."""
  global LAST_LLM_STATUS
  raw_env = (
      os.environ.get('GEMINI_API_KEY')
      or os.environ.get('GOOGLE_API_KEY')
      or os.environ.get('API_KEY')
      or ''
  )
  api_key = raw_env.strip().strip('"').strip("'").strip()

  # Support Groq (gsk_...) or OpenAI (sk-...) keys seamlessly if pasted into GEMINI_API_KEY or GROQ_API_KEY
  groq_key = (
      os.environ.get('GROQ_API_KEY') or (api_key if api_key.startswith('gsk_') else '')
  ).strip().strip('"').strip("'")
  openai_key = (
      os.environ.get('OPENAI_API_KEY') or (api_key if api_key.startswith('sk-') else '')
  ).strip().strip('"').strip("'")

  if groq_key or openai_key:
    endpoint = (
        'https://api.groq.com/openai/v1/chat/completions'
        if groq_key
        else 'https://api.openai.com/v1/chat/completions'
    )
    model = 'llama-3.3-70b-versatile' if groq_key else 'gpt-4o-mini'
    token = groq_key or openai_key
    oai_payload = json.dumps({
        'model': model,
        'temperature': 0.35,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }).encode('utf-8')
    try:
      req = urllib.request.Request(
          endpoint,
          data=oai_payload,
          headers={
              'Content-Type': 'application/json',
              'Authorization': f'Bearer {token}',
              'User-Agent': 'StackConverter/1.0',
          },
      )
      with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        resp_json = json.loads(resp.read().decode('utf-8'))
      raw_text = resp_json['choices'][0]['message']['content'].strip()
      raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text, flags=re.DOTALL).strip()
      LAST_LLM_STATUS = f'ok:{model}'
      return json.loads(raw_text)
    except (urllib.error.URLError, KeyError, ValueError, OSError) as err:
      LAST_LLM_STATUS = f'err:{model}:{type(err).__name__}'
      logging.warning('OpenAI/Groq API call failed: %s', err)

  if not api_key or api_key.startswith(('gsk_', 'sk-')):
    LAST_LLM_STATUS = 'missing_api_key'
    return None

  model_configs = [
      ('gemini-2.5-flash', {'temperature': 0.35, 'responseMimeType': 'application/json', 'thinkingConfig': {'thinkingBudget': 0}}),
      ('gemini-2.5-flash-lite', {'temperature': 0.35, 'responseMimeType': 'application/json', 'thinkingConfig': {'thinkingBudget': 0}}),
      ('gemini-2.0-flash', {'temperature': 0.35, 'responseMimeType': 'application/json'}),
  ]
  for ext_model, gen_cfg in model_configs:
    ext_payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': gen_cfg,
    }).encode('utf-8')
    try:
      req = urllib.request.Request(
          f'https://generativelanguage.googleapis.com/v1beta/models/{ext_model}:generateContent',
          data=ext_payload,
          headers={
              'Content-Type': 'application/json',
              'x-goog-api-key': api_key,
          },
      )
      with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        resp_json = json.loads(resp.read().decode('utf-8'))
      parts = resp_json['candidates'][0]['content']['parts']
      raw_text = next(
          (p['text'] for p in reversed(parts) if 'text' in p and not p.get('thought')),
          parts[-1].get('text', ''),
      ).strip()
      raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text, flags=re.DOTALL).strip()
      LAST_LLM_STATUS = f'ok:{ext_model}'
      return json.loads(raw_text)
    except urllib.error.HTTPError as http_err:
      LAST_LLM_STATUS = f'http_{http_err.code}:{ext_model}'
      logging.warning('GEMINI_API_KEY call (%s) HTTP %d: %s', ext_model, http_err.code, http_err)
    except (urllib.error.URLError, KeyError, ValueError, OSError) as err:
      LAST_LLM_STATUS = f'err:{ext_model}:{type(err).__name__}'
      logging.warning('GEMINI_API_KEY call (%s) failed: %s', ext_model, err)
  return None


def ensure_substack_video_mp4(host_or_subdomain: str, media_upload_id: str) -> str:
  """Download and cache the original high-quality Substack MP4 video stream."""
  target_host = (
      host_or_subdomain
      if '.' in host_or_subdomain
      else f'{host_or_subdomain}.substack.com'
  )
  if not _is_safe_public_host(target_host) or not UUID_RE.match(media_upload_id):
    return '/api/mp4/sample_agent_demo.mp4'

  mp4_filename = f'substack_{media_upload_id}.mp4'
  vault_root = os.path.realpath(VAULT_DIR) + os.sep
  mp4_path = os.path.realpath(os.path.join(VAULT_DIR, mp4_filename))
  tmp_mp4_path = os.path.realpath(os.path.join(VAULT_DIR, f'substack_{media_upload_id}.tmp.mp4'))

  if not mp4_path.startswith(vault_root) or not tmp_mp4_path.startswith(vault_root):
    return '/api/mp4/sample_agent_demo.mp4'

  with _get_video_lock(media_upload_id):
    if os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 1000:
      return f'/api/mp4/{mp4_filename}'

    video_api_url = (
        f'https://{target_host}/api/v1/video/upload/'
        f'{media_upload_id}/src?type=mp4'
    )
    try:
      req = urllib.request.Request(
          video_api_url,
          headers={
              'User-Agent': (
                  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/131.0.0.0 Safari/537.36'
              )
          },
      )
      with urllib.request.urlopen(req, timeout=30) as resp:
        raw_mp4 = resp.read(100 * 1024 * 1024)
      with open(tmp_mp4_path, 'wb') as f:
        f.write(raw_mp4)
      if os.path.isfile(tmp_mp4_path) and os.path.getsize(tmp_mp4_path) > 1000:
        os.replace(tmp_mp4_path, mp4_path)
        return f'/api/mp4/{mp4_filename}'
      return '/api/mp4/sample_agent_demo.mp4'
    except (urllib.error.URLError, OSError) as e:
      logging.warning('Could not download Substack MP4 video %s: %s', media_upload_id, e)
      return '/api/mp4/sample_agent_demo.mp4'
    finally:
      if os.path.isfile(tmp_mp4_path):
        try:
          os.remove(tmp_mp4_path)
        except OSError:
          pass


HOOK_SYSTEM_PROMPT = """You are an elite technical editor and social distribution strategist for a senior AI & data science newsletter.

Your job is to read the full text of a Substack article and write 4 high-converting LinkedIn feed hooks and 4 high-converting X (Twitter) feed hooks designed to stop the scroll of senior engineers, data scientists, PMs, and technical founders, and drive them to read the full piece.

STRICT GROUNDING RULES (ZERO HALLUCINATION):
1. Every claim, analogy, example, code snippet, metric, and quote MUST come strictly from the provided article text.
2. Never invent external statistics, company names, benchmarks, or takeaways that are not explicitly in the article.
3. Never include URLs, links, hashtags, or emojis.
4. Write in the author's exact voice: clear, concrete, first-principles, and grounded in how the technology actually works.

BANNED VOCABULARY & SYNTAX (CRITICAL):
- Banned words/phrases: "In today's fast-paced world", "Dive in", "Deep dive", "Game-changer", "Unleash", "Unlock", "Buckle up", "Here's why it matters", "Let's unpack", "The secret?", "The truth is".
- Banned syntactic patterns:
  * Do NOT use "It's not X. It's Y." or "Stop doing X. Start doing Y." contrast clichés.
  * Do NOT use one-word dramatic questions ("The result?", "The catch?", "Why?").
  * Do NOT open with "Most people think..." unless quoting the article verbatim.

FEED GEOMETRY & FORMATTING RULES:
- Use `\\n\\n` inside JSON text strings to represent clean line breaks.
- LinkedIn Fold Rule: Line 1 must be a standalone sentence under 15 words. Follow it with `\\n\\n`. Lines 1–2 combined must be under 200 characters so the tension lands BEFORE LinkedIn's "...see more" cutoff on mobile. Total length: 45–90 words.
- X / Twitter Fold Rule: The first 1–2 lines must create an immediate technical curiosity gap under 180 characters. Total length must be strictly under 280 characters (including spaces and line breaks) so it fits in a single post without truncation.

PLATFORM-SPECIFIC HOOK ARCHETYPES:

For LinkedIn (4 Hooks — authoritative, sharp, structured for the mobile "see more" fold):
- Hook 1 (The Counter-Intuitive Thesis): Line 1 exposes a specific technical misconception from the article. Line 2 contrasts it with the core mechanism/model from the piece. End with a 1-line bridge naming the exact breakdown inside the article.
- Hook 2 (The Concrete Analogy / Mental Model): Open directly with the strongest analogy or real-world system comparison from the article (avoid meta-intros like "Here is a great analogy"). Explain the underlying mechanism in 2–3 tight sentences. Close with a specific pointer to the full piece.
- Hook 3 (The Practitioner / Builder Question): Open with a concrete engineering, architecture, or product decision question grounded in the article. Follow with the diagnostic test, trade-off, or framework the article provides to answer it.
- Hook 4 (Verbatim Quote + Synthesis): Lead with the single sharpest verbatim 1-sentence quote from the article in quotation marks on Line 1. Follow with 2–3 punchy lines unpacking the engineering or product implication of that exact claim.

For X / Twitter (4 Hooks — high-signal, dense, zero throat-clearing, <280 chars each):
- Hook 1 (The Hard Truth Opener): 1 ultra-tight sentence stating the most surprising technical reality from the article, `\\n\\n`, followed by a 1–2 sentence explanation of the mechanism.
- Hook 2 (The Mechanism Shift): Contrast the old mental model vs. the actual technical reality described in the article using 3–4 short, high-contrast lines (use "→" for flow if helpful, no emojis).
- Hook 3 (The Diagnostic Litmus Test): State the exact question, heuristic, or mental model from the article that separates signal from hype, plus a 1-line takeaway.
- Hook 4 (The Verbatim Mic-Drop): Pair the strongest 1-sentence verbatim quote from the article in quotes with the core technical takeaway. Must be under 240 characters.

OUTPUT FORMAT:
Return ONLY valid JSON matching this exact structure (no markdown code fences or commentary outside the JSON):
{
  "linkedin": [
    {"label": "Counter-Intuitive Thesis", "text": "Line 1...\\n\\nLine 2..."},
    {"label": "Concrete Analogy", "text": "..."},
    {"label": "Practitioner Question", "text": "..."},
    {"label": "Verbatim Quote + Synthesis", "text": "..."}
  ],
  "x": [
    {"label": "Hard Truth Opener", "text": "...", "char_count": 0},
    {"label": "Mechanism Shift", "text": "...", "char_count": 0},
    {"label": "Diagnostic Litmus Test", "text": "...", "char_count": 0},
    {"label": "Verbatim Mic-Drop", "text": "...", "char_count": 0}
  ]
}"""


def _split_sentences(text: str) -> list[str]:
  raw = re.split(r'(?<=[.!?])\s+', text.strip())
  return [s.strip() for s in raw if len(s.strip()) >= 20]


def _extract_full_article_text(blocks: list[dict[str, object]]) -> str:
  full_text_lines = []
  for b in blocks:
    btype = b.get('type')
    if btype in ('h1', 'h2', 'h3', 'p', 'quote'):
      full_text_lines.append(str(b.get('text', '')).strip())
    elif btype in ('ul', 'ol'):
      for it in b.get('items', []):
        txt = it if isinstance(it, str) else str(it.get('text', ''))
        full_text_lines.append(f'- {txt}')
  return '\n\n'.join(line for line in full_text_lines if line)


LINKEDIN_ARCHETYPES = [
    (
        'Counter-Intuitive Thesis',
        'Line 1 exposes a specific technical misconception from the article (<15 words). '
        'Line 2 contrasts it with the core mechanism/model from the piece. '
        'End with a 1-line bridge naming the exact breakdown inside the article.',
    ),
    (
        'Concrete Analogy',
        'Open directly with the strongest analogy or real-world system comparison from the article '
        '(avoid meta-intros like "Here is a great analogy"). Explain the underlying mechanism in 2-3 tight sentences. '
        'Close with a specific pointer to the full piece.',
    ),
    (
        'Practitioner Question',
        'Open with a concrete engineering, architecture, or product decision question grounded in the article (<15 words). '
        'Follow with the diagnostic test, trade-off, or framework the article provides to answer it.',
    ),
    (
        'Verbatim Quote + Synthesis',
        'Lead with the single sharpest verbatim 1-sentence quote from the article in quotation marks on Line 1. '
        'Follow with 2-3 punchy lines unpacking the engineering or product implication of that exact claim.',
    ),
]

X_ARCHETYPES = [
    (
        'Hard Truth Opener',
        '1 ultra-tight sentence stating the most surprising technical reality from the article, \\n\\n, '
        'followed by a 1-2 sentence explanation of the mechanism. Strictly under 280 characters.',
    ),
    (
        'Mechanism Shift',
        'Contrast the old mental model vs. the actual technical reality described in the article using 3-4 short, '
        'high-contrast lines (use "→" for flow if helpful, no emojis). Strictly under 280 characters.',
    ),
    (
        'Diagnostic Litmus Test',
        'State the exact question, heuristic, or mental model from the article that separates signal from hype, '
        'plus a 1-line takeaway. Strictly under 280 characters.',
    ),
    (
        'Verbatim Mic-Drop',
        'Pair the strongest 1-sentence verbatim quote from the article in quotes with the core technical takeaway. '
        'Must be under 240 characters.',
    ),
]


def _build_multi_archetype_fallback_hooks(
    title: str, subtitle: str, blocks: list[dict[str, object]], offset: int = 0
) -> dict[str, list[dict[str, object]]]:
  """Synthesizes 4 distinct LinkedIn hooks and 4 distinct X hooks grounded in article text."""
  paragraphs = [
      str(b.get('text', '')).strip()
      for b in blocks
      if b.get('type') in ('p', 'quote')
      and len(str(b.get('text', '')).strip()) > 40
  ]
  headers = [
      str(b.get('text', '')).strip()
      for b in blocks
      if b.get('type') in ('h1', 'h2', 'h3')
      and len(str(b.get('text', '')).strip()) > 8
  ]
  quotes = [
      str(b.get('text', '')).strip()
      for b in blocks
      if b.get('type') == 'quote'
      and len(str(b.get('text', '')).strip()) > 25
  ]
  sentences = []
  for p in paragraphs:
    for s in _split_sentences(p):
      if 30 <= len(s) <= 210 and s not in sentences:
        sentences.append(s)

  if not paragraphs:
    paragraphs = [subtitle or title]
  if not sentences:
    sentences = [subtitle or title]

  def _pick(seq: list[str], idx: int) -> str:
    return seq[(idx + offset) % len(seq)]

  s0 = _pick(sentences, 0)
  s1 = _pick(sentences, 1)
  s2 = _pick(sentences, 2)
  s3 = _pick(sentences, 3)
  s4 = _pick(sentences, 4)
  s5 = _pick(sentences, 5)
  p0 = _pick(paragraphs, 0)
  p1 = _pick(paragraphs, 1)
  p2 = _pick(paragraphs, 2)
  p3 = _pick(paragraphs, 3)
  h0 = _pick(headers, 0) if headers else (subtitle or title)
  q0 = _pick(quotes, 0) if quotes else s2

  li_hooks = [
      {
          'label': 'Counter-Intuitive Thesis',
          'text': f'{s0}\n\n{p1}\n\n{s3}',
      },
      {
          'label': 'Concrete Analogy',
          'text': f'{s1}\n\n{p2}\n\n{s4}',
      },
      {
          'label': 'Practitioner Question',
          'text': f'{h0}\n\n{p0}\n\n{s5}',
      },
      {
          'label': 'Verbatim Quote + Synthesis',
          'text': f'"{q0}"\n\n{p3}',
      },
  ]
  for h in li_hooks:
    h['word_count'] = len(str(h['text']).split())

  def _trim_x(txt: str, limit: int = 276) -> str:
    txt = txt.strip()
    if len(txt) <= limit:
      return txt
    return txt[: limit - 1].rsplit(' ', 1)[0].rstrip('.,;:') + '.'

  x_hooks = [
      {
          'label': 'Hard Truth Opener',
          'text': _trim_x(f'{s0}\n\n{s1}'),
      },
      {
          'label': 'Mechanism Shift',
          'text': _trim_x(f'{h0}\n\n→ {s2}\n→ {s3}'),
      },
      {
          'label': 'Diagnostic Litmus Test',
          'text': _trim_x(f'{s4}\n\n{s5}'),
      },
      {
          'label': 'Verbatim Mic-Drop',
          'text': _trim_x(f'"{q0}"\n\n{s0}', 238),
      },
  ]
  for h in x_hooks:
    h['char_count'] = len(str(h['text']))

  return {'linkedin': li_hooks, 'x': x_hooks}


def generate_grounded_hooks(
    title: str, subtitle: str, blocks: list[dict[str, object]]
) -> dict[str, list[dict[str, object]]]:
  """Generates 4 LinkedIn and 4 X hooks using HOOK_SYSTEM_PROMPT via Gemini."""
  full_article_text = _extract_full_article_text(blocks)
  user_prompt = (
      f'Article Title: {title}\n'
      f'Article Subtitle: {subtitle}\n'
      f'Full Article Text:\n{full_article_text[:15000]}'
  )
  parsed_hooks = _call_gemini_json(HOOK_SYSTEM_PROMPT, user_prompt, timeout_sec=24)
  if (
      isinstance(parsed_hooks, dict)
      and isinstance(parsed_hooks.get('linkedin'), list)
      and len(parsed_hooks['linkedin']) >= 2
      and isinstance(parsed_hooks.get('x'), list)
      and len(parsed_hooks['x']) >= 2
  ):
    for h in parsed_hooks['linkedin']:
      if isinstance(h, dict):
        h['word_count'] = len(str(h.get('text', '')).split())
    for h in parsed_hooks['x']:
      if isinstance(h, dict):
        h['char_count'] = len(str(h.get('text', '')))
    return parsed_hooks

  return _build_multi_archetype_fallback_hooks(title, subtitle, blocks, offset=0)


SINGLE_HOOK_SYSTEM_PROMPT = (
    HOOK_SYSTEM_PROMPT.split('OUTPUT FORMAT:')[0]
    + 'OUTPUT FORMAT:\nReturn ONLY a single valid JSON object matching this exact structure '
    '(no markdown code fences or commentary):\n{"label": "...", "text": "..."}\n'
)


def generate_single_refreshed_hook(
    title: str,
    subtitle: str,
    blocks: list[dict[str, object]],
    platform: str,
    archetype_idx: int,
    exclude_text: str = '',
) -> dict[str, object]:
  """Generates a single fresh hook for the given platform & archetype using HOOK_SYSTEM_PROMPT."""
  is_x = platform == 'x'
  archetypes = X_ARCHETYPES if is_x else LINKEDIN_ARCHETYPES
  label, arch_desc = archetypes[archetype_idx % len(archetypes)]
  full_article_text = _extract_full_article_text(blocks)

  user_prompt = (
      f'Platform: {"X (Twitter)" if is_x else "LinkedIn"}\n'
      f'Required Hook Archetype: {label} — {arch_desc}\n'
      f'Variation Number: {archetype_idx + 1}\n'
      f'Previously Shown Hook (DO NOT repeat this angle or opening sentence): {exclude_text}\n\n'
      f'Article Title: {title}\n'
      f'Article Subtitle: {subtitle}\n'
      f'Full Article Text:\n{full_article_text[:14000]}\n\n'
      'Return ONLY a single JSON object matching this exact schema:\n'
      f'{{"label": "{label}", "text": "..."}}'
  )
  res = _call_gemini_json(SINGLE_HOOK_SYSTEM_PROMPT, user_prompt, timeout_sec=18)
  if isinstance(res, dict):
    if 'text' in res and isinstance(res['text'], str) and res['text'].strip():
      hook_text = res['text'].strip()
      if hook_text != exclude_text.strip():
        return {
            'label': str(res.get('label') or label),
            'text': hook_text,
            'word_count': len(hook_text.split()),
            'char_count': len(hook_text),
        }
    sub_list = res.get('x' if is_x else 'linkedin')
    if isinstance(sub_list, list) and sub_list:
      chosen = sub_list[archetype_idx % len(sub_list)]
      if isinstance(chosen, dict) and chosen.get('text'):
        hook_text = str(chosen['text']).strip()
        if hook_text != exclude_text.strip():
          return {
              'label': str(chosen.get('label') or label),
              'text': hook_text,
              'word_count': len(hook_text.split()),
              'char_count': len(hook_text),
          }

  # Guaranteed distinct multi-archetype fallback grounded in article text
  fallback_pool = _build_multi_archetype_fallback_hooks(
      title, subtitle, blocks, offset=archetype_idx
  )[platform]
  for candidate in fallback_pool:
    if str(candidate.get('text', '')).strip() != exclude_text.strip():
      candidate['label'] = label
      return candidate
  return fallback_pool[archetype_idx % len(fallback_pool)]


BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def _resolve_substack_endpoint(raw_url: str) -> tuple[str, str, str, str]:
  """Resolves ANY Substack URL (subdomain, open.substack.com, custom domain, or post ID)."""
  u = raw_url.strip()
  if not u.startswith(('http://', 'https://')):
    u = 'https://' + u
  parsed = urllib.parse.urlparse(u)
  host = (parsed.hostname or '').lower()
  path = urllib.parse.unquote(parsed.path).rstrip('/')

  if not _is_safe_public_host(host):
    raise ValueError('Please enter a valid public Substack article URL.')

  # 1. open.substack.com/pub/<pub>/p/<slug>
  m_open = re.match(r'^/pub/([a-zA-Z0-9._-]+)/p/([a-zA-Z0-9._-]+)', path)
  if host == 'open.substack.com' and m_open:
    pub, slug = m_open.group(1), m_open.group(2)
    pub_host = f'{pub}.substack.com'
    canonical = f'https://{pub_host}/p/{slug}'
    return f'https://{pub_host}/api/v1/posts/{slug}', pub_host, slug, canonical

  # 2. Any Substack or custom-domain /p/<slug> URL
  m_p = re.search(r'/p/([a-zA-Z0-9._-]+)', path)
  if m_p:
    slug = m_p.group(1)
    canonical = f'https://{host}/p/{slug}'
    return f'https://{host}/api/v1/posts/{slug}', host, slug, canonical

  # 3. Substack home / reader / inbox / profile post ID URLs (e.g. /home/post/p-123456 or /inbox/post/123456)
  m_id = re.search(r'(?:/post/p-|/inbox/post/|/p-)(\d{5,20})', path)
  if m_id:
    post_id = m_id.group(1)
    canonical = f'https://substack.com/home/post/p-{post_id}'
    return (
        f'https://substack.com/api/v1/posts/by-id/{post_id}',
        'substack.com',
        post_id,
        canonical,
    )

  raise ValueError(
      'Please enter a valid Substack post URL (e.g. https://publication.substack.com/p/post-slug '
      'or any custom-domain Substack /p/ link).'
  )


def _ensure_four_hooks_in_cached_article(cached: dict[str, object]) -> dict[str, object]:
  hooks = cached.get('hooks')
  if (
      not isinstance(hooks, dict)
      or len(hooks.get('linkedin', [])) < 4
      or len(hooks.get('x', [])) < 4
  ):
    cached['hooks'] = generate_grounded_hooks(
        str(cached.get('title', '')),
        str(cached.get('subtitle', '')),
        list(cached.get('blocks', [])),
    )
  return cached


def fetch_and_convert_substack_post(article_url: str) -> dict[str, object]:
  """Fetch any Substack article URL from any author, parse all blocks/captions, and convert videos."""
  cleaned_input = article_url.strip().split('?')[0].rstrip('/')
  if cleaned_input in ARTICLE_CACHE:
    return _ensure_four_hooks_in_cached_article(ARTICLE_CACHE[cleaned_input])

  api_url, pub_host, slug, canonical_url = _resolve_substack_endpoint(article_url)
  if canonical_url in ARTICLE_CACHE:
    return _ensure_four_hooks_in_cached_article(ARTICLE_CACHE[canonical_url])

  req = urllib.request.Request(api_url, headers=BROWSER_HEADERS)
  with urllib.request.urlopen(req, timeout=15) as resp:
    raw_json = json.loads(resp.read().decode('utf-8', errors='replace'))

  post_data = raw_json.get('post') if isinstance(raw_json.get('post'), dict) else raw_json
  if not isinstance(post_data, dict) or not post_data.get('body_html'):
    raise ValueError('Could not extract Substack article body from that URL.')

  canonical_from_post = str(post_data.get('canonical_url', '')).strip()
  if canonical_from_post.startswith('https://'):
    parsed_can = urllib.parse.urlparse(canonical_from_post)
    if parsed_can.hostname and _is_safe_public_host(parsed_can.hostname):
      pub_host = parsed_can.hostname.lower()
      canonical_url = canonical_from_post.split('?')[0].rstrip('/')

  title = str(post_data.get('title', '')).strip()
  subtitle = str(post_data.get('subtitle', '')).strip()
  cover_image = str(post_data.get('cover_image', '')).strip()
  post_date = str(post_data.get('post_date', '')).strip()[:10]
  body_html = str(post_data.get('body_html', ''))

  parser = SubstackBodyParser()
  parser.feed(body_html)

  image_count = 0
  video_gif_count = 0
  caption_count = 0

  for block in parser.blocks:
    if block.get('type') == 'image':
      image_count += 1
      if block.get('caption'):
        caption_count += 1
    elif block.get('type') in ('video', 'video_gif'):
      vid_id = str(block.get('mediaUploadId', ''))
      mp4_filename = f'substack_{vid_id}.mp4'
      mp4_disk_path = os.path.join(VAULT_DIR, mp4_filename)
      host_qs = urllib.parse.quote(pub_host, safe='')
      if os.path.isfile(mp4_disk_path) and os.path.getsize(mp4_disk_path) > 1000:
        video_url = f'/api/mp4/{mp4_filename}'
      else:
        video_url = f'/api/mp4/{mp4_filename}?host={host_qs}'
        threading.Thread(
            target=ensure_substack_video_mp4,
            args=(pub_host, vid_id),
            daemon=True,
        ).start()
      block['videoUrl'] = video_url
      block['mp4Url'] = (
          f'{video_url}&download=1' if '?' in video_url else f'{video_url}?download=1'
      )
      video_gif_count += 1
      if block.get('caption'):
        caption_count += 1

  result = {
      'url': canonical_url,
      'subdomain': pub_host,
      'slug': slug,
      'title': title,
      'subtitle': subtitle,
      'coverImage': cover_image,
      'publishedDate': post_date,
      'imageCount': image_count,
      'videoGifCount': video_gif_count,
      'captionCount': caption_count,
      'blocks': parser.blocks,
      'hooks': generate_grounded_hooks(title, subtitle, parser.blocks),
  }
  ARTICLE_CACHE[canonical_url] = result
  ARTICLE_CACHE[cleaned_input] = result
  return result


class BridgeRequestHandler(server.BaseHTTPRequestHandler):
  """Secure HTTP request handler with strict CSP, CSRF, and rate limiting."""

  def _apply_security_headers(self, content_type: str = 'application/json; charset=utf-8') -> None:
    self.send_header('Content-Type', content_type)
    self.send_header(
        'Content-Security-Policy',
        "default-src 'self'; "
        "img-src 'self' data: https: blob:; "
        "media-src 'self' data: https: blob:; "
        "style-src 'self'; script-src 'self'; "
        "connect-src 'self' https://substackcdn.com; "
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self';",
    )
    self.send_header('X-Content-Type-Options', 'nosniff')
    self.send_header('X-Frame-Options', 'DENY')
    self.send_header(
        'Permissions-Policy', 'camera=(), microphone=(), geolocation=()'
    )
    self.send_header('Cache-Control', 'no-store')

  def _check_rate_limit(self) -> bool:
    client_ip = self.client_address[0] if self.client_address else '127.0.0.1'
    now = time.time()
    q = REQUEST_LOG[client_ip]
    while q and now - q[0] > RATE_LIMIT_WINDOW_SEC:
      q.popleft()
    if len(q) >= RATE_LIMIT_MAX_REQUESTS:
      return False
    q.append(now)
    return True

  def _verify_csrf(self) -> bool:
    header_token = self.headers.get('X-CSRF-Token', '')
    cookie_header = self.headers.get('Cookie', '')
    c = cookies.SimpleCookie()
    c.load(cookie_header)
    cookie_morsel = c.get('__Host-csrf_token') or c.get('csrf_token')
    cookie_token = cookie_morsel.value if cookie_morsel else ''
    if not header_token or not cookie_token:
      return False
    return (
        secrets.compare_digest(header_token, CSRF_TOKEN)
        and secrets.compare_digest(cookie_token, CSRF_TOKEN)
    )

  def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode('utf-8')
    self.send_response(status_code)
    self._apply_security_headers('application/json; charset=utf-8')
    self.send_header('Content-Length', str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def do_PUT(self) -> None:  # pylint: disable=invalid-name
    self._send_json(405, {'error': 'Method Not Allowed'})

  def do_DELETE(self) -> None:  # pylint: disable=invalid-name
    self._send_json(405, {'error': 'Method Not Allowed'})

  def do_TRACE(self) -> None:  # pylint: disable=invalid-name
    self._send_json(405, {'error': 'Method Not Allowed'})

  def do_HEAD(self) -> None:  # pylint: disable=invalid-name
    self.send_response(200)
    self._apply_security_headers('text/html; charset=utf-8')
    self.end_headers()

  def do_GET(self) -> None:  # pylint: disable=invalid-name
    if not self._check_rate_limit():
      self._send_json(429, {'error': 'Too Many Requests'})
      return

    parsed = urllib.parse.urlparse(self.path)
    route = parsed.path

    if route == '/api/session':
      body = json.dumps({
          'csrfToken': CSRF_TOKEN,
          'recentPosts': RECENT_SUBSTACK_POSTS,
      }).encode('utf-8')
      self.send_response(200)
      self._apply_security_headers('application/json; charset=utf-8')
      self.send_header(
          'Set-Cookie',
          f'csrf_token={CSRF_TOKEN}; Path=/; HttpOnly; SameSite=Strict',
      )
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return

    if route.startswith('/api/mp4/'):
      mp4_name = os.path.basename(route[len('/api/mp4/'):])
      if not SAFE_MP4_NAME_RE.match(mp4_name):
        self._send_json(404, {'error': 'MP4 not found'})
        return
      vault_root = os.path.realpath(VAULT_DIR) + os.sep
      mp4_path = os.path.realpath(os.path.join(VAULT_DIR, mp4_name))
      if not mp4_path.startswith(vault_root):
        self._send_json(404, {'error': 'MP4 file not found'})
        return
      if not (os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 1000) and mp4_name.startswith('substack_'):
        qs = urllib.parse.parse_qs(parsed.query)
        host_param = (qs.get('host') or [''])[0]
        vid_uuid = mp4_name[len('substack_'):-len('.mp4')]
        if host_param and UUID_RE.match(vid_uuid):
          ensure_substack_video_mp4(host_param, vid_uuid)
      if not os.path.isfile(mp4_path):
        self._send_json(404, {'error': 'MP4 file not found'})
        return

      file_size = os.path.getsize(mp4_path)
      range_header = self.headers.get('Range', '')
      if range_header.startswith('bytes=') and 'download=1' not in parsed.query:
        try:
          range_spec = range_header[len('bytes='):].split(',')[0].strip()
          start_str, end_str = range_spec.split('-', 1)
          start = int(start_str) if start_str else 0
          end = int(end_str) if end_str else file_size - 1
          start = max(0, min(start, file_size - 1))
          end = max(start, min(end, file_size - 1))
          chunk_len = end - start + 1
          with open(mp4_path, 'rb') as f:
            f.seek(start)
            chunk = f.read(chunk_len)
          self.send_response(206)
          self._apply_security_headers('video/mp4')
          self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
          self.send_header('Accept-Ranges', 'bytes')
          self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
          self.send_header('Content-Length', str(len(chunk)))
          self.end_headers()
          self.wfile.write(chunk)
          return
        except (ValueError, OSError):
          pass

      with open(mp4_path, 'rb') as f:
        mp4_bytes = f.read()
      self.send_response(200)
      self._apply_security_headers('video/mp4')
      self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
      self.send_header('Accept-Ranges', 'bytes')
      if 'download=1' in parsed.query:
        self.send_header(
            'Content-Disposition',
            f'attachment; filename="{mp4_name}"',
        )
      self.send_header('Content-Length', str(len(mp4_bytes)))
      self.end_headers()
      self.wfile.write(mp4_bytes)
      return

    if route == '/api/convert':
      qs = urllib.parse.parse_qs(parsed.query)
      article_url = (qs.get('url') or [''])[0].strip()
      try:
        article = fetch_and_convert_substack_post(article_url)
        body = json.dumps({'article': article}).encode('utf-8')
        self.send_response(200)
        self._apply_security_headers('application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      except ValueError as ve:
        body = json.dumps({'error': str(ve)}).encode('utf-8')
        self.send_response(400)
        self._apply_security_headers('application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      except (urllib.error.URLError, TimeoutError, KeyError):
        body = json.dumps({
            'error': 'Could not fetch Substack URL. Check the URL and try again.'
        }).encode('utf-8')
        self.send_response(502)
        self._apply_security_headers('application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      return

    if route == '/api/refresh-hook':
      qs = urllib.parse.parse_qs(parsed.query)
      article_url = (qs.get('url') or [''])[0].strip()
      platform = (qs.get('platform') or ['linkedin'])[0].strip().lower()
      if platform not in ('linkedin', 'x'):
        platform = 'linkedin'
      idx_str = (qs.get('index') or ['1'])[0].strip()
      archetype_idx = int(idx_str) if idx_str.isdigit() else 1
      exclude_text = (qs.get('exclude') or [''])[0].strip()[:250]

      try:
        article = fetch_and_convert_substack_post(article_url)
        hook_obj = generate_single_refreshed_hook(
            str(article.get('title', '')),
            str(article.get('subtitle', '')),
            list(article.get('blocks', [])),
            platform,
            archetype_idx,
            exclude_text,
        )
        if not hook_obj:
          hooks_pool = article.get('hooks', {}).get(platform, [])
          if hooks_pool:
            hook_obj = hooks_pool[archetype_idx % len(hooks_pool)]
        body = json.dumps({'hook': hook_obj, 'status': LAST_LLM_STATUS}).encode('utf-8')
        self.send_response(200)
        self._apply_security_headers('application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      except (ValueError, urllib.error.URLError, TimeoutError, KeyError) as err:
        body = json.dumps({'error': str(err)}).encode('utf-8')
        self.send_response(400)
        self._apply_security_headers('application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      return

    if route == '/api/articles-cache':
      self._send_json(200, {'articles': ARTICLE_CACHE})
      return

    if route == '/api/download-image':
      qs = urllib.parse.parse_qs(parsed.query)
      img_url = (qs.get('url') or [''])[0]
      slot_str = (qs.get('slot') or ['1'])[0]
      if not re.match(r'^\d{1,2}$', slot_str):
        slot_str = '1'
      if not img_url.startswith('https://substackcdn.com/image/fetch/'):
        self._send_json(400, {'error': 'Only Substack CDN images are permitted'})
        return
      try:
        req = urllib.request.Request(
            img_url, headers={'User-Agent': 'Mozilla/5.0 (StackConverter/2.0)'}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
          img_bytes = resp.read(50_000_000)
        self.send_response(200)
        self._apply_security_headers('image/png')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        self.send_header(
            'Content-Disposition',
            f'attachment; filename="Slot_{slot_str}_Substack_Image.png"',
        )
        self.send_header('Content-Length', str(len(img_bytes)))
        self.end_headers()
        self.wfile.write(img_bytes)
      except (urllib.error.URLError, TimeoutError):
        self._send_json(502, {'error': 'Failed to download Substack image'})
      return

    rel_path = 'index.html' if route == '/' else route.lstrip('/')
    safe_name = os.path.basename(rel_path)
    target_path = os.path.realpath(os.path.join(PUBLIC_DIR, safe_name))
    public_root = os.path.realpath(PUBLIC_DIR) + os.sep
    if not target_path.startswith(public_root) or not os.path.isfile(target_path):
      self._send_json(404, {'error': 'Not Found'})
      return

    mime_map = {
        '.html': 'text/html; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.js': 'application/javascript; charset=utf-8',
        '.jpg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.zip': 'application/zip',
    }
    ext = os.path.splitext(safe_name)[1].lower()
    content_type = mime_map.get(ext, 'application/octet-stream')

    with open(target_path, 'rb') as f:
      data = f.read()

    self.send_response(200)
    self._apply_security_headers(content_type)
    if route == '/':
      self.send_header(
          'Set-Cookie',
          f'csrf_token={CSRF_TOKEN}; Path=/; HttpOnly; SameSite=Strict',
      )
    self.send_header('Content-Length', str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def do_POST(self) -> None:  # pylint: disable=invalid-name
    if not self._check_rate_limit():
      self._send_json(429, {'error': 'Too Many Requests'})
      return

    if not self._verify_csrf():
      self._send_json(403, {'error': 'CSRF token validation failed'})
      return

    content_len = int(self.headers.get('Content-Length', '0'))
    if content_len <= 0 or content_len > 100_000:
      self._send_json(400, {'error': 'Invalid request payload size.'})
      return

    raw_body = self.rfile.read(content_len)
    try:
      data = json.loads(raw_body.decode('utf-8'))
    except ValueError:
      self._send_json(400, {'error': 'Malformed JSON payload'})
      return

    parsed = urllib.parse.urlparse(self.path)
    if parsed.path == '/api/convert-url':
      article_url = str(data.get('url', '')).strip()
      try:
        article = fetch_and_convert_substack_post(article_url)
        self._send_json(200, {'article': article})
      except ValueError as ve:
        self._send_json(400, {'error': str(ve)})
      except (urllib.error.URLError, TimeoutError, KeyError):
        self._send_json(
            502,
            {'error': 'Could not fetch Substack URL. Check the URL and try again.'},
        )
      return

    self._send_json(404, {'error': 'Endpoint not found'})


def main() -> None:
  fqdn = socket.getfqdn()
  if fqdn and fqdn not in ('localhost', '127.0.0.1'):
    try:
      ext_httpd = server.ThreadingHTTPServer((fqdn, PORT), BridgeRequestHandler)
      t = threading.Thread(target=ext_httpd.serve_forever, daemon=True)
      t.start()
      logging.info('Network listener active on http://%s:%d', fqdn, PORT)
    except OSError as err:
      logging.warning('Could not bind to %s:%d (%s)', fqdn, PORT, err)

  httpd = server.ThreadingHTTPServer((HOST, PORT), BridgeRequestHandler)
  logging.info('Stack Converter listening on http://%s:%d', HOST, PORT)
  httpd.serve_forever()


if __name__ == '__main__':
  main()
