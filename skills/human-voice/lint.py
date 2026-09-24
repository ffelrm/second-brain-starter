#!/usr/bin/env python3
"""
human-voice lint: механический проход по тексту, который уходит наружу.

Ищет лексические маркеры машинного письма, риторические приёмы сверх бюджета
и слишком ровную форму. Текст не правит, только показывает места к разбору.
Каждое место либо исправляется заменой на конкретику, либо остаётся
с осознанным «оставляю, потому что…». Протокол прохода: SKILL.md рядом.

Запуск из корня системы:
    python3 skills/human-voice/lint.py <файл.md>
    python3 skills/human-voice/lint.py <файл.md> --budget-per 10000 --allow-dash

Ключи:
    --budget-per N   на сколько знаков текста рассчитан бюджет приёмов (10000)
    --allow-dash     не отмечать длинное тире, если твоя «Форма и стиль» его разрешает

Нужен Python 3.8 или новее, сторонних пакетов нет.
YAML-фронтматтер и всё, что ниже заголовка «## Служебное», в анализ не входят.
Свои запреты из секции «Форма и стиль» глоссария впиши в FORM_RULES ниже.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

# ---------------------------------------------------------------- словари
# Лексика ищется без различия заглавных и строчных букв.
# Шаблон, который начинается с «^», ловит только начало строки.

LEXICON = {
    "мета-связки (текст комментирует сам себя)": [
        r"на самом деле", r"по сути", r"важно понимать", r"важно отметить",
        r"стоит отметить", r"стоит признать", r"нельзя не отметить",
        r"ключевой момент", r"принципиально важно", r"отсюда вывод",
        r"логика прост", r"если коротко", r"иными словами", r"другими словами",
        r"как мы видим", r"давайте разбер", r"как я уже говорил",
        r"забегая вперёд", r"забегая вперед",
    ],
    "пустые усилители": [
        r"\bдействительно\b", r"\bпоистине\b", r"\bкрайне\b", r"\bневероятн",
        r"\bпоразительн", r"\bколоссальн", r"\bмаксимально\b",
        r"по-настоящему", r"\bподлинн",
        r"\bнастоящ(ий|ая|ее) (вызов|проблем|прорыв)",
    ],
    "корпоративный новояз": [
        r"бесшовн", r"экосистем", r"синерги", r"трансформаци", r"\bдрайвер",
        r"парадигм", r"фундаментальн", r"тектоническ", r"\bландшафт",
        r"вызовы времени", r"в эпоху", r"в мире, где", r"на стыке",
        r"точк[аи] роста", r"эффективн[ая]{1,2} коммуникаци",
        r"повышени[ея] эффективности", r"цифровизаци",
    ],
    "пафосные зачины": [
        r"сегодня уже никого не удивишь", r"мир меняется",
        r"в последние годы мы наблюдаем", r"не просто \w+, а ",
        r"трудно переоценить", r"ни для кого не секрет",
    ],
    "приказы читателю": [
        r"^смотрите[,.]", r"^представьте", r"^вдумайтесь", r"^давайте честно",
        r"^посудите сами",
    ],
    "неестественные сочетания (канцелярит)": [
        # смысла шаблоны не различают: прямое значение тоже попадёт в отчёт
        r"\bосуществл\w* деятельност", r"\bпроизв(?:од|ед|ест|ёл|ел)\w* оплат",
        r"\bв разрезе\b", r"\bв части\b",
    ],
}

# Длинное тире отмечается каждое. Ключ --allow-dash выключает проверку.
DASH = {"длинное тире": [r"—"]}

# Свои запреты формы: слова и обороты из секции «Форма и стиль» глоссария.
# По одному регулярному выражению на запрет, например r"\bфидбэк\w*".
FORM_RULES = []

# Риторические приёмы, у которых есть бюджет на объём текста.
# Здесь заглавные буквы важны: шаблоны опираются на начало предложения.
DEVICES = {
    "антитеза-уточнение": {
        "budget": 1,
        "patterns": [
            r"Не[ ]«[^»]+»\.\s*[А-ЯЁ]",                   # Не «X». Y
            r"Не\s+[«\"]?\w+[»\"]?\.\s+[А-ЯЁ]\w+\.",        # Не X. Y.
            r"\bЭто не \w+[^.]*\.\s*Это ",                  # Это не X. Это Y
            r"\bЭто не [^.!?]{2,45}, это ",                 # Это не X, это Y
            r"(?<=[.!?»])\s+Это [^.!?,]{2,45}, это ",       # Это X, это Y
            r"\bДело не в [^.]+\.\s*Дело ",                 # Дело не в X. Дело в Y
            r"\bне [^.,]{2,30}, а именно ",
            r"(?<=[.!?»])\s+Не [^.!?]{2,50}, а [^.!?]{2,50}[.!?]",  # Не X, а Y.
        ],
    },
    "односложный выстрел": {
        "budget": 2,
        "patterns": [r"(?<=[.!?])\s+[А-ЯЁ][а-яё]+\.(?=\s)"],
    },
    "правило трёх": {
        "budget": 2,
        "patterns": [
            # ряд из трёх коротких элементов через запятую
            r"(?<![,\w])(\w+(?:\s\w+)?),\s(\w+(?:\s\w+)?),\s(?:и\s)?(\w+(?:\s\w+)?)(?=[.:;\n])",
            # три коротких предложения подряд
            r"(?<=[.!?])\s+[А-ЯЁ][^.!?]{4,45}\.\s+[А-ЯЁ][^.!?]{4,45}\.\s+[А-ЯЁ][^.!?]{4,45}\.",
        ],
    },
    "анафора (одинаковый зачин подряд)": {
        "budget": 1,
        "patterns": [
            r"(?<=[.!?])\s+([А-ЯЁ][а-яё]+)\b[^.!?]{5,}[.!?]\s+\1\b[^.!?]{5,}[.!?]",
        ],
    },
    "болдовый лид-ин": {
        "budget": 4,
        # абзац или пункт списка, который начинается с «**Слово.**»
        "patterns": [r"^(?:[-*+]\s+|\d+[.)]\s+)?\*\*[А-ЯЁ][^*]{2,40}\.\*\*"],
    },
    "обращение к читателю": {
        "budget": 1,
        "patterns": [r"(?i)\b(смотрите|представьте|вдумайтесь|посудите)\b"],
    },
    "двоеточие-барабан": {
        "budget": 1,
        "patterns": [
            r"[Оо]твет (прост\w*|один|такой):",
            r"[Пп]ричина (одна|прост\w*|в том):",
            r"[Вв]ывод (такой|один|прост\w*):",
            r"[Сс]уть (прост\w*|в том):",
        ],
    },
}

SERVICE_HEADING = re.compile(r"^#{2,3}\s+Служебное\s*$", re.M)


# ---------------------------------------------------------------- утилиты

def strip_service(text: str) -> str:
    """Убрать фронтматтер и служебный блок. Нумерация строк не сдвигается."""
    m = re.match(r"---\n.*?\n---\n", text, flags=re.S)
    if m:
        text = "\n" * m.group(0).count("\n") + text[m.end():]
    m = SERVICE_HEADING.search(text)
    if m:
        text = text[:m.start()]
    return text


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма существительного после числа: 1 знак, 2 знака, 5 знаков."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def snippet(text: str, start: int, end: int, pad: int = 34) -> str:
    s = text[max(0, start - pad):min(len(text), end + pad)]
    return " ".join(s.split())


def find_spans(patterns: list[str], text: str, base_flags: int = 0) -> list[list[int]]:
    """Все совпадения шаблонов. Пересекающиеся сливаются: одно место считается один раз."""
    spans = []
    for pat in patterns:
        flags = base_flags | (re.M if pat.startswith("^") else 0)
        for m in re.finditer(pat, text, flags):
            lead = len(m.group(0)) - len(m.group(0).lstrip())
            spans.append((m.start() + lead, m.end()))
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def sentences(text: str) -> list[str]:
    plain = re.sub(r"^#{1,6} .*$", "", text, flags=re.M)
    plain = re.sub(r"[*_`>|-]", " ", plain)
    parts = re.split(r"(?<=[.!?])\s+", plain)
    return [p.strip() for p in parts if len(p.strip()) > 1]


def paragraphs(text: str) -> list[str]:
    body = re.sub(r"^#{1,6} .*$", "", text, flags=re.M)
    return [p.strip() for p in body.split("\n\n") if len(p.strip()) > 40]


# ---------------------------------------------------------------- проходы

def pass_lexicon(text: str, lexicon: dict) -> list[tuple[str, int, str, str]]:
    """Находки по категориям, внутри категории по порядку строк."""
    hits = []
    for category, patterns in lexicon.items():
        for start, end in find_spans(patterns, text, re.I):
            hits.append((category, line_of(text, start),
                         " ".join(text[start:end].split()),
                         snippet(text, start, end)))
    return hits


def pass_devices(text: str, budget_per: int) -> dict:
    scale = max(1.0, len(text) / budget_per)
    report = {}
    for name, spec in DEVICES.items():
        found = [(line_of(text, s), snippet(text, s, e))
                 for s, e in find_spans(spec["patterns"], text)]
        allowed = int(spec["budget"] * scale)
        report[name] = {"found": found, "allowed": allowed,
                        "over": max(0, len(found) - allowed)}
    return report


def pass_shape(text: str) -> dict:
    sents = [len(s) for s in sentences(text)]
    paras = [len(p) for p in paragraphs(text)]
    out = {}
    if sents:
        out["предложения"] = {
            "n": len(sents), "средняя": round(statistics.mean(sents)),
            "разброс": round(statistics.pstdev(sents)),
            "коротких (<40)": sum(1 for x in sents if x < 40),
            "длинных (>220)": sum(1 for x in sents if x > 220),
        }
    if paras:
        out["абзацы"] = {
            "n": len(paras), "средняя": round(statistics.mean(paras)),
            "разброс": round(statistics.pstdev(paras)),
            "самый короткий": min(paras), "самый длинный": max(paras),
        }
    return out


def pass_substance(text: str) -> list[str]:
    """Грубые индикаторы слоя D: есть ли в тексте вообще конкретика."""
    notes = []
    digits = len(re.findall(r"\d", text))
    if digits / max(1, len(text)) < 0.004:
        notes.append("мало цифр на объём текста: проверь тест на невозможность")
    names = re.findall(
        r"\b[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+(?:ов|ев|ин|ова|ева|ина|ко|ский|ская|ых)\b",
        text)
    if not names:
        notes.append("в тексте нет ни одного имени: кто ещё в кадре, кроме автора?")
    if not re.search(r"[«\"][^»\"]{15,300}[»\"]", text):
        notes.append("нет ни одной прямой реплики: добавь чужие слова")
    round_nums = re.findall(r"(?<!\d)(?:[1-9]0|[1-9]00|[1-9]000)(?!\d)", text)
    if len(round_nums) > 4:
        notes.append(f"много круглых чисел ({len(round_nums)}): читаются как выдуманные")
    return notes


# ---------------------------------------------------------------- вывод

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="human-voice lint: маркеры машинного письма в тексте наружу")
    ap.add_argument("file", help="markdown-файл с текстом")
    ap.add_argument("--budget-per", type=int, default=10000,
                    help="на сколько знаков рассчитан бюджет приёмов (по умолчанию 10000)")
    ap.add_argument("--allow-dash", action="store_true",
                    help="не отмечать длинное тире")
    args = ap.parse_args()

    if args.budget_per <= 0:
        print("--budget-per должен быть больше нуля", file=sys.stderr)
        return 2

    path = Path(args.file)
    if path.is_dir():
        print(f"это папка, а нужен файл: {path}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2

    try:
        raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        print(f"файл не в кодировке UTF-8: {path}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"не удалось прочитать {path}: {err.strerror or err}", file=sys.stderr)
        return 2
    text = strip_service(raw)
    size = len(text.strip())

    lexicon = dict(LEXICON)
    if not args.allow_dash:
        lexicon.update(DASH)
    if FORM_RULES:
        lexicon["свои запреты формы (FORM_RULES)"] = FORM_RULES

    print(f"# human-voice lint: {path.name}")
    print(f"тело текста: {size} {plural(size, 'знак', 'знака', 'знаков')}\n")

    lex = pass_lexicon(text, lexicon)
    print(f"## 1. Лексика ({len(lex)})")
    if not lex:
        print("чисто\n")
    else:
        current = None
        for category, ln, hit, ctx in lex:
            if category != current:
                print(f"\n### {category}")
                current = category
            print(f"  стр.{ln:>4}  «{hit}»   …{ctx}…")
        print()

    dev = pass_devices(text, args.budget_per)
    over_total = sum(v["over"] for v in dev.values())
    print(f"## 2. Риторические приёмы (перебор: {over_total})")
    for name, data in dev.items():
        mark = "ПЕРЕБОР" if data["over"] else "ок"
        print(f"\n### {name}: {len(data['found'])} шт, бюджет {data['allowed']} [{mark}]")
        for ln, ctx in data["found"]:
            print(f"  стр.{ln:>4}  …{ctx}…")
    print()

    shape = pass_shape(text)
    print("## 3. Форма")
    for block, stats in shape.items():
        print(f"  {block}: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    paras = shape.get("абзацы", {})
    if paras.get("n", 0) >= 3 and paras["разброс"] / max(1, paras["средняя"]) < 0.45:
        print("  ! абзацы слишком ровные, разбавь короткими и длинными")
    print()

    sub = pass_substance(text)
    print("## 4. Содержание (слой D)")
    if not sub:
        print("  индикаторы конкретики в норме")
    for n in sub:
        print(f"  ! {n}")
    print()

    verdict = len(lex) + over_total + len(sub)
    print(f"## Итог: {verdict} {plural(verdict, 'место', 'места', 'мест')} к разбору")
    print("Каждое либо чинится заменой на конкретику, либо получает осознанное «оставляю».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
