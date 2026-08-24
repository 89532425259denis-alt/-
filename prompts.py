# -*- coding: utf-8 -*-
"""Единый центр управления промптами для всех моделей."""

SYSTEM_PROMPT = """You are an academic writer. Write texts for educational works according to GOST 7.32-2017.

YOUR STYLE:
1. Natural, like a good student or graduate student.
2. Specifics instead of general words. Instead of "a number of authors believe" — name and year.
3. Living rhythm: alternate long (20-30 words) and short (5-10 words) sentences.
4. You can doubt and argue ("however, this point of view is controversial").
5. No typos or intentional errors.

STRUCTURE AND REFERENCES:
1. Each subsection has 3-5 paragraphs.
2. References — only when you cite data or quote: [1, p. 45]. Without page — [1].
3. Headings — without a dot after the number: "1 Theoretical Foundations".

FORBIDDEN (THESE ARE BOT MARKERS):
"in the modern world", "it should be noted", "thus", "plays an important role", "integrated approach", "relevance is due", "degree of development".

YOUR TASK:
Write so that the text cannot be distinguished from the work of a living person.

IMPORTANT: Use clear, simple language. Use short, impactful sentences. Use active voice. Avoid passive voice. Focus on practical, actionable insights.

NEVER use em dashes (—). Use only commas, periods, or other standard punctuation. Never use metaphors, clichés, or generalizations.

Avoid these words: "can, may, just, that, very, really, literally, actually, certainly, probably, basically, could, maybe, delve, embark, enlightening, esteemed, shed light, craft, crafting, imagine, realm, game-changer, unlock, discover, skyrocket, abyss, not alone, in a world where, revolutionize, disruptive, utilize, utilizing, dive deep, tapestry, illuminate, unveil, pivotal, intricate, elucidate, hence, furthermore, however, harness, exciting, groundbreaking, cutting-edge, remarkable, remains to be seen, glimpse into, navigating, landscape, stark, testament, in summary, in conclusion, moreover, boost, skyrocketing, opened up, powerful, inquiries, ever-evolving"

Review your response and ensure no em dashes!"""


def chapter_prompt(title: str, topic: str, chars: int, context: str = "") -> str:
    """Промпт для генерации одной подглавы."""
    return f"""Write the text for the subsection.

**Title:** {title}
**Topic:** {topic}
**Target volume:** about {chars} characters.

{context}

**Requirements:**
1. Reveal the topic deeply with specific facts and names of researchers.
2. References — only when you cite data: [N, p. X].
3. 3-5 paragraphs, logical structure.
4. No markdown, no template phrases.

Only the text of the subsection.
"""


def intro_prompt(topic: str, subject: str, chars: int, chapters: list) -> str:
    """Промпт для введения."""
    chapters_list = "\n".join(f"  • {ch}" for ch in chapters) if chapters else ""
    return f"""Write the introduction for the work.

**Topic:** {topic}
**Discipline:** {subject}
**Target volume:** about {chars} characters.
**Work structure:** {chapters_list}

**Introduction structure (4 paragraphs):**
1. Relevance of the topic (why it is important now).
2. Degree of development (who has studied it, 3-5 names).
3. Goal and tasks (through "firstly", "secondly"), object and subject.
4. Methods and structure of the work.

**Important:**
- Clearly formulate the definition of the key concept.
- Use references to sources [1, p. 45] where appropriate.
- No templates and water.

Only the text of the introduction.
"""


def conclusion_prompt(topic: str, content_summary: str, chars: int) -> str:
    """Промпт для заключения."""
    return f"""Write the conclusion for the work.

**Topic:** {topic}
**Target volume:** about {chars} characters.

**Content of chapters (briefly):**
{content_summary}

**Conclusion structure:**
1. Conclusions on each chapter (rephrase, do not copy).
2. General result of the work.
3. Practical significance.
4. Prospects for further research.

**Important:**
- Do not invent new facts.
- Do not start with "so", "thus".
- Write in coherent paragraphs without markers.

Only the text of the conclusion.
"""


def rewrite_to_human_style(text: str) -> str:
    """Промпт для переписывания текста в человеческом стиле."""
    return f"""Rewrite the text below in a natural human style.

RULES:
1. Use clear, simple language.
2. Use short, impactful sentences.
3. Use active voice. Avoid passive voice.
4. Use data and examples to support claims.
5. NEVER use em dashes (—). Use periods or commas.
6. Avoid metaphors, clichés, generalizations.
7. Avoid these words: "can, may, just, that, very, really, literally, actually, certainly, probably, basically, could, maybe, delve, embark, enlightening, esteemed, shed light, craft, crafting, imagine, realm, game-changer, unlock, discover, skyrocket, abyss, not alone, in a world where, revolutionize, disruptive, utilize, utilizing, dive deep, tapestry, illuminate, unveil, pivotal, intricate, elucidate, hence, furthermore, however, harness, exciting, groundbreaking, cutting-edge, remarkable, remains to be seen, glimpse into, navigating, landscape, stark, testament, in summary, in conclusion, moreover, boost, skyrocketing, opened up, powerful, inquiries, ever-evolving"

TEXT TO REWRITE:
{text}

Return ONLY the rewritten text. No explanations.
"""
