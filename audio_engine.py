"""
Низкоуровневый звуковой движок на базе sounddevice и numpy.
Обеспечивает полнодуплексный ввод-вывод аудио с защитой от задержек
и мгновенным сбросом буфера при перебивании модели (Interruption Handling).
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

import config


class SoundDeviceAudioEngine:
    """
    Управляет захватом звука с микрофона (16kHz mono)
    и воспроизведением через динамики (24kHz mono) в асинхронном цикле.
    """

    def __init__(
        self,
        input_rate: int = config.INPUT_SAMPLE_RATE,
        output_rate: int = config.OUTPUT_SAMPLE_RATE,
        block_size: int = config.AUDIO_CHUNK_SIZE,
    ) -> None:
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.block_size = block_size

        self.input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._input_stream: Optional[sd.RawInputStream] = None
        self._output_stream: Optional[sd.RawOutputStream] = None

        # Буфер воспроизведения (защищен threading.Lock)
        self._playback_lock = threading.Lock()
        self._playback_buffer = bytearray()

        # Метрики
        self.current_mic_rms: float = 0.0
        self.is_playing: bool = False
        self._running: bool = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Запускает потоки ввода и вывода."""
        self._loop = loop
        self._running = True

        # Входной поток (микрофон 16kHz)
        self._input_stream = sd.RawInputStream(
            samplerate=self.input_rate,
            channels=1,
            dtype="int16",
            blocksize=self.block_size,
            callback=self._mic_callback,
        )

        # Выходной поток (динамики 24kHz)
        self._output_stream = sd.RawOutputStream(
            samplerate=self.output_rate,
            channels=1,
            dtype="int16",
            blocksize=self.block_size,
            callback=self._speaker_callback,
        )

        self._input_stream.start()
        self._output_stream.start()

    def _mic_callback(self, indata, frames, time_info, status) -> None:
        """Callback звуковой карты для записи с микрофона."""
        if not self._running:
            return

        raw_bytes = bytes(indata)

        # Расчет среднеквадратичной громкости (RMS)
        try:
            samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
            if len(samples) > 0:
                self.current_mic_rms = float(np.sqrt(np.mean(np.square(samples))))
        except Exception:
            pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.input_queue.put_nowait, raw_bytes)

    def _speaker_callback(self, outdata, frames, time_info, status) -> None:
        """Callback звуковой карты для вывода звука модели в динамики."""
        bytes_needed = frames * 2  # 16-bit mono = 2 байта на семпл

        with self._playback_lock:
            # Если в буфере недостаточно данных, пытаемся подтянуть из очереди
            while len(self._playback_buffer) < bytes_needed:
                try:
                    chunk = self.output_queue.get_nowait()
                    self._playback_buffer.extend(chunk)
                except (asyncio.QueueEmpty, AttributeError):
                    break

            available = len(self._playback_buffer)
            if available >= bytes_needed:
                outdata[:bytes_needed] = self._playback_buffer[:bytes_needed]
                del self._playback_buffer[:bytes_needed]
                self.is_playing = True
            elif available > 0:
                outdata[:available] = self._playback_buffer[:available]
                outdata[available:] = b"\x00" * (bytes_needed - available)
                self._playback_buffer.clear()
                self.is_playing = False
            else:
                outdata[:] = b"\x00" * bytes_needed
                self.is_playing = False

    def enqueue_audio_chunk(self, chunk: bytes) -> None:
        """Добавляет чанк от Gemini Live в очередь воспроизведения."""
        if self._running:
            self.output_queue.put_nowait(chunk)

    def flush_playback(self) -> None:
        """
        Мгновенно очищает воспроизведение звука при перебивании (Interruption).
        Вызывается, когда Gemini шлет content.interrupted = True или пользователь начал говорить.
        """
        with self._playback_lock:
            self._playback_buffer.clear()

        # Очищаем асинхронную очередь чанков
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except (asyncio.QueueEmpty, AttributeError):
                break

        self.is_playing = False

    def stop(self) -> None:
        """Останавливает аудиопотоки и освобождает аудиоустройства."""
        self._running = False
        self.flush_playback()

        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        if self._output_stream:
            try:
                self._output_stream.stop()
                self._output_stream.close()
            except Exception:
                pass
            self._output_stream = None
