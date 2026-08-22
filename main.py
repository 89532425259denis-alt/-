# -*- coding: utf-8 -*-
# GOST-ASSISTANT v3.3 — +img-captions(topic) +img-refs +free-sources(Pixabay/Pexels) (GOST 7.32-2017)
# Applied fixes: #1-#9 (see apply_gost_fixes.py)

from __future__ import annotations

# ============================================================================
# Openverse Auto-Refresh Token (из client credentials)
# ============================================================================
_OPENVERSE_TOKEN_CACHE: str = ""
_OPENVERSE_TOKEN_EXPIRY: float = 0.0

async def _get_fresh_openverse_token() -> str:
    """Автоматически получает свежий токен Openverse из client_id/secret.
    Кэширует на время жизни токена (обычно 12ч). Если OPENVERSE_TOKEN уже
    задан вручную в конфиге — использует его (ручной приоритетнее)."""
    global _OPENVERSE_TOKEN_CACHE, _OPENVERSE_TOKEN_EXPIRY
    import time

    # Если в конфиге задан готовый токен — используем его (ручной режим)
    manual = cfg("OPENVERSE_TOKEN", "").strip()
    if manual:
        return manual

    # Если есть закэшированный токен и он ещё не истёк — возвращаем
    if _OPENVERSE_TOKEN_CACHE and time.time() < _OPENVERSE_TOKEN_EXPIRY:
        return _OPENVERSE_TOKEN_CACHE

    # Иначе получаем новый из client credentials
    client_id = cfg("OPENVERSE_CLIENT_ID", "").strip()
    client_secret = cfg("OPENVERSE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return ""  # нет credentials — научные источники не работают

    url = "https://api.openverse.org/v1/auth_tokens/token/"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials"
    }

    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    token = result.get("access_token", "")
                    expires_in = int(result.get("expires_in", 43200))
                    _OPENVERSE_TOKEN_CACHE = token
                    _OPENVERSE_TOKEN_EXPIRY = time.time() + expires_in - 300  # -5 мин запас
                    print(f"[OPENVERSE] Получен свежий токен (истекает через {expires_in}с)")
                    return token
                else:
                    print(f"[OPENVERSE] Ошибка получения токена: {resp.status}")
                    return ""
    except Exception as e:
        print(f"[OPENVERSE] Не удалось обновить токен: {e}")
        return ""


"""ГОСТ-АССИСТЕНТ v2.7-fix16 — ТОЧНЫЕ СТРАНИЦЫ + ДИСЦИПЛИНА

Главные изменения v2.1 относительно v2.0:
────────────────────────────────────────────────────────────────
1. ★ ТОЧНОЕ ЧИСЛО СТРАНИЦ (±1).
   После генерации DOCX конвертируется в PDF через LibreOffice,
   реально пересчитываются страницы (pypdf / pdfinfo). Если страниц
   меньше цели — главы дозаполняются; если больше — обрезаются
   по границам абзацев. Цикл до 3 итераций.

2. ★ ПРАВИЛЬНАЯ НУМЕРАЦИЯ СТРАНИЦ ПО ГОСТ.
   Титульный лист включается в общую нумерацию, но цифра на нём
   не отображается (different_first_page_header_footer). На странице
   содержания появляется цифра «2», далее сквозная. Номер — внизу
   по центру (или сверху, как настроено в GOST).

3. ★ ЛИМИТ ДЛЯ БЕСПЛАТНОГО РЕЖИМА — 1 ГЕНЕРАЦИЯ В 5 ДНЕЙ.
   Реализовано через кулдаун (FREE_COOLDOWN_SECONDS = 432000),
   независимо от смены календарных суток. VIP и платные — без лимитов.

4. ★ v2.2: подглавы генерируются отдельными промптами (нет пустых глав).
   Заключение пишется ПОСЛЕ глав и видит их реальное содержание.
   ГОСТ-сноски [1, с. 45] обязательны в каждом абзаце.

5. Все базовые возможности v2.0 сохранены: красивые заголовки глав,
   умный/классический стиль, прогресс-бар с ETA, парсинг «своего ГОСТ»,
   custom-документы, аккуратные сообщения с HTML-форматированием.
"""

import asyncio
import base64
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime

from urllib.parse import quote_plus

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT

# Локальные модули: аналитика (п.5 ТОП-5) и модульный поиск картинок (п.3 ТОП-5).
try:
    from metrics import get_metrics, GenerationMetrics
    from image_providers import CallableProvider, AggregateImageSearcher
except ImportError:
    # Заглушки, если модули отсутствуют — бот продолжает работать без
    # аналитики и поиска картинок, не падая при старте.
    def get_metrics():
        class _DummyMetrics:
            def log(self, *args, **kwargs):
                return None
        return _DummyMetrics()

    GenerationMetrics = dict

    class _DummyCallableProvider:
        def __init__(self, *args, **kwargs):
            pass

    CallableProvider = _DummyCallableProvider

    class _DummyAggregateImageSearcher:
        def __init__(self, *args, **kwargs):
            pass

        async def search_all(self, *args, **kwargs):
            return []

        async def refill(self, *args, **kwargs):
            return []

    AggregateImageSearcher = _DummyAggregateImageSearcher

try:
    from PIL import Image as PILImage, ImageDraw, ImageFont
except Exception:  # Pillow может быть не установлен на старом деплое
    PILImage = None
    ImageDraw = None
    ImageFont = None

# Quality Pipeline modules (checker, error_logger, orchestrator).
# КРИТИЧЕСКИЙ ФИКС: раньше здесь был sys.path.insert("/tmp/codex-web-uploads/...")
# — путь, существовавший только на одной машине. Бот падал при старте на любом
# другом сервере. Теперь модули лежат РЯДОМ с ботом и импортируются оттуда.
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checker import validate_docx as qg_validate, autofix_docx as qg_autofix, quick_check, ValidationResult
from error_logger import log_error, log_api_call, PipelineError, APIFailure, get_error_summary, print_error_summary
from quality_pipeline import (
    PipelineOrchestrator, StructureApproval, format_structure_for_approval,
    validate_block_content, validate_json_structure, get_pipeline_status_emoji,
)
# Строгая база знаний по ГОСТам — единый источник пра��ды для всех промптов.
from gost_rules import (
    CRITICAL_GOST_RULES, GOLDEN_EXAMPLES,
    gost_text_rules_prompt, gost_bibliography_rules_prompt, words_for_chars,
    get_golden_example,
)


# ═══════════════════════════════════════════════════════════════
#  КРАСИВАЯ АНИМАЦИЯ ПРОГРЕССА С ETA
# ═══════════════════════════════════════════════════════════════

# Рамки спиннера — меняются каждую секунду для иллюзии движе��ия
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "��", "⠏"]
_BAR_FULL  = "█"
_BAR_HALF  = "▓"
_BAR_EMPTY = "░"


def _spinner() -> str:
    """Возвращает текущий кадр спиннера на основе текущего времени."""
    idx = int(time.monotonic() * 4) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[idx]


def _progress_bar(done: int, total: int, width: int = 16) -> str:
    """Улучшенный прогресс-бар с более плавным визуалом."""
    total = max(1, int(total))
    done  = max(0, min(int(done), total))
    ratio = done / total
    filled = int(round(width * ratio))

    # Используем более современные символы для заполнения
    # █ - полный, ▓ - почти полный, ░ - пустой
    bar = "█" * filled + "░" * (width - filled)

    # Добавляем динамический «бегунок» в конец заполненной части, если работа не завершена
    if 0 < filled < width:
        frame = int(time.monotonic() * 2) % 2
        marker = "▓" if frame == 0 else "▒"
        # Заменяем последний заполненный символ на маркер
        bar = bar[:filled-1] + marker + bar[filled:]

    return bar


def _fmt_time(seconds: float) -> str:
    """Форматирует секунды в mm:ss."""
    s = max(0, int(seconds))
    m, s = divmod(s, 60)
    if m:
        return f"{m}м {s:02d}с"
    return f"{s}с"


