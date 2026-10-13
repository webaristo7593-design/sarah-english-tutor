"""
Движок долговременной памяти (Cross-Session Memory Engine) для English B2 & Design.
Анализирует архив заметок в Obsidian (notes/ и notes/),
извлекает историю тем, сложные грамматические моменты, словарный запас
и инжектирует персональный контекст в системный промпт Sarah.
"""

from __future__ import annotations

import glob
from pathlib import Path
import re
import json
import hashlib
import threading

_archive_lock = threading.RLock()


def _archive_dirs():
    return {config.OBSIDIAN_NOTES_DIR.resolve()}


def _trash_files():
    for directory in _archive_dirs():
        trash = directory / '.sarah-trash'
        if trash.is_dir() and not trash.is_symlink() and trash.resolve() == trash:
            yield from (p for p in trash.glob('*Session*.md') if p.is_file() and not p.is_symlink())


def get_deleted_lessons():
    with _archive_lock:
        return {"sessions": [{"id": session_id(p), "filename": p.name} for p in _trash_files()]}


def trash_learning_note(note_id):
    with _archive_lock:
        source = next((p for p in get_session_files() if session_id(p) == note_id), None)
        if source is None:
            raise FileNotFoundError()
        if source.is_symlink() or source.resolve().parent not in _archive_dirs():
            raise ValueError('Invalid archive path')
        trash = source.parent / '.sarah-trash'
        if trash.is_symlink() or trash.resolve().parent != source.parent.resolve():
            raise ValueError('Invalid trash path')
        trash.mkdir(exist_ok=True)
        target = trash / source.name
        if target.exists():
            raise FileExistsError()
        source.rename(target)
        return {"id": session_id(target), "filename": target.name}


def restore_learning_note(note_id):
    with _archive_lock:
        source = next((p for p in _trash_files() if session_id(p) == note_id), None)
        if source is None:
            raise FileNotFoundError()
        target = source.parent.parent / source.name
        if target.exists():
            raise FileExistsError()
        source.rename(target)
        return {"id": session_id(target), "filename": target.name}
from typing import Any, Dict, List

import config


def get_session_files() -> List[Path]:
    """
    Возвращает список файлов сессий из Obsidian.
    Сначала ищет в English-папке, затем в общей Languages.
    """
    english_dir = config.OBSIDIAN_NOTES_DIR
    parent_dir = english_dir.parent

    all_files: List[Path] = []
    if english_dir.exists():
        all_files.extend(list(english_dir.glob("English_B2_Session_*.md")))
        all_files.extend(list(english_dir.glob("*Session*.md")))


        

    # Дедупликация с сохранением уникальных путей
    unique_files = list({f.resolve(): f for f in all_files}.values())
    def modified(path):
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0
    unique_files.sort(key=modified, reverse=True)
    return unique_files


def extract_section(content: str, header: str, next_headers: List[str]) -> str:
    """Извлекает текст секции между заголовком header и следующим заголовком."""
    # Both ## and ### occur in past generated notes. Stop at the next heading.
    marker = header.lstrip("# ")
    match = re.search(r"^#{1,6}\s+" + re.escape(marker) + r"[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)", content, re.M | re.S)
    return match.group(1).strip() if match else ""


