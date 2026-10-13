"""
Асинхронный клиент Gemini Live API (двунаправленный стриминг по WebSockets).
Включает агрегатор транскрипта (устраняет спам микросообщениями),
поддержку переключения языков mid-stream и динамическую память из Obsidian.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Callable, Dict, List, Optional

from google import genai
from google.genai import types

import config
import system_prompt


class GeminiLiveTutor:
    """
    Управляет сессией Gemini Live API.
    Принимает входной аудиопоток (16kHz PCM), отправляет в Gemini,
    стримит обратно аудиоответ (24kHz PCM) и агрегирует реплики диалога.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        voice_name: Optional[str] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_turn_start: Optional[Callable[[str], None]] = None,
        on_transcript_delta: Optional[Callable[[str, str, str], None]] = None,
        on_turn_complete: Optional[Callable[[str, str], None]] = None,
        on_interrupted: Optional[Callable[[], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
        on_response_complete: Optional[Callable[[], None]] = None,
        practice_focus: str = "Free conversation",
        target_level: str = "B1+",
        manual_turns: bool = False,
    ) -> None:
        self.api_key = api_key or config.validate_api_key()
        self.model_name = model_name or config.GEMINI_LIVE_MODEL
        self.voice_name = voice_name or config.GEMINI_VOICE

        # Callbacks
        self.on_audio_chunk = on_audio_chunk
        self.on_turn_start = on_turn_start
        self.on_transcript_delta = on_transcript_delta
        self.on_turn_complete = on_turn_complete
        self.on_interrupted = on_interrupted
        self.on_status = on_status
        self.on_ready = on_ready
        self.on_response_complete = on_response_complete
        self.practice_focus = practice_focus
        self.target_level = target_level
        self.manual_turns = manual_turns
        self._activity_open = False
        self._user_interim = ""

        # История диалога для Obsidian (только законченные полноценные реплики)
        self.transcript_history: List[Dict[str, str]] = []

        # Состояние текущего спикера для агрегации
        self._current_speaker: Optional[str] = None
        self._current_user_text: str = ""
        self._current_model_text: str = ""

        self._running = False
        self._client: Optional[genai.Client] = None
        self._session = None
        self._session_ready = asyncio.Event()
        self._send_lock = asyncio.Lock()

    def _notify_status(self, text: str) -> None:
        if self.on_status:
            self.on_status(text)

    def _build_config(self) -> types.LiveConnectConfig:
        """Собирает конфигурацию LiveConnectConfig с актуальным контекстом памяти."""
        instruction_text = system_prompt.get_system_instruction(self.target_level) + (
            f"\nToday's practice focus: {self.practice_focus}. "
            "Start with one brief question about this topic. Preserve the learner's intended meaning. "
            "Prefer clear, natural English over business jargon. Never invent errors."
        )
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice_name
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=instruction_text)]
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=self.manual_turns,
                    silence_duration_ms=None if self.manual_turns else 800,
                    prefix_padding_ms=None if self.manual_turns else 40,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
        )

    async def handle_user_text_turn(self, text: str) -> None:
        """
        Обрабатывает распознанную реплику пользователя (из браузерного STT или текстового ввода).
        Фиксирует реплику в истории и немедленно отправляет в Gemini Live API для гарантированного ответа.
        """
        if not text or not text.strip():
            return
        
        clean_text = text.strip()

        # Дожидаемся готовности WebSockets-сессии с Google Cloud
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Gemini Live connection timed out")

        if self._session and self._running:
            try:
                async with self._send_lock:
                    await self._session.send_client_content(
                        turns=[types.Content(role="user", parts=[types.Part.from_text(text=clean_text)])],
                        turn_complete=True,
                    )
                self._current_user_text = clean_text
                self._finalize_turn("user")
            except Exception as e:
                raise RuntimeError("Could not send your message to Gemini") from e

    async def begin_audio_turn(self) -> None:
        """Open a manual turn and immediately interrupt provider generation."""
        if self._session and self._running and not self._activity_open:
            async with self._send_lock:
                await self._session.send_realtime_input(activity_start=types.ActivityStart())
            self._activity_open = True

    async def end_audio_stream(self) -> None:
        """Сигнализирует Gemini Live о завершении реплики через VAD."""
        if self._session and self._running:
            async with self._send_lock:
                if self.manual_turns:
                    if not self._activity_open:
                        return
                    await self._session.send_realtime_input(activity_end=types.ActivityEnd())
                    self._activity_open = False
                else:
                    await self._session.send_realtime_input(audio_stream_end=True)

    async def send_text_cue(self, text: str) -> None:
        """Отправляет текстовую команду mid-stream (например, переключение на английский)."""
        if self._session and self._running:
            try:
                async with self._send_lock:
                    await self._session.send_client_content(
                        turns=[types.Content(role="user", parts=[types.Part.from_text(text=text)])],
                        turn_complete=True,
                    )
                self._notify_status(f"Команда модели: {text}")
            except Exception as e:
                self._notify_status(f"Ошибка отправки подсказки: {e}")

    async def run(self, audio_input_stream: AsyncGenerator[bytes, None]) -> None:
        """Основной цикл соединения с Gemini Live API."""
        self._running = True
        self._client = genai.Client(api_key=self.api_key)

        try:
            cfg = await asyncio.to_thread(self._build_config)
            async with self._client.aio.live.connect(model=self.model_name, config=cfg) as session:
                self._session = session
                self._session_ready.set()
                if self.on_ready:
                    self.on_ready()
                self._notify_status("Connected. Start speaking.")
                send_task = asyncio.create_task(self._send_loop(session, audio_input_stream))
                receive_task = asyncio.create_task(self._receive_loop(session))
                try:
                    done, _ = await asyncio.wait([send_task, receive_task], return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    send_task.cancel()
                    receive_task.cancel()
                    await asyncio.gather(send_task, receive_task, return_exceptions=True)
        finally:
            self._session = None
            self._session_ready.clear()
            self._running = False
            await self._client.aio.aclose()
            self._client.close()

    async def _send_loop(
        self,
        session,
        audio_stream: AsyncGenerator[bytes, None],
    ) -> None:
        """Пересылает чанки микрофона в Gemini Live API."""
        try:
            async for chunk in audio_stream:
                if not self._running:
                    break
                if chunk and len(chunk) > 0:
                    blob = types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={config.INPUT_SAMPLE_RATE}",
                    )
                    async with self._send_lock:
                        await session.send_realtime_input(audio=blob)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._running:
                raise RuntimeError("Microphone stream failed") from e

    async def _receive_loop(self, session) -> None:
        """Обрабатывает входящий поток данных от сервера Gemini с агрегацией реплик для бесконечного диалога."""
        while self._running:
            try:
                received = False
                async for response in session.receive():
                    received = True
                    if not self._running:
                        break

                    server_content = getattr(response, "server_content", None)
                    if server_content is None:
                        continue

                    # 1. Сигнал прерывания (пользователь заговорил)
                    if getattr(server_content, "interrupted", False):
                        self._finalize_turn("model")
                        if self.on_interrupted:
                            self.on_interrupted()

                    # Input can arrive alongside the model's first audio packet.
                    interim = getattr(server_content, "interim_input_transcription", None)
                    if interim and getattr(interim, "text", None):
                        self._user_interim = interim.text
                        if self.on_transcript_delta:
                            preview = self._current_user_text + self._user_interim
                            self.on_transcript_delta("user", "", preview)
                    in_trans = getattr(server_content, "input_transcription", None)
                    if in_trans and getattr(in_trans, "text", None):
                        self._handle_user_delta(in_trans.text)
                        self._user_interim = ""
                    if in_trans and getattr(in_trans, "finished", False) and not self._activity_open:
                        self._finalize_turn("user")

                    # 2. Аудиовывод модели (24kHz PCM)
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn and getattr(model_turn, "parts", None):
                        if self._current_speaker != "model":
                            if self._current_user_text.strip() and not self._activity_open:
                                self._finalize_turn("user")
                            self._current_speaker = "model"
                            if self.on_turn_start:
                                self.on_turn_start("model")
                        for part in model_turn.parts:
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data and getattr(inline_data, "data", None):
                                raw_audio = inline_data.data
                                if self.on_audio_chunk and raw_audio:
                                    self.on_audio_chunk(raw_audio)

                    # 3. Транскрипция речи пользователя (дельта-токены)

                    # 4. Транскрипция ответа модели (дельта-токены)
                    out_trans = getattr(server_content, "output_transcription", None)
                    if out_trans and getattr(out_trans, "text", None):
                        delta = out_trans.text
                        if delta:
                            self._handle_model_delta(delta)

                    # 5. Завершение реплики
                    if getattr(server_content, "turn_complete", False):
                        if not self._activity_open:
                            self._finalize_turn("user")
                        if self._current_speaker == "model" or self._current_model_text.strip():
                            self._finalize_turn("model")
                        elif self._current_speaker == "user" or self._current_user_text.strip():
                            self._finalize_turn("user")
                        if self.on_response_complete:
                            self.on_response_complete()
                if not received and self._running:
                    raise RuntimeError("Gemini Live stream closed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    raise RuntimeError("Gemini Live disconnected") from e

    def _handle_user_delta(self, delta: str) -> None:
        """Агрегирует поток распознавания речи пользователя в единую реплику."""
        if self._current_speaker != "user":
            # Смена хода: закрываем предыдущую реплику модели
            self._current_speaker = "user"
            if self.on_turn_start:
                self.on_turn_start("user")

        self._current_user_text += delta
        if self.on_transcript_delta:
            self.on_transcript_delta("user", delta, self._current_user_text)

    def _handle_model_delta(self, delta: str) -> None:
        """Агрегирует поток генерации ответа модели в единую реплику."""
        if self._current_speaker != "model":
            # Смена хода: закрываем предыдущую реплику пользователя
            if self._current_user_text.strip() and not self._activity_open:
                self._finalize_turn("user")
            self._current_speaker = "model"
            if self.on_turn_start:
                self.on_turn_start("model")

        self._current_model_text += delta
        if self.on_transcript_delta:
            self.on_transcript_delta("model", delta, self._current_model_text)

    def _finalize_turn(self, speaker: str) -> None:
        """Фиксирует законченную реплику в истории и уведомляет UI."""
        if speaker == "user" and self._current_user_text.strip():
            text = self._current_user_text.strip()
            self.transcript_history.append({"role": "user", "text": text})
            if self.on_turn_complete:
                self.on_turn_complete("user", text)
            self._current_user_text = ""
            if self._current_speaker == "user":
                self._current_speaker = None

        elif speaker == "model":
            text = self._current_model_text.strip()
            if text:
                self.transcript_history.append({"role": "model", "text": text})
                if self.on_turn_complete:
                    self.on_turn_complete("model", text)
            self._current_model_text = ""
            self._current_speaker = None

    def stop(self) -> None:
        """Останавливает сессию и фиксирует незавершенные реплики."""
        self._running = False
        if self._user_interim:
            self._current_user_text += self._user_interim
            self._user_interim = ""
        self._finalize_turn("user")
        self._finalize_turn("model")