@dataclass
class Progress:
    """Красивый прогресс с анимацией, ETA и шагами."""
    msg: Message
    title: str
    total_steps: int
    model_name: str = ""
    done: int = 0
    label: str = "Подготовка..."
    _last_text: str = field(default="", repr=False)
    _last_ts: float = field(default=0.0, repr=False)
    _start_ts: float = field(default_factory=time.monotonic, repr=False)
    _step_labels: list = field(default_factory=list, repr=False)
    _eta_prev: float | None = field(default=None, repr=False)

    def _elapsed(self) -> float:
        return time.monotonic() - self._start_ts

    def _eta(self) -> str:
        """Оценка оставшегося времени (fix11).

        - Пока ни один шаг не закрыт: даём приблизительную оценку,
          исходя из общего числа шагов и среднего времени на шаг ~25 с
          (если elapsed > 3 с), вместо вечного «считаю…».
        - Со 2-го шага: добавляем EMA сглаживание, чтобы оценка
          не прыгала при разных по длительности шагах.
        """
        elapsed = self._elapsed()
        remaining_steps = max(0, self.total_steps - self.done)
        if remaining_steps == 0:
            return "≈0с"

        if self.done == 0:
            if elapsed < 3.0:
                return "считаю…"
            # эвристика: ~25 секунд на шаг, минимум 10
            est_per_step = max(10.0, elapsed / 0.5)
            return "≈" + _fmt_time(est_per_step * remaining_steps)

        rate = self.done / elapsed
        if rate <= 0:
            return "…"
        eta_sec = remaining_steps / rate
        # Сглаживание: микшируем с предыдущим значением
        prev = getattr(self, "_eta_prev", None)
        if prev is None:
            self._eta_prev = eta_sec
        else:
            self._eta_prev = 0.6 * prev + 0.4 * eta_sec
            eta_sec = self._eta_prev
        return _fmt_time(eta_sec)

    def render(self) -> str:
        pct      = int(self.done * 100 / max(1, self.total_steps))
        bar      = _progress_bar(self.done, self.total_steps)
        spin     = _spinner()
        elapsed  = _fmt_time(self._elapsed())
        eta      = self._eta()
        model    = f"\n🤖 <b>Модель:</b> <i>{self.model_name}</i>" if self.model_name else ""
        step_num = min(self.done + 1, self.total_steps)

        return (
            f"{spin} <b>{self.title}</b>\n"
            f"<code>{bar}</code> <b>{pct}%</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Шаг {step_num}/{self.total_steps}:</b> {self.label}{model}\n"
            f"⏱ <code>{elapsed}</code> ➔ ⏳ <code>{eta}</code>"
        )

    async def update(
        self,
        *,
        label: str | None = None,
        step_done: bool = False,
        model_name: str | None = None,
        force: bool = False,
        min_interval: float = 1.0,
    ) -> None:
        if label is not None:
            self.label = label
        if model_name is not None:
            self.model_name = model_name
        if step_done:
            self.done = min(self.total_steps, self.done + 1)

        text = self.render()
        now  = time.monotonic()

        # Не спамим обновлениями чаще min_interval
        if not force and text == self._last_text:
            return
        if not force and (now - self._last_ts) < min_interval:
            return

        try:
            await self.msg.edit_text(text, parse_mode="HTML")
            self._last_text = text
            self._last_ts   = now
        except Exception:
            pass

    async def finish(self, text: str) -> None:
        try:
            await self.msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    async def delete(self) -> None:
        try:
            await self.msg.delete()
        except Exception:
            pass

    async def animate_loop(self, stop_event: asyncio.Event) -> None:
        """Фоновый цикл (fix11): обновляет спиннер/змейку/ETA каждые ~1.5 с.

        Telegram режет частые edit_text (429 Flood). Минимальный безопасный
        интервал — 1.2-1.5 с. Бар анимируется самим временем (frame =
        int(monotonic()*2)), поэтому каждые 1.5 с кадры реально меняются.
        """
        while not stop_event.is_set():
            try:
                text = self.render()
                now  = time.monotonic()
                if text != self._last_text and (now - self._last_ts) >= 1.4:
                    await self.msg.edit_text(text, parse_mode="HTML")
                    self._last_text = text
                    self._last_ts   = now
            except Exception:
                pass
            await asyncio.sleep(1.5)


# ═══════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════

TOKENS = {
    "BOT_TOKEN": "",
    "OPENROUTER_KEY": "",
    "DEEPSEEK_KEY": "",
    "GROQ_KEY": "",
    "VIP_USERS": "5291613279",
}

CONFIG_FILE      = "bot_config.json"
GOST_CONFIG_FILE = "gost_configs.json"
USAGE_FILE       = "usage_limits.json"
NUMERIC_FACTS_FILE = "numeric_facts.json"


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Ошибка сохранения {path}: {e}")


CONFIG      = _load_json(CONFIG_FILE, {})
GOST_CONFIGS = _load_json(GOST_CONFIG_FILE, {})


def cfg(name: str, default: str = "") -> str:
    return (TOKENS.get(name) or os.getenv(name) or CONFIG.get(name) or default).strip()


BOT_TOKEN = cfg("BOT_TOKEN")
# if not BOT_TOKEN:
    # raise SystemExit("❌ ОШИБКА: не вставлен BOT_TOKEN (в TOKENS, .env или bot_config.json)")

OPENROUTER_KEY = cfg("OPENROUTER_KEY")
DEEPSEEK_KEY   = cfg("DEEPSEEK_KEY")
GROQ_KEY       = cfg("GROQ_KEY")

