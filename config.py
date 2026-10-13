"""
Конфигурация проекта Voice Language Tutor.
Автоматически считывает переменные из .env или системного окружения.
"""

from __future__ import annotations

import os
from pathlib import Path

# Поиск .env файла (в текущей папке или родительской)
CURRENT_DIR = Path(__file__).resolve().parent
ENV_PATH = CURRENT_DIR / ".env"

if ENV_PATH.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_PATH)
    except ImportError:
        # Резервный парсер на случай отсутствия python-dotenv
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

# Основные параметры
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live").strip()
GEMINI_SUMMARY_MODEL = os.getenv("GEMINI_SUMMARY_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_TRANSCRIBE_MODEL = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live").strip()
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Aoede").strip()

# Аудиопараметры Gemini Live API
# Gemini Live ожидает входной PCM 16kHz mono 16-bit
INPUT_SAMPLE_RATE = int(os.getenv("INPUT_SAMPLE_RATE", "16000"))
# Gemini Live возвращает PCM 24kHz mono 16-bit
OUTPUT_SAMPLE_RATE = int(os.getenv("OUTPUT_SAMPLE_RATE", "24000"))
AUDIO_CHUNK_SIZE = int(os.getenv("AUDIO_CHUNK_SIZE", "1024"))

# Obsidian путь для английского языка
OBSIDIAN_RAW_DIR = os.getenv("OBSIDIAN_NOTES_DIR", "notes").strip()
OBSIDIAN_NOTES_DIR = (CURRENT_DIR / OBSIDIAN_RAW_DIR).resolve()

# Web UI
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip()
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

def validate_api_key() -> str:
    """Проверяет наличие API-ключа и выбрасывает понятное исключение при отсутствии."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        raise ValueError(
            "\n[ОШИБКА] GEMINI_API_KEY не установлен!\n"
            f"Откройте файл '{ENV_PATH}' и укажите ваш ключ:\n"
            "GEMINI_API_KEY=AIzaSy...\n"
            "Создать свой ключ: https://aistudio.google.com/apikey\n"
        )
    return GEMINI_API_KEY
