"""Regression checks. No network calls, microphone use or personal-note writes."""
import asyncio
import datetime as dt
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import config
import live_client
import memory_engine
import obsidian_exporter
import web_app


class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    def tutor(self, **kwargs):
        return live_client.GeminiLiveTutor(api_key="test-key", **kwargs)

    async def test_fragments_are_one_turn_and_empty_model_is_not_invented(self):
        completed = []
        tutor = self.tutor(on_turn_complete=lambda *args: completed.append(args))
        tutor._handle_user_delta("I work ")
        tutor._handle_user_delta("on interfaces.")
        tutor._handle_model_delta("Tell me ")
        tutor._handle_model_delta("more.")
        tutor._finalize_turn("model")
        tutor._finalize_turn("model")
        self.assertEqual(completed, [("user", "I work on interfaces."), ("model", "Tell me more.")])

    async def test_failed_text_is_not_recorded_as_delivered(self):
        tutor = self.tutor()
        tutor._running = True
        tutor._session_ready.set()
        tutor._session = NS(send_client_content=AsyncMock(side_effect=OSError()))
        with self.assertRaises(RuntimeError):
            await tutor.handle_user_text_turn("Hello")
        self.assertEqual(tutor.transcript_history, [])

    async def test_coincident_input_and_model_audio_preserve_order(self):
        tutor = self.tutor()
        tutor._running = True
        responses = [NS(server_content=NS(input_transcription=NS(text="Hello there."),
            model_turn=NS(parts=[NS(inline_data=NS(data=b"00"))]),
            output_transcription=NS(text="Hi!"), turn_complete=True))]
        class Session:
            async def receive(self):
                for response in responses:
                    yield response
                tutor._running = False
        await tutor._receive_loop(Session())
        self.assertEqual(tutor.transcript_history, [{"role":"user","text":"Hello there."}, {"role":"model","text":"Hi!"}])

    async def test_receive_failure_is_propagated(self):
        tutor = self.tutor(); tutor._running = True
        class Session:
            async def receive(self):
                raise OSError("disconnected")
                yield
        with self.assertRaises(RuntimeError):
            await tutor._receive_loop(Session())

    async def test_coaching_rejects_invented_quotations(self):
        generated = {"corrections":[{"original":"I has","correction":"I have","rule":"Agreement"}],
            "upgrades":[{"original":"I work on design.","better":"I design interfaces.","context":"More specific"}], "takeaway":"Be specific."}
        client = NS(aio=NS(models=NS(generate_content=AsyncMock(return_value=NS(text=json.dumps(generated))))))
        data = await web_app.analyze_user_turn_coaching("I work on design.",client)
        self.assertEqual(data["corrections"],[])
        self.assertEqual(len(data["upgrades"]),1)