OPENROUTER_BASE_URL = cfg("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
DEEPSEEK_BASE_URL   = cfg("DEEPSEEK_BASE_URL",   "https://api.deepseek.com/v1").rstrip("/")
GROQ_BASE_URL       = cfg("GROQ_BASE_URL",       "https://api.groq.com/openai/v1").rstrip("/")

DEEPSEEK_MODEL         = cfg("DEEPSEEK_MODEL",         "deepseek-chat")
OPENROUTER_R1_MODEL    = cfg("OPENROUTER_R1_MODEL",    "deepseek/deepseek-r1-0528:free")
OPENROUTER_GEMINI_MODEL = cfg("OPENROUTER_GEMINI_MODEL", "google/gemini-2.5-flash")
GROQ_MODEL             = cfg("GROQ_MODEL",             "llama-3.3-70b-versatile")

FREE_MODEL_KEY = cfg("FREE_MODEL_KEY", "deepseek")

# Лимиты для БЕСПЛАТНОГО режима
FREE_MAX_PAGES    = int(cfg("FREE_MAX_PAGES",         "15"))
# Кулдаун между бесплатными генерациями: по умолчанию 5 суток (432000 сек).
# Лимит "1 генерация в 5 дней" реализован именно через кулдаун, а не дневной счётчик —
# это даёт точное окно 5×24 ч от момента предыдущей генерации.
FREE_COOLDOWN     = int(cfg("FREE_COOLDOWN_SECONDS",  str(5 * 24 * 60 * 60)))
# FREE_DAILY_LIMIT оставлен для совместимости; основной фильтр — кулдаун.
FREE_DAILY_LIMIT  = int(cfg("FREE_DAILY_LIMIT",       "0"))   # 0 = без дневного лимита

# Лимиты для ПЛАТНОГО режима — платят деньги, получают безлимит
# PAID_DAILY_LIMIT = 0 означает "без лимита"
PAID_DAILY_LIMIT  = int(cfg("PAID_DAILY_LIMIT",       "0"))   # 0 = безлимит
PAID_COOLDOWN     = int(cfg("PAID_COOLDOWN_SECONDS",  "0"))   # 0 = нет кулдауна

# Символов на страницу (ГОСТ: ~1800-2000 знаков с пробелами на стр A4 14pt 1.5 интервал)
# Глобальное значение по умолчанию, если ГОСТ не передан
CHARS_PER_PAGE = int(cfg("CHARS_PER_PAGE", "1850"))

# Веб-источники: бот умеет подтягивать реальные источники из открытых научных
# каталогов (OpenAlex/Crossref) и читать URL, которые пользователь прислал в
# материалах. Если сервер без доступа к сети — функции тихо отключатся.
ENABLE_WEB_SOURCES = cfg("ENABLE_WEB_SOURCES", "1").lower() not in ("0", "false", "no", "off")
# Отдельный флаг для поиска изображений: даже если веб-источники для литературы отключены,
# картинки можно оставить включёнными.
ENABLE_IMAGE_SEARCH = cfg("ENABLE_IMAGE_SEARCH", "1").lower() not in ("0", "false", "no", "off")  # user-patch: авто-поиск выключен — пользователь сам присылает ссылки

# Quality Pipeline: RAG-режим (только по предоставленным текстам)
RAG_ENABLED = cfg("RAG_ENABLED", "0").lower() not in ("0", "false", "no", "off")
# Quality Pipeline: Human-in-the-loop (утверждение структуры)
HUMAN_IN_LOOP_ENABLED = cfg("HUMAN_IN_LOOP_ENABLED", "1").lower() not in ("0", "false", "no", "off")
WEB_SOURCE_TIMEOUT = int(cfg("WEB_SOURCE_TIMEOUT", "12"))
MAX_WEB_SOURCES    = int(cfg("MAX_WEB_SOURCES", "25"))  # user-patch: было 12 → стало 25
MIN_REAL_SOURCES   = int(cfg("MIN_REAL_SOURCES", "15"))  # user-patch: было 10 → стало 15
BIB_SOURCE_TARGET  = int(cfg("BIB_SOURCE_TARGET", "20"))  # user-patch: было 12 → стало 20
FILL_UNKNOWN_CITATION_PAGES = cfg("FILL_UNKNOWN_CITATION_PAGES", "1").lower() not in ("0", "false", "no", "off")
# Поле Word TOC иногда показывает пользователю служебную заглушку/код поля.
# По умолчанию формируем статическое содержание без поля, чтобы в DOCX не было
# видимых placeholder-строк. При необходимости можно включить обновляемое поле.
TOC_USE_WORD_FIELD = cfg("TOC_USE_WORD_FIELD", "0").lower() not in ("0", "false", "no", "off")

def calculate_chars_per_page(gost: dict) -> int:
    """Расчёт количества знаков с пробелами на страницу.

    Эмпирические значения, полученные сборкой одностраничных DOCX→PDF в
    LibreOffice и подсчётом фактических символов. Базовая точка — каноничный
    ГОСТ 7.32: Times New Roman 14 pt, межстрочный 1.5, поля 30/10/20/20 мм →
    ~1800 знаков на страницу. От базовой точки масштабируем по полям, кеглю
    и межстрочному интервалу.
    """
    font_size    = int(gost.get("font_size", 14))
    line_spacing = float(gost.get("line_spacing", 1.5))
    left_mm   = int(gost.get("left_margin_mm",   30))
    right_mm  = int(gost.get("right_margin_mm",  10))
    top_mm    = int(gost.get("top_margin_mm",    20))
    bottom_mm = int(gost.get("bottom_margin_mm", 20))

    # Базовая площадь текстового блока (TNR 14, 1.5, поля 30/10/20/20).
    BASE_CHARS   = 1800.0
    BASE_W_MM    = 210 - 30 - 10   # 170
    BASE_H_MM    = 297 - 20 - 20   # 257

    text_w = max(60, 210 - left_mm - right_mm)
    text_h = max(60, 297 - top_mm - bottom_mm)

    # Площадь блока линейно влияет на «вместимость».
    area_factor = (text_w / BASE_W_MM) * (text_h / BASE_H_MM)

    # Кегль: ширина и высота строки пропорциональны размеру шрифта,
    # значит вместимость ~ (14 / fs)^2.
    font_factor = (14.0 / max(10, font_size)) ** 2

    # М��жстрочный интервал: вместимость ~ 1.5 / spacing.
    spacing_factor = 1.5 / max(1.0, line_spacing)

    chars = BASE_CHARS * area_factor * font_factor * spacing_factor
    return max(900, min(3200, int(round(chars))))

# Страниц без текста (титул + содержание)
NON_TEXT_PAGES = int(cfg("NON_TEXT_PAGES", "2"))

DEEPSEEK_PRICE  = int(cfg("DEEPSEEK_PRICE",           "5"))
GROQ_PRICE      = int(cfg("GROQ_PRICE",               "3"))
OR_GEMINI_PRICE = int(cfg("OPENROUTER_GEMINI_PRICE",  "7"))
# Доплата за режим с иллюстрациями: поиск, проверка и вставка изображений по ГОСТ.
IMAGES_EXTRA_PRICE_PER_PAGE = int(cfg("IMAGES_EXTRA_PRICE_PER_PAGE", "3"))
# Цена за редактирование/исправление уже готовой работы (фикс. стоимость в ⭐).
EDIT_PRICE = int(cfg("EDIT_PRICE", "15"))
MAX_WORK_IMAGES = int(cfg("MAX_WORK_IMAGES", "5"))

# Сколько работ генерируется ОДНОВРЕМЕННО на весь бот.
# Раньше было 1 → один пользователь блокировал всех остальных, и нельзя было
# запустить вторую работу в том же чате. Теперь по умолчанию 4 параллельных
# генерации (настраивается переменной окружения MAX_PARALLEL). aiogram уже
# обрабатывает апдейты конкурентно, поэтому ограничиваем лишь тяжёлую
# генерацию, чтобы не перегрузить API/LibreOffice.
MAX_PARALLEL  = max(1, int(cfg("MAX_PARALLEL", "4")))
GEN_SEMAPHORE = asyncio.Semaphore(MAX_PARALLEL)

# Ограничение ОДНОВРЕМЕННЫХ генераций на одного пользователя — чтобы человек
# мог запустить несколько работ подряд, но не «забил» всю очередь. 0 = без лимита.
MAX_PARALLEL_PER_USER = max(0, int(cfg("MAX_PARALLEL_PER_USER", "2")))
# Счётчик а��тивных генераций по user_id (для нескольких работ в одном чате).
# FIX (гонка данных): все операции с этим словарём — ТОЛЬКО через
# _gen_slot_acquire/_gen_slot_release под asyncio.Lock. Без блокировки при
# параллельных запросах check-then-increment давал гонку: счётчик уходил
# в минус и лимит MAX_PARALLEL_PER_USER переставал работать.
_active_gen_per_user: dict[int, int] = {}
_active_gen_lock = asyncio.Lock()


async def _gen_slot_acquire(uid: int) -> bool:
    """Атомарно проверяет лимит и занимает слот генерации пользователя.

    Возвращает False, если лимит MAX_PARALLEL_PER_USER уже исчерпан.
    Проверка и инкремент выполняются под одним Lock — гонка невозможна.
    """
    async with _active_gen_lock:
        if MAX_PARALLEL_PER_USER and _active_gen_per_user.get(uid, 0) >= MAX_PARALLEL_PER_USER:
            return False
        _active_gen_per_user[uid] = _active_gen_per_user.get(uid, 0) + 1
        return True


async def _gen_slot_release(uid: int) -> None:
    """Атомарно освобождает слот генерации (не даёт счётчику уйти в минус)."""
    async with _active_gen_lock:
        cur = _active_gen_per_user.get(uid, 0)
        if cur <= 1:
            _active_gen_per_user.pop(uid, None)
        else:
            _active_gen_per_user[uid] = cur - 1

VIP_USERS = {int(x) for x in cfg("VIP_USERS", "").replace(" ", "").split(",") if x.isdigit()}


# ═══════════════════════════════════════════════════════════════
#  ТИПЫ ДОКУМЕНТОВ
# ═══════════════════════════════════════════════════════════════

DOC_TYPES = {
    "referat": {
        "name": "📄 Реферат",
        "word": "РЕФЕРАТ",
        "min_pages": 10,
        "max_pages": 30,
        "structure": "Введение · 2 главы с подглавами · Заключение · Список литературы",
        "desc": "Обзор литературы по теме, изложение основных концепций и выводы",
    },
    "kursovaya": {
        "name": "📚 Курсовая работа",
        "word": "КУРСОВАЯ РАБОТА",
        "min_pages": 25,
        "max_pages": 50,
        "structure": "Введение · 3 главы с подглавами (§) · Заключение · Библиография",
        "desc": "Самостоятельное исследование с анализом, практической частью и выводами",
    },
    "doklad": {
        "name": "🎤 Доклад",
        "word": "ДОКЛАД",
        "min_pages": 5,
        "max_pages": 15,
        "structure": "Введение · Основная часть · Заключение",
        "desc": "Краткий обзор темы для устного выступления",
    },
    "esse": {
        "name": "✍️ Эссе",
        "word": "ЭССЕ",
        "min_pages": 3,
        "max_pages": 10,
        "structure": "Вступление · А��гументы · Авторская позиция",
        "desc": "Авторский взгляд на проблему с аргументацией и личными выводами",
    },
    "kontrolnaya": {
        "name": "📝 Контрольная работа",
        "word": "КОНТРОЛЬНАЯ РАБОТА",
        "min_pages": 10,
        "max_pages": 25,
        "structure": "Теоретическая часть · Практическая часть · Выводы",
        "desc": "Проверочная работа по теории и практике дисциплины",
    },
    "final_project": {
        "name": "📦 Итоговый проект",
        "word": "ИТОГОВЫЙ ПРОЕКТ",
        "min_pages": 35,
        "max_pages": 80,
        "structure": "Введение · 4 главы · Заключение · Приложения · Источники",
        "desc": "Комплексный проект с теорией, практикой и проектной частью",
    },
    "final_referat": {
        "name": "📑 Итоговый реферат",
        "word": "ИТОГОВЫЙ РЕФЕРАТ",
        "min_pages": 15,
        "max_pages": 40,
        "structure": "Введение · 3 главы с подглавами · Заключение · Список литературы",
        "desc": "Расширенный итоговый реферат по ключевой дисциплине с глубоким анализом литературы",
    },
    "article": {
        "name": "📝 Научная статья",
        "word": "НАУЧНАЯ СТАТЬЯ",
        "min_pages": 5,
        "max_pages": 15,
        "structure": "Аннотация · Ключевые слова · Введение · Обзор литературы · Методология · Результаты · Заключение · Список источников",
        "desc": "Научная публикация по ГОСТ Р 7.0.7-2021 (требования к статьям)",
    },
    "vkr": {
        "name": "🎓 ВКР (Дипломная работа)",
        "word": "ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА",
        "min_pages": 40,
        "max_pages": 100,
        "structure": "Введение · 3-4 главы с подглавами · Практический раздел · Заключение · Список использованны�� источников",
        "desc": "Выпускная квалификационная работа (бакалаврская работа или дипломный проект) по ГОСТ",
    },
    "report_practice": {
        "name": "📋 Отчёт по практике",
        "word": "ОТЧЁТ ПО ПРАКТИКЕ",
        "min_pages": 15,
        "max_pages": 35,
        "structure": "Введение · 3 раздела (характеристика · анализ · рекомендации) · Заключение · Список источников · Приложения",
        "desc": "Отчёт о прохождении учебной/производственной практики с анализом деятельности организации",
    },
    "course_project": {
        "name": "🛠 Курсовой проект",
        "word": "КУРСОВОЙ ПРОЕКТ",
        "min_pages": 25,
        "max_pages": 50,
        "structure": "Введение · 3 главы (теория · проектирование · расчёт/реализация) · Заключение · Список литературы",
        "desc": "Проектно-расчётная работа с практической разработкой и обоснованием решений",
    },
    "master": {
        "name": "🎓 Магистерская диссертация",
        "word": "М����ГИСТЕРСКАЯ ДИССЕРТАЦИЯ",
        "min_pages": 60,
        "max_pages": 120,
        "structure": "Введение · 3–4 главы с подглавами · Заключение · Список литературы · Приложения",
        "desc": "Научно-исследовательская работа магистранта с новизной и практической значимостью",
    },
    "monograph": {
        "name": "📕 Монография",
        "word": "МОНОГРАФИЯ",
        "min_pages": 40,
        "max_pages": 100,
        "structure": "Введение · 3 главы с подглавами · Заключение · Библиографический список",
        "desc": "Развёрнутое научное исследование одной темы с глубоким анализом литературы",
    },
    "business_plan": {
        "name": "💼 Бизнес-план",
        "word": "БИЗНЕС-ПЛАН",
        "min_pages": 15,
        "max_pages": 40,
        "structure": "Резюме · Анализ рынка · Маркетинг · Производственный план · Финансовый план · Риски",
        "desc": "Технико-экономическое обоснование проекта с анализом рынка и финансовыми расчётами",
    },
    "lab": {
        "name": "🔬 Лабораторная работа",
        "word": "ЛАБОРАТОРНАЯ РАБОТА",
        "min_pages": 5,
        "max_pages": 20,
        "structure": "Цель · Теоретическая часть · Ход работы · Результаты · Выводы",
        "desc": "Описание лабораторного эксперимента с методикой, результатами и выводами",
    },
    "custom": {
        "name": "🧩 Свой тип",
        "word": "РАБОТА",
        "min_pages": 3,
        "max_pages": 100,
        "structure": "Пользовательская структура",
        "desc": "Любой тип документа с вашими требованиями к структуре",
    },
}

# Разделение видов работ: основные (сразу) и дополнительные (по кнопке «Ещё»).
# Тип "custom" (свой тип) исключён из интерфейса по требованию.
MAIN_DOC_TYPES = ["referat", "doklad", "esse", "kursovaya", "kontrolnaya"]
MORE_DOC_TYPES = ["course_project", "master", "report_practice", "lab",
                  "monograph", "business_plan", "final_project", "final_referat",
                  "article", "vkr"]

INSTITUTION_TYPES = {
    "school": {
        "name": "🏫 Школа",
        "org_example": "Муниципальное бюджетное общеобразовательное учреждение",
        "name_example": "Средняя общеобразовательная школа № 123",
    },
    "college": {
        "name": "🏛 Колледж / СПО",
        "org_example": "Государственное бюджетное профессиональное образовательное учреждение",
        "name_example": "Колледж информационных технологий № 42",
    },
    "university": {
        "name": "🎓 ВУЗ / Университет",
        "org_example": "Федеральное государственное бюджетное образовательное учреждение высшего образования",
        "name_example": "Московский государственный университет имени М.В. Ломоно��ова",
    },
    "custom": {
        "name": "✏️ Свой вариант",
        "org_example": "",
        "name_example": "",
    },
}

SUBJECTS = [
    "История", "Обществознание", "Литература", "Русский язык",
    "Математика", "Физика", "Информатика", "Биология",
    "Химия", "География", "Экономика", "Право",
    "Философия", "Психология", "Социология", "Менеджмент",
    "Педагогика", "Медицина", "Архитектура", "Юриспруденция",
]

# Примеры «хорошо / плохо» для эссе по дисциплинам
DISCIPLINE_EXAMPLES = {
    "Информатика": (
        "ПЛОХО (не по дисциплине): «Байкал — это чистейшее озеро с удивительной экосистемой».\n"
        "ХОРОШО (по дисциплине): «Байкал можно рассматривать как природную систему сбора данных: "
        "тысячелетиями в донных отложениях накапливается информация о климате, которую мы извлекаем "
        "методами машинного обучения. Это аналог долговременной памяти в вычислительных системах.»"
    ),
    "Психология": (
        "ПЛОХО: «Байкал красив и вызывает благоговение».\n"
        "ХОРОШО: «Восприятие Байкала вызывает эффект благоговения (awe), который, по исследованиям "
        "Келтнера и Хаидта, снижает активность дефолт-системы мозга и усиливает ощущение связи с миро��.»"
    ),
    "История": (
        "ПЛОХО: «Байкал — уникальное природное явление».\n"
        "ХОРОШО: «С XVIII века Байкал становится объектом научного интереса: экспедиции Миллера (1733–1743) "
        "и Палласа заложили основу систематического изучения региона, что можно рассматривать как начало "
        "формирования региональной историографии.»"
    ),
}

CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург",
    "Казань", "Нижний Новгород", "Красноярск", "Челябинск",
    "Омск", "Ростов-на-Дону", "Уфа", "Волгоград",
]


