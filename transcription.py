"""Live captions only. Never sends recognized text back as a second user turn."""
import asyncio
from google import genai
from google.genai import types
import config


class LiveCaptions:
    def __init__(self, emit):
        self.emit = emit
        self.task = None
        self.queue = None
        self.turn_id = 0
        self.latest_text = ""

    async def begin(self, turn_id):
        await self.close()
        self.turn_id = turn_id
        self.latest_text = ""
        self.queue = asyncio.Queue(maxsize=100)
        self.task = asyncio.create_task(self._run(turn_id, self.queue))

    def push(self, chunk):
        if self.task and not self.task.done():
            try:
                self.queue.put_nowait(chunk)
            except asyncio.QueueFull:
                self.task.cancel()
                self.emit("caption_status", turn_id=self.turn_id,
                    text="Расшифровка не успевает. Итоговый текст появится после отправки.")

    def finish(self):
        self.push(None)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    async def _run(self, turn_id, queue):
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        parts = []
        finished = asyncio.Event()
        ending = False

        def caption(partial=""):
            self.latest_text = " ".join(parts + [partial]).strip()
            self.emit("speech_preview", turn_id=turn_id, text=self.latest_text)

        try:
            async with client.aio.live.connect(
                model=config.GEMINI_TRANSCRIBE_MODEL,
                config=types.LiveConnectConfig(response_modalities=["TEXT"],
                    input_audio_transcription=types.AudioTranscriptionConfig()),
            ) as session:
                async def receive():
                    while True:
                        any_response = False
                        async for response in session.receive():
                            any_response = True
                            content = response.server_content
                            if not content:
                                continue
                            interim = content.interim_input_transcription
                            final = content.input_transcription
                            if interim and interim.text:
                                caption(interim.text)
                            if final and final.text:
                                parts.append(final.text)
                                caption()
                            if ending and (content.turn_complete or (final and final.finished)):
                                finished.set()
                        if not any_response:
                            return

                receiver = asyncio.create_task(receive())
                try:
                    while True:
                        chunk = await queue.get()
                        if chunk is None:
                            ending = True
                            await session.send_realtime_input(audio_stream_end=True)
                            try:
                                await asyncio.wait_for(finished.wait(), timeout=3)
                            except asyncio.TimeoutError:
                                pass
                            break
                        await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
                        if receiver.done():
                            receiver.result()
                            break
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.emit("caption_status", turn_id=turn_id,
                text="Живая расшифровка недоступна. Итоговый текст появится после отправки.")
        finally:
            await client.aio.aclose()
            client.close()