class NoteTests(unittest.TestCase):
    def test_library_ignores_placeholder_and_prefers_live_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'English_Session_2026-10-13_12-30.md'
            path.write_text('## ⚠️ Corrections\n| What Learner Said | Correct | Rule |\n| --- | --- | --- |\n| No grammatical errors present | N/A | Nothing to correct |\n', encoding='utf-8')
            with patch.object(memory_engine, 'get_session_files', return_value=[path]):
                self.assertEqual(memory_engine.get_learning_library()['sessions'][0]['corrections'], [])
                path.write_text('## ⚠️ Corrections\n| What Learner Said | Correct | Rule |\n| --- | --- | --- |\n| Hello | Greetings | Optional style |\n<!-- tutor-feedback\n[]\n-->\n', encoding='utf-8')
                data = memory_engine.get_learning_library()['sessions'][0]
                self.assertEqual(data['corrections'], [])
                self.assertEqual(data['time'], '12:30')

    def test_live_feedback_survives_summary_failure_and_returns_in_memory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, "OBSIDIAN_NOTES_DIR", Path(directory)), patch.object(obsidian_exporter, "generate_session_summary", side_effect=OSError("offline")):
            now = dt.datetime.now()
            feedback = [{"corrections": [{"original": "I go yesterday", "correction": "I went yesterday", "rule": "Past simple"}], "takeaway": "went is the past of go"}]
            path = obsidian_exporter.save_session_note([{"role": "user", "text": "I go yesterday"}, {"role": "model", "text": "Use went for the past."}], now, now, "B1+", feedback)
            saved = path.read_text(encoding="utf-8")
            self.assertIn("Работа над ошибками", saved)
            self.assertIn("Use went for the past.", saved)
            context = memory_engine.get_cross_session_context()
            self.assertIn("I went yesterday", context)
            self.assertIn("went is the past of go", context)
            self.assertIn("Use went for the past.", context)

    def test_transcript_exists_before_summary_and_fallback_survives(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config,"OBSIDIAN_NOTES_DIR",Path(directory)):
            def failure(*args):
                existing = list(Path(directory).glob('*.md'))
                self.assertEqual(len(existing),1)
                self.assertIn("My original words",existing[0].read_text(encoding="utf-8"))
                raise OSError("offline")
            with patch.object(obsidian_exporter,"generate_session_summary",side_effect=failure):
                now = dt.datetime.now()
                path = obsidian_exporter.save_session_note([{"role":"user","text":"My original words"}],now,now)
            self.assertIn("My original words",path.read_text(encoding="utf-8"))

    def test_same_second_sessions_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config,"OBSIDIAN_NOTES_DIR",Path(directory)), patch.object(obsidian_exporter,"generate_session_summary",return_value="# Notes"):
            now = dt.datetime.now()
            paths = [obsidian_exporter.save_session_note([{"role":"user","text":"Hello"}],now,now) for _ in range(2)]
            self.assertNotEqual(paths[0],paths[1])
            self.assertTrue(all("Original transcript" in p.read_text(encoding="utf-8") for p in paths))

    def test_empty_history_has_no_fabricated_progress(self):
        with patch.object(memory_engine,"get_session_files",return_value=[]):
            self.assertEqual(memory_engine.get_memory_stats(),{"total_sessions":0,"recent_topics":[],"weak_points":[],"recent_vocab":[]})

    def test_heading_levels_and_table_separators(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "English_B2_Session_test.md"
            path.write_text("## 📌 Vocabulary\n| Term | Meaning |\n| --- | --- |\n| buy-in | support |\n## ⚠️ Corrections\nSome text\n",encoding="utf-8")
            self.assertEqual(memory_engine.parse_session_note(path)["vocab"],["buy-in — support"])


class WebTests(unittest.TestCase):
    def test_trash_restore_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'OBSIDIAN_NOTES_DIR', Path(directory) / 'English'), TestClient(web_app.app) as client:
            config.OBSIDIAN_NOTES_DIR.mkdir()
            path = config.OBSIDIAN_NOTES_DIR / 'English_Session_delete_test.md'
            original = '## Original transcript\nLearner: exact original words\n'
            path.write_text(original, encoding='utf-8')
            note_id = memory_engine.session_id(path)
            headers = {'X-Sarah-Archive': '1', 'Origin': 'http://testserver'}
            self.assertEqual(client.delete('/api/lessons/' + note_id).status_code, 403)
            self.assertEqual(client.delete('/api/lessons/' + note_id, headers={**headers, 'Origin':'https://other.test'}).status_code, 403)
            deleted = client.delete('/api/lessons/' + note_id, headers=headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(path.exists())
            self.assertEqual(client.get('/api/lessons').json()['sessions'], [])
            self.assertNotIn('exact original words', memory_engine.get_cross_session_context())
            trash_id = client.get('/api/lesson-trash').json()['sessions'][0]['id']
            self.assertEqual(client.delete('/api/lessons/' + note_id, headers=headers).status_code, 404)
            path.write_text('new content', encoding='utf-8')
            endpoint = '/api/lesson-trash/' + trash_id + '/restore'
            self.assertEqual(client.post(endpoint, headers=headers).status_code, 409)
            self.assertEqual(path.read_text(encoding='utf-8'), 'new content')
            path.unlink()  # Test-only temporary fixture.
            self.assertEqual(client.post(endpoint, headers=headers).status_code, 200)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
            self.assertEqual(client.get('/api/lesson-trash').json()['sessions'], [])
            self.assertEqual(client.post('/api/lesson-trash/unknown/restore', headers=headers).status_code, 404)

    def test_lesson_archive_reads_only_known_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / 'English_Session_example.md'
            note.write_text('---\ndate: 2026-10-13\n---\n## 📌 Vocabulary\n| Term | Meaning |\n| --- | --- |\n| went | ходил |\n## ⚠️ Corrections\n| What Learner Said | Correct | Rule |\n| --- | --- | --- |\n| I go yesterday | I went yesterday | Past simple |', encoding='utf-8')
            with patch.object(memory_engine, 'get_session_files', return_value=[note]), TestClient(web_app.app) as client:
                sessions = client.get('/api/lessons').json()['sessions']
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]['corrections'][0]['correction'], 'I went yesterday')
                content = client.get('/api/lessons/' + sessions[0]['id'])
                self.assertEqual(content.status_code, 200)
                self.assertIn('went', content.json()['markdown'])
                self.assertEqual(client.get('/api/lessons/unknown').status_code, 404)
                self.assertEqual(client.get('/api/lessons/.env').status_code, 404)
                self.assertNotIn(str(root), str(sessions))

    def test_static_assets_and_safe_status(self):
        with TestClient(web_app.app) as client:
            self.assertEqual(client.get('/').status_code,200)
            self.assertEqual(client.get('/static/pcm-worklet.js').status_code,200)
            self.assertNotIn('api_key',client.get('/api/status').text.lower())

    def test_cross_origin_socket_is_rejected(self):
        with TestClient(web_app.app) as client:
            with self.assertRaises(web_app.WebSocketDisconnect):
                with client.websocket_connect('/ws/voice',headers={"origin":"https://unrelated.example"}):
                    self.fail("Cross-origin socket accepted")

    def test_missing_key_reports_error_without_a_note(self):
        with patch.object(config,"validate_api_key",side_effect=ValueError()), patch.object(obsidian_exporter,"save_session_note") as save:
            with TestClient(web_app.app) as client, client.websocket_connect('/ws/voice') as ws:
                self.assertEqual(ws.receive_json()["type"],"error")
            save.assert_not_called()

    def test_text_coached_once_and_summary_delivered(self):
        class Tutor:
            def __init__(self, **callbacks):
                self.cb=callbacks; self.transcript_history=[]
            async def run(self, stream):
                self.cb['on_ready']()
                await asyncio.Event().wait()
            async def handle_user_text_turn(self,text):
                self.transcript_history.append({"role":"user","text":text})
                self.cb['on_turn_complete']('user',text)
            async def end_audio_stream(self): pass
            def stop(self): pass
        coaching = AsyncMock(return_value={"corrections":[],"upgrades":[],"takeaway":""})
        with tempfile.TemporaryDirectory() as directory:
            note = Path(directory)/'test.md'; note.write_text('# Test notes',encoding='utf-8')
            with patch.object(config,'GEMINI_API_KEY','test'), patch.object(config,'validate_api_key',return_value='test'), patch.object(web_app,'GeminiLiveTutor',Tutor), patch.object(web_app,'analyze_user_turn_coaching',coaching), patch.object(obsidian_exporter,'save_session_note',return_value=note):
                with TestClient(web_app.app) as client, client.websocket_connect('/ws/voice') as ws:
                    self.assertEqual(ws.receive_json()['type'],'ready')
                    ws.send_json({'type':'user_turn','text':'Hello'})
                    self.assertEqual(ws.receive_json()['type'],'turn_complete')
                    self.assertEqual(ws.receive_json()['type'],'live_coaching')
                    ws.send_json({'type':'end_session'})
                    self.assertEqual(ws.receive_json()['type'],'saving')
                    self.assertEqual(ws.receive_json()['markdown'],'# Test notes')
                self.assertEqual(coaching.await_count,1)


if __name__ == '__main__':
    unittest.main()
