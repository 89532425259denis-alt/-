# -*- coding: utf-8 -*-
"""quality_gate_ultimate — финальный контроль качества с автоправками."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable


class UltimateQualityGate:
    """Финальная проверка с автоматическими правками."""

    def __init__(self):
        self.issues = []
        self.fixes = []

    async def check_and_fix(
        self,
        parts: dict[str, str],
        topic: str,
        subject: str,
        model_key: str,
        chat_fn: Callable[..., Awaitable[tuple[str, str]]],
        max_attempts: int = 3,
    ) -> tuple[dict[str, str], list[str]]:
        """Проверяет и исправляет ВСЕ проблемы."""

        for attempt in range(max_attempts):
            self.issues = []

            # Проверяем каждый раздел
            for key, text in parts.items():
                if key == "literature":
                    continue
                self._analyze_section(key, text, topic, subject)

            if not self.issues:
                print(f"[QUALITY] ✅ Все проверки пройдены (попытка {attempt+1})")
                return parts, self.fixes

            print(f"[QUALITY] ⚠️ Найдено {len(self.issues)} проблем (попытка {attempt+1})")

            # Исправляем проблемы
            for issue in self.issues[:5]:  # максимум 5 правок за раз
                await self._fix_issue(parts, issue, model_key, chat_fn)

        return parts, self.fixes

    def _analyze_section(self, key: str, text: str, topic: str, subject: str):
        """Анализирует один раздел."""

        # 1. Объём
        words = len(text.split())
        if words < 50 and key not in ("literature",):
            self.issues.append({
                "section": key,
                "type": "volume",
                "severity": "critical",
                "description": f"Слишком коротко ({words} слов)",
                "text": text,
            })

        # 2. Ссылки
        citations = re.findall(r"\[\d+,\s*с\.\s*\d+\]", text)
        if len(citations) < 2 and key not in ("literature", "conclusion"):
            self.issues.append({
                "section": key,
                "type": "citations",
                "severity": "critical",
                "description": f"Мало ссылок ({len(citations)})",
                "text": text,
            })

        # 3. Клише
        cliches = [
            "в современном мире", "необходимо отметить", "следует отметить",
            "таким образом", "играет важную роль", "имеет важное значение",
            "комплексный подход", "широкий спектр", "актуальность обусловлена",
        ]
        found = [c for c in cliches if c in text.lower()]
        if found:
            self.issues.append({
                "section": key,
                "type": "cliches",
                "severity": "medium",
                "description": f"Клише: {', '.join(found[:3])}",
                "text": text,
            })

        # 4. Соответствие теме
        if topic and topic.lower()[:20] not in text.lower():
            self.issues.append({
                "section": key,
                "type": "relevance",
                "severity": "critical",
                "description": "Не соответствует теме",
                "text": text,
            })

        # 5. Оборванные ссылки
        if re.search(r"\[\d+,\s*с\.\s*$", text):
            self.issues.append({
                "section": key,
                "type": "broken_citation",
                "severity": "critical",
                "description": "Оборванная ссылка",
                "text": text,
            })

    async def _fix_issue(
        self,
        parts: dict[str, str],
        issue: dict,
        model_key: str,
        chat_fn: Callable[..., Awaitable[tuple[str, str]]],
    ):
        """Исправляет конкретную проблему."""

        key = issue["section"]
        text = parts.get(key, "")

        if issue["type"] == "volume":
            # Дополняем текст
            prompt = f"""
            Дополни следующий текст, раскрой тему:

            {text}

            Добавь 3-5 предложений, раскрывающих тему.
            Верни только дополненный текст.
            """
            messages = [
                {"role": "system", "content": "Ты академический автор. Дописывай текст содержательно."},
                {"role": "user", "content": prompt}
            ]
            fixed, _ = await chat_fn(model_key, messages, 2048)
            if fixed and len(fixed.split()) > len(text.split()):
                parts[key] = fixed
                self.fixes.append(f"Дополнен раздел {key}")

        elif issue["type"] == "citations":
            # Добавляем ссылки
            prompt = f"""
            Добавь в текст ссылки на источники в формате [N, с. X].
            Поставь ссылки там, где они логичны.

            Текст:
            {text}

            Верни только текст со ссылками.
            """
            messages = [
                {"role": "system", "content": "Ты академический редактор. Добавляй ссылки там, где уместно."},
                {"role": "user", "content": prompt}
            ]
            fixed, _ = await chat_fn(model_key, messages, 2048)
            if fixed and len(re.findall(r"\[\d+,\s*с\.\s*\d+\]", fixed)) > len(re.findall(r"\[\d+,\s*с\.\s*\d+\]", text)):
                parts[key] = fixed
                self.fixes.append(f"Добавлены ссылки в {key}")

        elif issue["type"] == "cliches":
            # Убираем клише
            from humanizer import remove_cliches
            fixed = remove_cliches(text)
            if fixed != text:
                parts[key] = fixed
                self.fixes.append(f"Убраны клише из {key}")

        elif issue["type"] == "broken_citation":
            # Исправляем оборванные ссылки
            fixed = re.sub(r"\[\d+,\s*с\.\s*$", "", text)
            if fixed != text:
                parts[key] = fixed
                self.fixes.append(f"Исправлены ссылки в {key}")
