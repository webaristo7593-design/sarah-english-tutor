"""
Терминальный интерфейс голосового тренажера (sounddevice + Gemini Live API).
Стриминг реплик в реальном времени (эффект пишущей машинки) без спама.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
from audio_engine import SoundDeviceAudioEngine
from live_client import GeminiLiveTutor
import obsidian_exporter


def print_banner() -> None:
    print("=" * 65)
    print(" 🎙️  AI-УЧИЛКА: РАЗГОВОРНЫЙ ТРЕНАЖЕР (ENGLISH / TARGET B1+)")
    print("=" * 65)
    print(f" • Модель Live:    {config.GEMINI_LIVE_MODEL}")
    print(f" • Голос Sarah:  {config.GEMINI_VOICE} (Sarah)")
    print(" • Память:         Активна (считывает архив заметок Obsidian)")
    print("-" * 65)
    print(" Управление:")
    print("  - Говорите прямо в микрофон на английском языке.")
    print("  - Обсудите работу, дизайн или повседневные темы.")
    print("  - Нажмите [Ctrl + C] для завершения сессии и сохранения конспекта.")
    print("=" * 65 + "\n")


async def main_async() -> None:
    try:
        config.validate_api_key()
    except ValueError as e:
        print(e)
        return

    print_banner()

    start_time = datetime.datetime.now()
    loop = asyncio.get_running_loop()
    audio_engine = SoundDeviceAudioEngine()

    def on_audio_chunk(data: bytes) -> None:
        audio_engine.enqueue_audio_chunk(data)

    def on_turn_start(speaker: str) -> None:
        if speaker == "user":
            sys.stdout.write("\n[Learner]: ")
        else:
            sys.stdout.write("\n[🤖 Sarah]:    ")
        sys.stdout.flush()

    def on_transcript_delta(speaker: str, delta: str, accumulated: str) -> None:
        sys.stdout.write(delta)
        sys.stdout.flush()

    def on_turn_complete(speaker: str, full_text: str) -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()

    def on_interrupted() -> None:
        audio_engine.flush_playback()
        print("\n[⚡ Перебивание: Sarah умолкла]")

    def on_status(msg: str) -> None:
        print(f"[i] {msg}")

    tutor = GeminiLiveTutor(
        on_audio_chunk=on_audio_chunk,
        on_turn_start=on_turn_start,
        on_transcript_delta=on_transcript_delta,
        on_turn_complete=on_turn_complete,
        on_interrupted=on_interrupted,
        on_status=on_status,
    )

    async def mic_generator():
        while True:
            chunk = await audio_engine.input_queue.get()
            yield chunk

    audio_engine.start(loop)

    try:
        await tutor.run(mic_generator())
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n\n[!] Завершение диалога...")
    except Exception as e:
        print(f"\n[!] Ошибка в процессе сессии: {e}")
    finally:
        audio_engine.stop()
        tutor.stop()
        end_time = datetime.datetime.now()

        print("\n" + "=" * 65)
        print(" 📝 ГЕНЕРАЦИЯ КОНСПЕКТА СЕССИИ В OBSIDIAN...")
        print("=" * 65)

        note_path = obsidian_exporter.save_session_note(
            transcript=tutor.transcript_history,
            start_time=start_time,
            end_time=end_time,
        )

        formatted_uri = f"file:///{note_path.as_posix()}"
        print("✅ Конспект успешно сохранен в Obsidian!")
        print(f"👉 Ссылка: [{note_path.name}]({formatted_uri})")
        print("=" * 65 + "\n")


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