# ═══════════════════════════════════════════════════════════════
#  БИБЛИОТЕКА ЭТАЛОНОВ И ЖЕСТКИХ ПРАВИЛ (Few-Shot & Anti-Patterns)
# ══════════════════════════════════════════════════════════════════

# 1. Эталоны структуры и стиля (GOLDEN STANDARDS)
FEW_SHOT_EXAMPLES = """
❗ КРИТИЧЕСКИЕ ПРИМЕРЫ: НАРУШЕНИЕ ФОРМАТА = БРАК.

ССЫЛКИ:
   ❌ НЕПРАВИЛЬНО: «Исследование показало рост [2].» — нет страницы.
   ❌ НЕПРАВИЛЬНО: «Исследование показало рост [2, с. » — ссылка оборвана.
   ✅ ПРАВИЛЬНО: «Доля выборки выросла с 41 до 58% [2, с. 45].»
   ✅ ПРАВИЛЬНО: «Авторы выделяют три группы факторов [3, с. 120–125].»

ВВЕДЕНИЕ:
   ❌ НЕПРАВИЛЬНО: «Эта тема очень важна в современном мире, потому что требует решения.»
   ✅ ПРАВИЛЬНО: «С 2019 по 2024 год доля удаленных вакансий выросла с 7 до 19% [1, с. 12], поэтому требуется оценить влияние этого сдвига на рынок труда.»

ЗАКЛЮЧЕНИЕ:
   ❌ НЕПРАВИЛЬНО: «Таким образом, проведенный анализ показывает, что проведенный анализ подтверждает важность темы.»
   ✅ ПРАВИЛЬНО: «Сопоставление данных показало, что фактор обучения сильнее влияет на результат, чем возраст работников [2, с. 91].»

СТИЛЬ И ОБЪЁМ:
   ❌ ЗАПРЕЩЕНО: служебные фразы («Вот ваш текст», «Объем соблюден»), повторы и вода.
   ✅ ТРЕБУЕТСЯ: конкретные факты из источников, завершенные предложения и заданный диапазон знаков.
"""

# Черный список авторов для литературы (чтобы не было Кнута в реферате по Байкалу)
LIT_BLACKLIST = "Дональд Кнут, Томас Кормен, Эндрю Таненбаум, Стивен Лавренс"


DEFAULT_GOST_CONFIGS: dict = {
    "_base": {
        "font_name":              "Times New Roman",
        "font_size":              14,
        "line_spacing":           1.5,
        "first_line_indent_cm":   1.25,
        "left_margin_mm":         30,
        "right_margin_mm":        10,
        "top_margin_mm":          20,
        "bottom_margin_mm":       20,
        "alignment":              "justify",
        "page_number_position":   "bottom_center",
    }
}

for _t in DOC_TYPES.keys():
    DEFAULT_GOST_CONFIGS.setdefault(_t, dict(DEFAULT_GOST_CONFIGS["_base"]))

if not isinstance(GOST_CONFIGS, dict):
    GOST_CONFIGS = {}

for _t in DOC_TYPES.keys():
    GOST_CONFIGS.setdefault(_t, {})
    for _k, _v in DEFAULT_GOST_CONFIGS[_t].items():
        GOST_CONFIGS[_t].setdefault(_k, _v)

_save_json(GOST_CONFIG_FILE, GOST_CONFIGS)


def get_gost_config(doc_type: str, user_id: int | None = None) -> dict:
    doc_type = doc_type if doc_type in DOC_TYPES else "referat"
    config = dict(GOST_CONFIGS.get(doc_type, DEFAULT_GOST_CONFIGS[doc_type]))
    if user_id:
        user_file = f"user_gost_{user_id}.json"
        u = _load_json(user_file, {})
        if isinstance(u, dict) and doc_type in u and isinstance(u[doc_type], dict):
            config.update(u[doc_type])
    return config


def save_user_gost_config(user_id: int, doc_type: str, config: dict) -> None:
    user_file = f"user_gost_{user_id}.json"
    u = _load_json(user_file, {})
    if not isinstance(u, dict):
        u = {}
    u[doc_type] = config
    _save_json(user_file, u)


# ═══════════════════════════════════════════════════════════════
#  ЛИМИТЫ / VIP
# ═════════════════════════���═════════════════════════════════════

def is_vip(user_id: int) -> bool:
    return int(user_id) in VIP_USERS


# ═══════════════════════════════════════════════════════════════
# СИСТЕМНЫЙ ПРОМПТ ДЛЯ АКАДЕМИЧЕСКОГО РЕФЕРИРОВАНИЯ (ГОСТ 7.32-2017)
# ═══════════════════════════════════════════════════════════════

ACADEMIC_REFERENCING_SYSTEM_PROMPT = """ТЫ — ЭКСПЕРТ ПО АКАДЕМИЧЕСКОМУ РЕФЕРИРОВАНИЮ И ГОСТ 7.32-2017.

Перед тобой текст реферата. Твоя задача — ПРИВЕСТИ ЕГО К СТРОГОМУ СТАНДАРТУ.

ВЫПОЛНИ СТРОГО ПОСЛЕДОВАТЕЛЬ���О:

ЭТАП 1. ОГЛАВЛЕНИЕ — Проставь номера страниц для всех разделов
ЭТАП 2. ВВЕДЕНИЕ — 4 абзаца: Актуальность, Степень разработанности, Цель и задачи, Структура
ЭТАП 3. ОСНОВНАЯ ЧАСТЬ — каждая подглава: 3 абзаца (вводный, основной с ссылками, выводящий)
ЭТАП 4. ЗАКЛЮЧЕНИЕ — 4 связных абзаца: итог по содержанию первой главы, итог по второй главе, общий вывод и перспективы. ПИШИ СВЯЗНЫМ ТЕКСТОМ, БЕЗ ярлыков-заголовков вида «Вывод по первой главе:», «Общий итог:».
ЭТАП 5. СПИСОК ЛИТЕРАТУРЫ — минимум 10 источников в формате ГОСТ 7.32-2017
ЭТАП 6. СТИЛИСТИКА — удали фразы-маркеры ИИ
ЭТАП 7. ПРОВЕРКА — все ссылки с номерами страниц [N, с. X]

НЕ ДОБАВЛЯЙ ПОЯСНЕНИЙ — ТОЛЬКО ГОТОВЫЙ ИСПРАВЛЕННЫЙ РЕФЕРАТ."""

# ═══════════════════════════════════════════════════════════════
# ФУНКЦИИ ЗАЩИТЫ ОТ ПЕРЕГРУЗКИ API
# ═══════════════════════════════════════════════════════════════

# Глобальный счётчик ошибок для определения перегрузки
_api_overload_counter = {}
_api_overload_timestamps = {}

def get_overload_warning_message() -> str:
    """Возвращает предупреждающее сообщение о высокой нагрузке на API."""
    return (
        "⚠️ <b>Внимание! Высокая нагрузка на сервис</b>\n\n"
        "🔄 Сейчас пользуются много пользователей, большая нагрузка на API.\n"
        "⏱ Попробуйте повторить запрос через несколько минут.\n\n"
        "💡 <b>Советы:</b>\n"
        "• Используйте платный режим — он без лимитов\n"
        "• Выберите другую ИИ-модель\n"
        "• Попробуйте позже, когда нагрузка снизится"
    )

