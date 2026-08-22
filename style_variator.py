# -*- coding: utf-8 -*-
"""style_variator — случайное смешивание стилей для повышения "человечности"."""

import random
import re


STYLE_PATTERNS = {
    "научный": {
        "markers": ["согласно имеющимся данным", "в контексте рассматриваемой темы", "исходя из проведенного анализа"],
    },
    "публицистический": {
        "markers": ["безусловно", "очевидно", "как показывает практика"],
    },
    "академический": {
        "markers": ["как представляется", "целесообразно отметить", "примечательно, что"],
    },
    "описательный": {
        "markers": ["следует обратить внимание", "рассмотрим подробнее", "важно подчеркнуть"],
    },
}


def mix_styles(text: str) -> str:
    """Перемешивает стили в разных частях текста для естественности."""
    if not text:
        return text

    # Разбиваем на предложения
    raw_sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(raw_sentences) < 3:
        return text

    styles = list(STYLE_PATTERNS.keys())
    mixed = []

    for i, sent in enumerate(raw_sentences):
        sent_str = sent.strip()
        if not sent_str:
            continue

        # Запоминаем конечное знак препинания если есть
        punct = ""
        if sent_str[-1] in ".!?":
            punct = sent_str[-1]
            sent_body = sent_str[:-1].strip()
        else:
            punct = "."
            sent_body = sent_str

        if not sent_body:
            continue

        # Вставляем вводное слово / маркер стиля
        if random.random() < 0.25 and len(sent_body) > 25:
            style = random.choice(styles)
            pattern = STYLE_PATTERNS[style]
            marker = random.choice(pattern["markers"])
            if marker.endswith("что"):
                sent_body = f"{marker.capitalize()} {sent_body[0].lower() + sent_body[1:] if len(sent_body) > 1 else sent_body}"
            else:
                sent_body = f"{marker.capitalize()}, {sent_body[0].lower() + sent_body[1:] if len(sent_body) > 1 else sent_body}"
        elif random.random() < 0.15 and len(sent_body) > 25:
            intros = ["кроме того", "более того", "вместе с тем", "следовательно"]
            intro = random.choice(intros)
            sent_body = f"{intro.capitalize()}, {sent_body[0].lower() + sent_body[1:] if len(sent_body) > 1 else sent_body}"

        mixed.append(sent_body + punct)

    return " ".join(mixed)