def parse_session_note(file_path: Path) -> Dict[str, Any]:
    """Парсит одну заметку сессии и возвращает структурированные данные."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return {}

    data: Dict[str, Any] = {
        "file": file_path.name,
        "date": "",
        "summary": "",
        "vocab": [],
        "errors": [],
        "corrections": [],
        "is_english": "English" in file_path.name or "english" in text.lower(),
    }

    date_match = re.search(r"date:\s*([0-9\-]+)", text)
    if date_match:
        data["date"] = date_match.group(1)

    abs_match = re.search(r">\s*\[!abstract\][^\n]*\n(>.*(?:\n>.*)*)", text)
    if abs_match:
        callout_lines = [line.lstrip("> ").strip() for line in abs_match.group(1).splitlines()]
        data["summary"] = " ".join([l for l in callout_lines if l])

    # Извлечение словаря
    vocab_text = extract_section(text, "### 📌", ["### ⚠️", "---", "### 💡"])
    if vocab_text:
        for line in vocab_text.splitlines():
            line = line.strip()
            if line.startswith("|") and not line.startswith("| :") and not "Term" in line and not "Zwrot" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2 and not all(re.fullmatch(r"[-: ]+", part) for part in parts):
                    clean_word = parts[0].replace("**", "").strip()
                    data["vocab"].append(f"{clean_word} — {parts[1]}")
            elif line.startswith("- "):
                data["vocab"].append(line[2:].strip())

    # Извлечение ошибок
    err_text = extract_section(text, "### ⚠️", ["### 💡", "---", "### 🎯"])
    if err_text:
        for line in err_text.splitlines():
            line = line.strip()
            if line.startswith("|") and not line.startswith("| :") and not "What Learner" in line and not "Co powiedział" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3 and not all(re.fullmatch(r"[-: ]+", part) for part in parts):
                    data["errors"].append(f"Learner said: '{parts[0]}' -> Native: '{parts[1]}' ({parts[2]})")
                    data["corrections"].append({"original": parts[0], "correction": parts[1], "rule": parts[2]})

    saved = re.search(r"<!-- tutor-feedback\n(.*?)\n-->", text, re.S)
    if saved:
        try:
            feedback_items = json.loads(saved.group(1))
            # Saved live feedback is authoritative; summary tables may contain
            # placeholders or optional rewrites that were never shown as errors.
            data["corrections"] = []
            data["errors"] = []
            for feedback in feedback_items:
                for item in feedback.get("corrections", []):
                    data["errors"].append(f"{item.get('original', '')} → {item.get('correction', '')} — {item.get('rule', '')}")
                    data["corrections"].append(item)
                if feedback.get("takeaway"):
                    data["vocab"].append(feedback["takeaway"])
        except (ValueError, TypeError, AttributeError):
            pass
    if not data["summary"]:
        dialogue = extract_section(text, "Original transcript", []) or extract_section(text, "Session transcript", []) or extract_section(text, "💬 Spoken Dialogue", [])
        if dialogue:
            data["summary"] = "Фрагмент прошлого разговора: " + dialogue[:1200]

    return data


def session_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]


def get_learning_library() -> dict:
    sessions = []
    for path in get_session_files():
        parsed = parse_session_note(path)
        if not parsed:
            continue
        parsed["id"] = session_id(path)
        stamp = re.search(r"\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})", path.name)
        parsed["time"] = f"{stamp[1]}:{stamp[2]}" if stamp else ""
        parsed["legacy"] = path.name.startswith("English_B2_")
        # Generated summaries and verbatim feedback can describe the same slip.
        unique = {}
        for item in parsed["corrections"]:
            key = (item.get("original", "").strip("* "), item.get("correction", "").strip("* "))
            if all(key) and key[1].lower() not in {"n/a", "na", "—", "-", "none"} and not re.search(r"no (grammatical |genuine )?errors|ошибок нет", key[0], re.I):
                unique[key] = item
        parsed["corrections"] = list(unique.values())
        sessions.append(parsed)
    return {"sessions": sessions}


def read_learning_note(note_id: str) -> dict | None:
    # Resolve only files discovered in the lesson archive; no user-controlled paths.
    for path in get_session_files():
        if session_id(path) == note_id:
            return {"filename": path.name, "markdown": path.read_text(encoding="utf-8")}
    return None


def get_memory_stats() -> Dict[str, Any]:
    """Возвращает агрегированную статистику памяти для Nova Web UI."""
    files = get_session_files()
    parsed = [parse_session_note(f) for f in files[:5]]
    return {
        "total_sessions": len(files),
        "recent_topics": [p["summary"] for p in parsed if p.get("summary")][:3],
        "weak_points": [e for p in parsed for e in p.get("errors", [])][:5],
        "recent_vocab": list(dict.fromkeys(v for p in parsed for v in p.get("vocab", [])))[:8],
    }


def get_cross_session_context() -> str:
    """
    Генерирует блок долгосрочной памяти на английском для внедрения в системный промпт Sarah.
    """
    files = get_session_files()
    if not files:
        return (
            "SESSION HISTORY & LEARNING MEMORY:\n"
            "This is your very first English strategy session with Learner. "
            "Welcome the learner warmly, ask what they would like to practice today, "
            "and use the selected target level."
        )

    parsed_sessions = [parse_session_note(f) for f in files[:4]]

    lines = [
        "MEMORY & CONTEXT FROM PREVIOUS SESSIONS WITH LEARNER (CRITICAL FOR CONTINUITY):",
        f"- Total archived sessions in his Obsidian Second Brain: {len(files)}.",
    ]

    summaries = [p["summary"] for p in parsed_sessions if p.get("summary")]
    if summaries:
        lines.append("- Recent conversation topics & background context: " + " | ".join(summaries[:2]))

    all_errors = []
    for p in parsed_sessions:
        all_errors.extend(p.get("errors", []))
    if all_errors:
        lines.append("- Nuance & grammar points previously highlighted (check naturally in today's dialogue):")
        for err in all_errors[:4]:
            lines.append(f"  * {err}")

    all_vocab = []
    for p in parsed_sessions:
        all_vocab.extend(p.get("vocab", []))
    if all_vocab:
        lines.append("- Vocabulary & idioms Learner recently practiced: " + ", ".join(all_vocab[:6]))

    lines.append(
        "\nCONVERSATION OPENING INSTRUCTION:\n"
        "In your very first response, naturally connect with his recent topics or interests "
        "(e.g., ask about his Figma projects, client interactions, or energy levels today) "
        "so Learner immediately feels a continuous, personalized mentorship loop."
    )

    return "\n".join(lines)