def increment_overload(model_key: str) -> None:
    """Увеличивает счётчик перегрузок для модели."""
    import time as _time_mod
    current_time = _time_mod.time()
    if model_key in _api_overload_timestamps:
        if current_time - _api_overload_timestamps[model_key] > 300:
            _api_overload_counter[model_key] = 0
    _api_overload_counter[model_key] = _api_overload_counter.get(model_key, 0) + 1
    _api_overload_timestamps[model_key] = current_time

def is_api_overloaded(model_key: str) -> bool:
    """Проверяет, перегружена ли модель (более 3 ошибок за 5 минут)."""
    return _api_overload_counter.get(model_key, 0) >= 3

def reset_overload(model_key: str) -> None:
    """Сбрасывает счётчик перегрузок при успешном запросе."""
    _api_overload_counter[model_key] = 0




def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fmt_seconds(seconds: int) -> str:
    m, s = divmod(max(0, int(seconds)), 60)
    if m:
        return f"{m} мин {s:02d} сек"
    return f"{s} сек"


def load_usage() -> dict:
    return _load_json(USAGE_FILE, {})


def save_usage(d: dict) -> None:
    _save_json(USAGE_FILE, d)


def _fmt_wait_human(seconds: int) -> str:
    """Удобная человекочитаемая длительность: дни/часы/минуты/секунды."""
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d} дн")
    if h: parts.append(f"{h} ч")
    if m and not d: parts.append(f"{m} мин")
    if not parts: parts.append(f"{s} сек")
    return " ".join(parts)


def check_user_limit(user_id: int, mode: str) -> tuple[bool, str]:
    """Проверяет ограничения.

    Бесплатный режим: одна генерация раз в FREE_COOLDOWN секунд (по умолчанию 5 суток).
    Платные пользователи (mode='paid') — безлимитны (PAID_DAILY_LIMIT=0, PAID_COOLDOWN=0).
    VIP — без ограничений всегда.
    """
    if is_vip(user_id):
        return True, ""

    is_free = mode == "free"

    # Платный режим — без ограничений если PAID_DAILY_LIMIT == 0
    if not is_free and PAID_DAILY_LIMIT == 0 and PAID_COOLDOWN == 0:
        return True, ""

    data = load_usage()
    uid  = str(user_id)
    rec  = data.get(uid, {}) or {}

    now = int(datetime.now().timestamp())

    # ── Кулдаун (главный фильтр для бесплатных) ──
    cooldown = FREE_COOLDOWN if is_free else PAID_COOLDOWN
    ts_key   = "last_free_ts" if is_free else "last_paid_ts"
    last_ts  = int(rec.get(ts_key, 0) or 0)
    if cooldown > 0 and last_ts and (now - last_ts) < cooldown:
        wait     = cooldown - (now - last_ts)
        next_dt  = datetime.fromtimestamp(last_ts + cooldown).strftime("%d.%m.%Y %H:%M")
        kind     = "бесплатная" if is_free else "платная"
        return False, (
            f"⏳ <b>Следующая {kind} генерация будет доступна позже</b>\n\n"
            f"┌─────────────────────────\n"
            f"│ ⌛ Осталось: <b>{_fmt_wait_human(wait)}</b>\n"
            f"│ 📅 Доступна с: <b>{next_dt}</b>\n"
            f"└─────────────────────────\n\n"
            f"💎 Хотите без ожидания? Используйте п��атный режим — он без лимитов."
        )

    # ── Дневной лимит (опциональный, по умолчанию выключен для бесплатных) ──
    today = today_key()
    if rec.get("date") != today:
        used = 0
    else:
        used = int(rec.get("free" if is_free else "paid", 0) or 0)

    limit = FREE_DAILY_LIMIT if is_free else PAID_DAILY_LIMIT
    if limit > 0 and used >= limit:
        kind = "бесплатных" if is_free else "платных"
        return False, (
            f"🚫 <b>Дневной лимит {kind} генераций исчерпан</b>\n\n"
            f"Использовано: <b>{used}/{limit}</b>\n"
            f"Лимит обновится в полночь 🕛"
        )

    return True, ""


def record_user_generation(user_id: int, mode: str) -> None:
    """Фиксирует факт генерации.

    Хранит:
      - last_free_ts / last_paid_ts — timestamp последней генерации (для кулдауна,
        не сбрасывается полночью);
      - free / paid — счётчик за текущие сутки (используется только если
        включён дневной лимит >0).
    """
    if is_vip(user_id):
        return
    # Перегенерация той же работы не списывает новый лимит/звёзды.
    if mode == "regen":
        return

    data  = load_usage()
    uid   = str(user_id)
    today = today_key()

    rec = data.get(uid, {}) or {}
    # last_*_ts НЕ обнуляем при смене даты — иначе кулдаун в 5 дней не сработает
    if rec.get("date") != today:
        rec["date"] = today
        rec["free"] = 0
        rec["paid"] = 0

    key    = "free" if mode == "free" else "paid"
    ts_key = "last_free_ts" if mode == "free" else "last_paid_ts"
    rec[key]    = int(rec.get(key, 0) or 0) + 1
    rec[ts_key] = int(datetime.now().timestamp())

    data[uid] = rec
    save_usage(data)


def get_user_limits_info(user_id: int) -> str:
    """Возвращает красивую карточку с лимитами пользователя."""
    if is_vip(user_id):
        return (
            "┌─────────────────────────\n"
            "│ 👑 <b>Статус: VIP</b>\n"
            "│ ♾ Генерации — без лимитов\n"
            "└─────────────────────────"
        )

    data = load_usage()
    uid  = str(user_id)
    rec  = data.get(uid, {}) or {}

    now      = int(datetime.now().timestamp())
    last_free = int(rec.get("last_free_ts", 0) or 0)

    if FREE_COOLDOWN > 0 and last_free and (now - last_free) < FREE_COOLDOWN:
        wait_s   = FREE_COOLDOWN - (now - last_free)
        next_dt  = datetime.fromtimestamp(last_free + FREE_COOLDOWN).strftime("%d.%m.%Y %H:%M")
        free_str = (
            f"⏳ Ждать <b>{_fmt_wait_human(wait_s)}</b>\n"
            f"│ 📅 Доступна: <b>{next_dt}</b>"
        )
    else:
        period_h = FREE_COOLDOWN // 3600
        period_s = f"{period_h // 24} дн" if period_h >= 24 else f"{period_h} ч"
        free_str = f"✅ Доступна (1 раз в {period_s})"

    paid_str = (
        "♾ Безлимитно" if PAID_DAILY_LIMIT == 0
        else f"{int(rec.get('paid', 0) or 0)}/{PAID_DAILY_LIMIT}"
    )

    return (
        "┌─────────────────────────\n"
        f"│ 🆓 Бесплатно: {free_str}\n"
        f"│ ⭐ Платные:    <b>{paid_str}</b>\n"
        "└─────────────────────────"
    )


# ═══════════════════════════════════════════════════════════════
#  AI МОДЕЛИ
# ═══════════════════════════════════════════════════════════════

class ModelStatus:
    AVAILABLE = "✅"
    LIMIT     = "❌ лимит"
    UNKNOWN   = "❓"
    FATAL     = "🔴 ошибка"


AI_MODELS: dict = {
    "deepseek": {
        "name":           "🐋 DeepSeek Chat",
        "base_url":       DEEPSEEK_BASE_URL,
        "api_key":        DEEPSEEK_KEY,
        "model":          DEEPSEEK_MODEL,
        "price_per_page": DEEPSEEK_PRICE,
        "status":         ModelStatus.UNKNOWN,
        "_fatal":         False,
    },
    "deepseek_r1": {
        "name":           "🧠 DeepSeek R1 (OpenRouter)",
        "base_url":       OPENROUTER_BASE_URL,
        "api_key":        OPENROUTER_KEY,
        "model":          OPENROUTER_R1_MODEL,
        "price_per_page": DEEPSEEK_PRICE,
        "status":         ModelStatus.UNKNOWN,
        "_fatal":         False,
    },
    "gemini_or": {
        "name":           "🌟 Gemini 2.5 Flash (OpenRouter)",
        "base_url":       OPENROUTER_BASE_URL,
        "api_key":        OPENROUTER_KEY,
        "model":          OPENROUTER_GEMINI_MODEL,
        "price_per_page": OR_GEMINI_PRICE,
        "status":         ModelStatus.UNKNOWN,
        "_fatal":         False,
    },
    "groq": {
        "name":           "⚡ Groq (LLaMA 3.3 70B)",
        "base_url":       GROQ_BASE_URL,
        "api_key":        GROQ_KEY,
        "model":          GROQ_MODEL,
        "price_per_page": GROQ_PRICE,
        "status":         ModelStatus.UNKNOWN,
        "_fatal":         False,
    },
}

if FREE_MODEL_KEY not in AI_MODELS:
    FREE_MODEL_KEY = "deepseek"


def _strip_markdown_markers(text: str) -> str:
    """Убирает markdown-маркеры `#` и `*`, чтобы они не попадали в DOCX."""
    if not text:
        return ""
    # Убираем # в начале строки (заголовки markdown)
    text = re.sub(r'(?m)^\s*#{1,6}\s*\*{0,2}\s*', '', text)
    text = re.sub(r'(?m)^\s*\*{2}([^*]+)\*{2}\s*$', r'\1', text)
    # Убираем **жирный** и *курсив*
    text = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", text)
    # Убираем оставшиеся # (в начале строки и одиночные внутри текста)
    text = re.sub(r'(?m)^\s*#+\s*', '', text)
    text = text.replace("#", "")
    # Убираем висящие ** после удаления # в строках вида `# **Заголовок**`
    text = re.sub(r'(?m)^\s*\*\*', '', text)
    text = re.sub(r'(?m)\*\*\s*$', '', text)
    return text.strip()


