"""
Главная точка входа приложения Voice Language Tutor.
Позволяет выбрать режим работы:
  - Терминальный (CLI / sounddevice): python main.py --cli (по умолчанию)
  - Браузерный (Web UI / FastAPI): python main.py --web
"""

from __future__ import annotations

import argparse
import sys

# Обеспечиваем UTF-8 для вывода в консоль Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="AI Voice Tutor — разговорная практика английского (цель B1+) в реальном времени."
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Запустить в режиме Web UI (браузерный интерфейс FastAPI + Web Audio API)",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Запустить в режиме CLI (нативный терминал + sounddevice)",
    )

    args = parser.parse_args()

    if args.web:
        import web_app
        web_app.run_server()
    else:
        # По умолчанию режим CLI
        import cli
        cli.main()


if __name__ == "__main__":
    main()