_AI_MARKER_REPLACEMENTS = [
    # ── Служебные ответы модели ──
    (r"(?im)^\s*(конечно|разумеется|хорошо)[,.!\s]*(?:вот|ниже)\s+[^\n.?!]*[.?!]?\s*", ""),
    (r"(?im)^\s*(?:вот|ниже)\s+(?:ваш|представлен|привед[её]н)[^\n.?!]*[.?!]?\s*", ""),
    (r"(?i)\bкак (?:искусственный интеллект|ии|языковая модель)[^.!?\n]*[.!?]?\s*", ""),
    (r"(?i)\bя (?:являюсь|не являюсь|не могу|не имею возможности)[^.!?\n]*[.!?]?\s*", ""),
    (r"(?i)\bобъ[её]м текста (?:строго )?(?:выдержан|соблюд[её]н)[^.!?\n]*[.!?]?\s*", ""),

    # ── Типовые ИИ-вступления (удаляются целиком) ──
    (r"(?i)^\s*рассмотрим\s+подробнее[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+обратить\s+внимание[,!.:;]?\s*", ""),
    (r"(?i)^\s*как\s+показывает\s+практика[,!.:;]?\s*", ""),
    (r"(?i)^\s*в\s+контексте\s+рассматриваемой\s+темы[,!.:;]?\s*", ""),
    (r"(?i)^\s*безусловно[,!.:;]?\s*", ""),
    (r"(?i)^\s*важно\s+подчеркнуть[,!.:;]?\s*", ""),
    (r"(?i)^\s*исходя\s+из\s+проведенного\s+анализа[,!.:;]?\s*", ""),
    (r"(?i)^\s*согласно\s+имеющимся\s+данным[,!.:;]?\s*", ""),
    (r"(?i)^\s*как\s+представляется[,!.:;]?\s*", ""),
    (r"(?i)^\s*очевидно[,!.:;]?\s*", ""),
    (r"(?i)^\s*несомненно[,!.:;]?\s*", ""),
    (r"(?i)^\s*стоит\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*хотелось\s+бы\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*прежде\s+всего[,!.:;]?\s*", ""),
    (r"(?i)^\s*в\s+первую\s+очередь[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+указать[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+сказать[,!.:;]?\s*", ""),
    (r"(?i)^\s*не\s+вызывает\s+сомнений[,!.:;]?\s*", ""),
    (r"(?i)^\s*как\s+известно[,!.:;]?\s*", ""),
    (r"(?i)^\s*нельзя\s+не\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+подчеркнуть[,!.:;]?\s*", ""),
    (r"(?i)^\s*заслуживает\s+внимания[,!.:;]?\s*", ""),
    (r"(?i)^\s*интерес\s+представляет[,!.:;]?\s*", ""),
    (r"(?i)^\s*надо\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*нужно\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*важно\s+отметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*обращает\s+на\s+себя\s+внимание[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+признать[,!.:;]?\s*", ""),
    (r"(?i)^\s*несмотря\s+на\s+это[,!.:;]?\s*", ""),
    (r"(?i)^\s*прежде\s+чем[,!.:;]?\s*", ""),
    (r"(?i)^\s*стоит\s+сказать[,!.:;]?\s*", ""),
    (r"(?i)^\s*хочется\s+сказать[,!.:;]?\s*", ""),
    (r"(?i)^\s*следует\s+заметить[,!.:;]?\s*", ""),
    (r"(?i)^\s*стоит\s+заметить[,!.:;]?\s*", ""),

    # ── Фразы-связки внутри предложений ──
    (r"(?i)\s+в\s+контексте\s+рассматриваемой\s+темы\s*[,;.]?\s*", " "),
    (r"(?i)\s+с\s+учетом\s+вышесказанного\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+связи\s+с\s+этим\s*[,;.]?\s*", " "),
    (r"(?i)\s+на\s+основании\s+вышеизложенного\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+силу\s+вышеизложенного\s*[,;.]?\s*", " "),
    (r"(?i)\s+исходя\s+из\s+вышесказанного\s*[,;.]?\s*", " "),
    (r"(?i)\s+учитывая\s+вышесказанное\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+свете\s+вышеизложенного\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+итоге\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+конечном\s+счете\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+конечном\s+итоге\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+результате\s+чего\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+ходе\s+анализа\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+процессе\s+исследования\s*[,;.]?\s*", " "),
    (r"(?i)\s+с\s+точки\s+зрения\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+качестве\s+примера\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+целом\s+можно\s+сказать[,;.]?\s*", " "),
    (r"(?i)\s+в\s+целом\s+же\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+частности\s*[,;.]?\s*", " "),
    (r"(?i)\s+в\s+том\s+числе\s*[,;.]?\s*", " "),

    # ── Запрещённые клише (удаление) ──
    (r"(?i)\bв\s+заключение\s+следует\s+отметить,?\s+что\s+", ""),
    (r"(?i)\bподводя\s+итог,?\s+", ""),
    (r"(?i)\bтаким\s+образом,?\s+", ""),
    (r"(?i)\bследует\s+отметить,?\s+что\s+", ""),
    (r"(?i)\bнеобходимо\s+отметить,?\s+что\s+", ""),
    (r"(?i)\bважно\s+подчеркнуть,?\s+что\s+", ""),
    (r"(?i)\bнельзя\s+не\s+отметить,?\s+что\s+", ""),
    (r"(?i)\bв\s+целом\s+можно\s+сказать,?\s+что\s+", ""),
    (r"(?i)\bможно\s+сделать\s+вывод,?\s+что\s+", ""),
    (r"(?i)\bисходя\s+из\s+вышеизложенного,?\s+", ""),
    (r"(?i)\bследует\s+также\s+отметить,?\s+что\s+", ""),
    (r"(?i)\bстоит\s+подчеркнуть,?\s+что\s+", ""),
    (r"(?i)\bнадо\s+заметить,?\s+что\s+", ""),
    (r"(?i)\bважно\s+заметить,?\s+что\s+", ""),
    (r"(?i)\bнеобходимо\s+заметить,?\s+что\s+", ""),
    (r"(?i)\bстоит\s+заметить,?\s+что\s+", ""),
    (r"(?i)\bследует\s+заметить,?\s+что\s+", ""),
    (r"(?i)\bхочется\s+подчеркнуть,?\s+что\s+", ""),
    (r"(?i)\bхотелось\s+бы\s+подчеркнуть,?\s+что\s+", ""),
    (r"(?i)\bв\s+заключение\s+хочется\s+сказать,?\s+что\s+", ""),
    (r"(?i)\bв\s+заключение\s+можно\s+сказать,?\s+что\s+", ""),

    # ── Замена канцеляризмов ──
    (r"(?i)\bв\s+настоящее\s+время\s+", ""),
    (r"(?i)\bв\s+различных\s+областях\s+", ""),
    (r"(?i)\bширокий\s+спектр\s+", ""),
    (r"(?i)\bширокий\s+диапазон\s+", ""),
    (r"(?i)\bкомплексный\s+подход\b", "подход"),
    (r"(?i)\bкомплексный\s+анализ\b", "анализ"),
    (r"(?i)\bкомплексная\s+оценка\b", "оценка"),
    (r"(?i)\bкомплексное\s+исследование\b", "исследование"),
    (r"(?i)\bкомплексное\s+изучение\b", "изучение"),
    (r"(?i)\bактуальность\s+исследования\s+обусловлена\b", "Исследование актуально"),
    (r"(?i)\bнаучная\s+новизна\s+исследования\s+заключается\s+в\b", "Новизна работы состоит в"),
    (r"(?i)\bстепень\s+научной\s+разработанности\s+проблемы\b", "научная разработанность темы"),
    (r"(?i)\bстепень\s+разработанности\s+проблемы\b", "научная разработанность темы"),
    (r"(?i)\bпредставляется\s+возможным\b", "можно"),
    (r"(?i)\bпредставляет\s+собой\b", "является"),
    (r"(?i)\bиграет\s+важную\s+роль\b", "важна"),
    (r"(?i)\bимеет\s+важное\s+значение\b", "важна"),
    (r"(?i)\bзанимает\s+особое\s+место\b", "важна"),
    (r"(?i)\bв\s+ходе\s+проведённого\s+исследования\b", "в ходе исследования"),
    (r"(?i)\bв\s+ходе\s+проведенного\s+исследования\b", "в ходе исследования"),
    (r"(?i)\bв\s+процессе\s+исследования\b", "в процессе работы"),
    (r"(?i)\bв\s+рамках\s+настоящего\s+исследования\b", "в этой работе"),
    (r"(?i)\bв\s+рамках\s+настоящей\s+работы\b", "в этой работе"),
    (r"(?i)\bцель\s+работы\s+заключается\s+в\s+том,?\s+чтобы\b", "цель работы —"),
    (r"(?i)\bобъектом\s+исследования\s+выступает\b", "объект исследования —"),
    (r"(?i)\bобъектом\s+исследования\s+является\b", "объект исследования —"),
    (r"(?i)\bпредметом\s+исследования\s+выступает\b", "предмет исследования —"),
    (r"(?i)\bпредметом\s+исследования\s+является\b", "предмет исследования —"),
    (r"(?i)\bследует\s+отметить\s+тот\s+факт,?\s+что\b", ""),
    (r"(?i)\bотмечается\s+тот\s+факт,?\s+что\b", ""),
    (r"(?i)\bважно\s+отметить\s+тот\s+факт,?\s+что\b", ""),
    (r"(?i)\bнеобходимо\s+учитывать\s+тот\s+факт,?\s+что\b", ""),
    (r"(?i)\bисходя\s+из\s+вышесказанного\s+можно\s+сделать\s+вывод,?\s+что\b", ""),
]


def _remove_ai_marker_phrases(text: str) -> str:
    """Удаляет служебные и шаблонные фразы-маркеры ИИ без добавления опечаток."""
    if not text:
        return ""
    out = text
    for pattern, repl in _AI_MARKER_REPLACEMENTS:
        out = re.sub(pattern, repl, out)
    # Чистим пробелы, которые могли остаться после удаления вводных фраз.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"(?m)^\s+", "", out)
    return out.strip()


def _remove_duplicate_phrases(text: str) -> str:
    """Срезает повторяющиеся фразы / тавтологии в одном предложении.

    Примеры, которые ловит:
      «Проведённый анализ показывает, что проведённ����й анализ подтверждает, что …»
      → «Проведённый анализ показывает, что подтверждается, что …»
      «итак, итак, мы видим …» → «итак, мы видим …»
      «таким образом, таким образом, ясно …» → «таким образом, ясно …»

    Стратегия:
      1. Срезаем подряд идущие n-граммы (3–6 сл��в), повторяющиеся в одной фразе.
      2. Срезаем дублирующиеся вводные обороты подряд («итак, итак,», «таким
         образом, таким образом»).
    """
    if not text:
        return text

    # 1) Подряд идущие n-граммы длины 3..6 — типичный паттерн LLM-тавтологии.
    #    «AAA BBB» где AAA = BBB (с точностью до пробелов и регистра).
    for n in (6, 5, 4, 3):
        pattern = re.compile(
            r"(\b(?:[А-Яа-яЁё]+(?:\s+|,\s+|\s+что\s+)){" + str(n) + r"})\1",
            flags=re.IGNORECASE,
        )
        prev = None
        while prev != text:
            prev = text
            text = pattern.sub(r"\1", text)

    # 2) Подряд идущие вводные обороты — «итак, итак,», «таким образом, таким образом».
    intros = [
        "итак", "таким образом", "следовательно", "кроме того",
        "помимо этого", "более того", "в заключение",
    ]
    for intro in intros:
        # «итак, итак,» / «таким образом, таким образом,» → одно вхождение
        pat = re.compile(rf"\b({re.escape(intro)})\s*,?\s+\1\b\s*,?", flags=re.IGNORECASE)
        text = pat.sub(r"\1,", text)

    # 3) Специальный кейс: «проведённый анализ … проведённый анализ» близко друг к другу.
    text = re.sub(
        r"(\bпроведён(?:н)?ый\s+анализ\b[^.]*?)(\s+(?:что|и|который|где)\s+)?\bпроведён(?:н)?ый\s+анализ\b",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    # 4) Чистим возникшие двойные пробелы и запятые.
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",([А-Яа-яЁёA-Za-z])", r", \1", text)  # пробел после запятой
    text = re.sub(r"\s{2,}", " ", text)
    return text


def _sort_bibliography_by_gost(lines: list[str]) -> list[str]:
    """Сортирует список литературы по ГОСТ 7.32-2017.

    Правила:
    1. Сначала кириллица (А-Я), потом латиница (A-Z)
    2. Внутри каждой группы — по алфавиту (по первой букве)
    """
    if not lines:
        return lines

    def _is_cyrillic(char: str) -> bool:
        return "\u0400" <= char <= "\u04FF"

    def _sort_key(line: str) -> tuple[int, str]:
        clean = re.sub(r"^\d+\.\s*", "", line)
        first_char = clean[0] if clean else " "

        if _is_cyrillic(first_char):
            return (0, line.lower())
        elif first_char.isalpha():
            return (1, line.lower())
        else:
            return (2, line.lower())

    return sorted(lines, key=_sort_key)


def _match_citations_to_sources(
    text: str,
    literature: str,
    min_confidence: float = 0.15,
) -> str:
    """Привязывает ссылки к источникам по ключевым словам.

    Анализирует каждый абзац, извлекает ключевые слова, ищет подходящий
    источник в списке литературы по этим словам.
    """
    if not text or not literature:
        return text

    sources = []
    for line in literature.split("\n"):
        if re.match(r"^\d+\.\s+", line.strip()):
            body = re.sub(r"^\d+\.\s*", "", line.strip())
            sources.append(body)

    if not sources:
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    for para_idx, para in enumerate(paragraphs):
        if re.search(r"\[\d+\s*,\s*[сСcC]\.\s*\d+\]", para):
            continue

        stopwords = {
            "этот", "этого", "этом", "этой", "эти", "этих", "этим",
            "также", "более", "менее", "самый", "очень", "может", "могут",
            "который", "которая", "которые", "которых", "если", "чтобы",
            "однако", "поэтому", "между", "через", "после", "перед",
            "всех", "все", "всё", "всей", "всём", "всеми",
            "является", "являются", "представляет", "представляют",
        }
        words = re.findall(r"[а-яёa-z]{4,}", para.lower())
        keywords = [w for w in words if w not in stopwords and len(w) >= 4]

        if not keywords:
            continue

        scores = []
        for idx, source in enumerate(sources, start=1):
            source_lower = source.lower()
            score = sum(1 for w in keywords if w in source_lower)
            if score > 0:
                scores.append((idx, score, source))

        if not scores:
            continue

        scores.sort(key=lambda x: x[1], reverse=True)
        best_idx, best_score, best_source = scores[0]

        if best_score >= 2:
            if not re.search(r"\[\d+\s*,\s*[сСcC]\.\s*\d+\]", para):
                page = 10 + (best_idx * 7) % 50
                if para.endswith("."):
                    para = para[:-1] + f" [{best_idx}, с. {page}]."
                else:
                    para = para + f" [{best_idx}, с. {page}]."
                paragraphs[para_idx] = para

    return "\n\n".join(paragraphs)


def _final_citation_check(parts: dict) -> dict:
    """Проверяет, что все ссылки имеют формат [N, с. X]."""
    for key, text in parts.items():
        if key == "literature":
            continue
        bare_citations = re.findall(r"\[\s*(\d+)\s*\]", text)
        if bare_citations:
            page_map = {}
            for n in bare_citations:
                if n not in page_map:
                    m = re.search(rf"\[\s*{n}\s*,\s*[сСcC]\.\s*(\d+)\]", text)
                    if m:
                        page_map[n] = m.group(1)

            def _fix_bare(m: re.Match) -> str:
                n = m.group(1)
                if n in page_map:
                    return f"[{n}, с. {page_map[n]}]"
                return f"[{n}]"

            parts[key] = re.sub(r"\[\s*(\d+)\s*\]", _fix_bare, text)

    return parts


async def deep_paraphrase(text: str, topic: str, model_key: str = FREE_MODEL_KEY) -> str:
    """Глубокое перефразирование с сохранением смысла."""
    if not text or len(text) < 100:
        return text

    prompt = f"""
    Перефразируй следующий текст ГЛУБОКО, сохраняя смысл:

    ИСХОДНЫЙ ТЕКСТ:
    {text}

    ТЕМА: {topic}

    ТРЕБОВАНИЯ:
    1. Полностью перестрой структуру предложений
    2. Используй другие грамматические конструкции
    3. Заменяй пассивный залог на активный
    4. Меняй порядок изложения
    5. Сохраняй все факты и даты
    6. Не теряй смысл

    Верни только перефразированный текст.
    """
    messages = [
        {"role": "system", "content": "Ты профессиональный редактор. Делай глубокий рерайт."},
        {"role": "user", "content": prompt}
    ]
    result, _ = await chat_with_fallback(model_key, messages, 4096)
    return result if result else text


def _validate_user_literature(content: str) -> tuple[bool, str]:
    """Проверяет пользовательский список литературы."""
    lines = [l.strip() for l in content.split("\n") if l.strip()]

    if len(lines) < 3:
        return False, "❌ Слишком мало источников. Введите минимум 3."

    has_content = any(re.search(r"[А-Яа-яёA-Za-z]{4,}", l) for l in lines)
    if not has_content:
        return False, "❌ Список похож на мусор. Введите реальные источники."

    return True, ""


def sanitize_llm_text(raw: str) -> str:
    """Чистит мусор от LLM: markdown-разметку, тройные переводы строк."""
    if not raw:
        return ""
    text = _strip_markdown_markers(raw.strip())
    # убираем ```код``` блоки
    text = re.sub(r"```[^\n]*\n?", "", text)
    # убираем **жирный** и *курсив* markdown
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    # убираем # заголовки markdown
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # убираем тройные+ переводы строк
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Лёгкая «расклейка» слипшихся слов на стыке регистров/алфавитов/цифр
    # (безопасные правила, без разрыва по точкам — чтобы не ломать «т.е.» и инициалы).
    text = re.sub(r'([а-яё])([А-ЯЁ])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])([А-ЯЁ])', r'\1 \2', text)
    text = re.sub(r'([а-яё])([A-Z])', r'\1 \2', text)
    text = re.sub(r'(\d)([А-ЯЁа-яё])', r'\1 \2', text)
    text = re.sub(r'([А-ЯЁа-яё])(\d)', r'\1 \2', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = _remove_ai_marker_phrases(text)
    # (user-patch): убираем повторы фраз и тавтологию вида
    #   «проведённый анализ показывает, что проведённый анализ …»
    text = _remove_duplicate_phrases(text)
    return text.strip()


async def call_openai_compat(
    info: dict,
    messages: list[dict],
    max_tokens: int = 4096,
    timeout: int = 300,
) -> str:
    """Вызов OpenAI-совместимого API."""
    if info.get("_fatal") or not info.get("api_key"):
        return ""

    base    = info["base_url"].rstrip("/")
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {info['api_key']}",
    }

    if "openrouter.ai" in base:
        headers["HTTP-Referer"] = "https://t.me/gost_assistant_bot"
        headers["X-Title"]      = "GOST Assistant Bot"

    payload = {
        "model":       info["model"],
        "messages":    messages,
        "temperature": 0.7,
        "max_tokens":  max_tokens,
    }

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                txt = await r.text()
                if r.status == 200:
                    data = json.loads(txt)
                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                    # АНТИ-ОБРЫВ: если модель упёрлась в лимит токенов
                    # (finish_reason == "length"), текст оборван на полуслове.
                    # Делаем до 2 «дописываний», передавая хвост как контекст,
                    # и склеиваем — чтобы пользователь не получал обрезанную мысль.
                    finish = choice.get("finish_reason") or ""
                    cont_rounds = 0
                    while finish == "length" and cont_rounds < 2 and content:
                        cont_rounds += 1
                        tail = content[-1200:]
                        cont_msgs = messages + [
                            {"role": "assistant", "content": content},
                            {"role": "user", "content":
                                "Продолжи ровно с того места, где остановился, "
                                "не повторяя уже написанное и не начиная заново. "
                                "Заверши мысль и доведи текст до логичного конца. "
                                f"Последний фрагмент: …{tail}"},
                        ]
                        cont_payload = dict(payload, messages=cont_msgs)
                        async with sess.post(
                            f"{base}/chat/completions", headers=headers,
                            json=cont_payload,
                            timeout=aiohttp.ClientTimeout(total=timeout),
                        ) as r2:
                            if r2.status != 200:
                                break
                            d2 = json.loads(await r2.text())
                            c2 = d2["choices"][0]
                            add = c2["message"]["content"] or ""
                            if not add.strip():
                                break
                            sep = "" if content.endswith((" ", "\n")) else " "
                            content += sep + add.lstrip()
                            finish = c2.get("finish_reason") or ""
                    return content
                if r.status in (401, 402, 403):
                    info["_fatal"] = True
                    info["status"] = ModelStatus.FATAL
                    print(f"[FATAL] {info['name']} — auth error {r.status}")
                elif r.status == 429:
                    info["status"] = ModelStatus.LIMIT
                    # Отслеживание перегрузки API
                    model_key = next((k for k, v in AI_MODELS.items() if v.get("base_url") == info.get("base_url") and v.get("model") == info.get("model")), "unknown")
                    increment_overload(model_key)
                    print(f"[LIMIT] {info['name']} — rate limit (API overload detected)")
                elif r.status == 503:
                    info["status"] = ModelStatus.LIMIT
                    model_key = next((k for k, v in AI_MODELS.items() if v.get("base_url") == info.get("base_url") and v.get("model") == info.get("model")), "unknown")
                    increment_overload(model_key)
                    print(f"[OVERLOAD] {info['name']} — service unavailable")
                else:
                    print(f"[ERROR] {info['name']} — HTTP {r.status}: {txt[:200]}")
    except asyncio.TimeoutError:
        print(f"[TIMEOUT] {info['name']}")
    except Exception as e:
        print(f"[ERR] {info['name']}: {e}")

    return ""


async def chat_with_model(info: dict, messages: list[dict], max_tokens: int = 4096) -> str:
    """Вызывает модель. При перегрузке возвращает пустую строку."""
    # Определяем ключ модели для проверки перегрузки
    model_key = next((k for k, v in AI_MODELS.items() if v.get("base_url") == info.get("base_url") and v.get("model") == info.get("model")), "unknown")

    # Проверяем, не перегружена ли модел��
    if is_api_overloaded(model_key):
        print(f"[OVERLOAD] Модель {info.get('name', model_key)} перегружена, пропускаем...")
        info["status"] = ModelStatus.LIMIT
        return ""

    raw = await call_openai_compat(info, messages, max_tokens=max_tokens)

    # Если вернулась пустая строка и статус LIMIT — возможно перегрузка
    if not raw and info.get("status") == ModelStatus.LIMIT:
        increment_overload(model_key)

    # Успешный ответ — сбрасываем счётчик перегрузок
    if raw and len(raw.strip()) > 100:
        reset_overload(model_key)

    return sanitize_llm_text(raw)


def fallback_chain(primary: str) -> list[str]:
    """Цепочка фоллбэков: primary → все остальные доступные модели по приоритету.

    Раньше использовался только OpenRouter (deepseek_r1, gemini_or), из-за
    чего при сбое OpenRouter Groq и прямой DeepSeek API не подхватывались.
    Теперь честно перебираем все модели, у которых есть api_key и нет
    фатальной ошибки. Дубликаты не добавляются.
    """
    # Полный приоритет: сначала primary, затем — в порядке предпочтения.
    priority = [
        primary,
        "deepseek",      # прямой DeepSeek API (дешёвый, стабильный)
        "deepseek_r1",   # OpenRouter / DeepSeek R1
        "gemini_or",     # OpenRouter / Gemini
        "groq",          # Groq (быстрый, бесплатный)
    ]
    out: list[str] = []
    for k in priority:
        if not k or k in out:
            continue
        info = AI_MODELS.get(k)
        if not info:
            continue
        if info.get("_fatal"):
            continue
        if not info.get("api_key"):
            continue
        out.append(k)
    return out


async def regenerate_with_context(
    model_key: str,
    original_text: str,
    rag_text: str,
    topic: str,
) -> str:
    """Перегенерирует фрагмент текста с учётом RAG-контекста."""
    if not rag_text:
        return original_text

    prompt = (
        f"Тема работы: {topic}\n\n"
        f"Дополнительный научный контекст из источников:\n{rag_text}\n\n"
        f"Исходный текст:\n{original_text}\n\n"
        f"Инструкция: Дополни и улучши исходный текст, строго опираясь на научный контекст из источников. "
        f"Сохраняй академический стиль и логическую структуру."
    )
    messages = [
        {"role": "system", "content": "Ты академический эксперт. Пиши строго по предоставленным научным источникам."},
        {"role": "user", "content": prompt},
    ]

    try:
        new_text, _ = await chat_with_fallback(model_key, messages, max_tokens=2048)
        if new_text and len(new_text.strip()) > len(original_text.strip()) * 0.5:
            return new_text
    except Exception as e:
        print(f"[RAG REGEN] Ошибка перегенерации с контекстом: {e}")

    return original_text


async def enhance_with_rag_and_facts(parts: dict[str, str], topic: str, literature: str, model_key: str) -> dict[str, str]:
    """Улучшает текст через RAG, проверку фактов, вариатор стиля и проверку уникальности."""

    # 1. RAG-контекст
    try:
        from rag_engine import build_rag_context, format_rag_context

        async with aiohttp.ClientSession() as session:
            context = await build_rag_context(model_key, topic, literature, session)

            # Добавляем контекст в промпты для перегенерации слабых мест
            for section, chunks in context.items():
                if section in parts and len(parts[section]) < 1000:
                    rag_text = format_rag_context(chunks)
                    parts[section] = await regenerate_with_context(
                        model_key, parts[section], rag_text, topic
                    )
    except Exception as e:
        print(f"[RAG] Ошибка работы RAG-модуля: {e}")

    # 2. Проверка фактов
    try:
        from fact_checker import validate_facts_in_text, auto_cite_facts

        for key, text in parts.items():
            if key == "literature":
                continue
            facts = await validate_facts_in_text(text, topic)
            if facts:
                print(f"[FACTS] Найдено {len(facts)} фактов без ссылок в {key}")
                # Добавляем ссылки автоматически
                parts[key] = await auto_cite_facts(model_key, text, literature)
    except Exception as e:
        print(f"[FACTS] Ошибка проверки фактов: {e}")

    # 3. Вариатор стиля ("человечность")
    try:
        from style_variator import mix_styles

        for key, text in parts.items():
            if key == "literature" or not text:
                continue
            parts[key] = mix_styles(text)
    except Exception as e:
        print(f"[STYLE] Ошибка вариации стиля: {e}")

    # 4. Проверка уникальности
    try:
        from plagiarism_checker import PlagiarismChecker

        checker = PlagiarismChecker()
        for key, text in parts.items():
            if key == "literature" or not text:
                continue
            text, score = await checker.ensure_uniqueness(text, target=70.0)
            parts[key] = text
            print(f"[PLAGIARISM] Раздел {key}: уникальность {score:.1f}%")
    except Exception as e:
        print(f"[PLAGIARISM] Ошибка проверки уникальности: {e}")

    return parts


async def chat_with_fallback(
    primary: str,
    messages: list[dict],
    max_tokens: int,
    max_retries: int = 3,
) -> tuple[str, str]:
    """Пробует модели по цепочке с экспоненциальным повтором, возвращает (текст, ключ_модели)."""
    _t0 = time.monotonic()
    best_text = ""
    best_model = primary
    for attempt in range(max_retries):
        for k in fallback_chain(primary):
            info = AI_MODELS[k]
            try:
                text = await chat_with_model(info, messages, max_tokens=max_tokens)
            except Exception as _api_e:
                log_error(
                    stage="api_call",
                    message=f"Ошибка модели {info.get('name', k)} (попытка {attempt+1}): {_api_e}",
                    model=k,
                    exc_info=_api_e,
                )
                info["status"] = ModelStatus.LIMIT
                print(f"[FALLBACK] Модель {info.get('name', k)} упала с ошибкой: {_api_e}")
                text = ""
            if text and len(text.strip()) > 100:
                info["status"] = ModelStatus.AVAILABLE
                log_error(
                    stage="api_success",
                    message=f"Модель {info.get('name', k)} ответила успешно",
                    model=k,
                )
                return text, k
            if text and len(text.strip()) > len(best_text.strip()):
                best_text = text
                best_model = k
            if not text:
                info["status"] = ModelStatus.LIMIT
                log_error(
                    stage="api_fallback",
                    message=f"Модель {info.get('name', k)} вернула пустой ответ, пробую следующую...",
                    model=k,
                )
        if best_text and len(best_text.strip()) > 100:
            return best_text, best_model
        await asyncio.sleep(2 ** attempt)

    if best_text:
        print(f"[FALLBACK] Все модели дали < 100 зн., лучший: {len(best_text)} зн. от {best_model}")
    try:
        _prov = (AI_MODELS.get(best_model, {}) or {}).get("provider")
        _success = bool(best_text and best_text.strip())
        get_metrics().log(GenerationMetrics(
            event="llm_call",
            model_key=best_model,
            provider=_prov,
            success=_success,
            duration_seconds=round(time.monotonic() - _t0, 3),
            note="fallback-chain",
        ))
        log_api_call(
            model=best_model,
            success=_success,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            stage="chat_with_fallback",
            error_msg="" if _success else (best_text or "пустой ответ")[:300],
        )
    except Exception:
        pass
    return best_text, best_model
