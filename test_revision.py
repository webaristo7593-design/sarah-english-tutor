"""Acceptance checks for manual turns, bilingual learning and level presets."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import unittest
from google.genai import types
import system_prompt
from live_client import GeminiLiveTutor
from transcription import LiveCaptions
import web_app


class RevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_activity_start_interrupts_and_only_send_ends_turn(self):
        tutor=GeminiLiveTutor(api_key='test',manual_turns=True)
        tutor._running=True
        tutor._session=NS(send_realtime_input=AsyncMock())
        await tutor.begin_audio_turn()
        await tutor.begin_audio_turn()
        calls=tutor._session.send_realtime_input.call_args_list
        self.assertEqual(len(calls),1)
        self.assertIsInstance(calls[0].kwargs['activity_start'],types.ActivityStart)
        self.assertTrue(tutor._activity_open)
        await tutor.end_audio_stream()
        await tutor.end_audio_stream()
        calls=tutor._session.send_realtime_input.call_args_list
        self.assertEqual(len(calls),2)
        self.assertIn('activity_end',calls[1].kwargs)
        self.assertNotIn('audio_stream_end',calls[1].kwargs)

    async def test_manual_config_and_bilingual_default(self):
        tutor=GeminiLiveTutor(api_key='test',manual_turns=True)
        with patch.object(system_prompt.memory_engine,'get_cross_session_context',return_value=''):
            config=tutor._build_config()
        self.assertTrue(config.realtime_input_config.automatic_activity_detection.disabled)
        self.assertEqual(config.realtime_input_config.activity_handling,types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS)
        self.assertIsNone(config.input_audio_transcription.language_codes)
        prompt=config.system_instruction.parts[0].text
        self.assertIn('TARGET LEVEL: B1+',prompt)
        self.assertIn('explain in Russian',prompt)
        self.assertIn('Do not assume his current level',prompt)

    async def test_each_preset_changes_prompt(self):
        with patch.object(system_prompt.memory_engine,'get_cross_session_context',return_value=''):
            for level, guidance in system_prompt.LEVEL_PRESETS.items():
                prompt=system_prompt.get_system_instruction(level)
                self.assertIn('TARGET LEVEL: '+level,prompt)
                self.assertIn(guidance,prompt)
            self.assertIn('TARGET LEVEL: B1+',system_prompt.get_system_instruction('invalid'))

    async def test_interim_is_replaced_not_committed_twice(self):
        previews=[]
        tutor=GeminiLiveTutor(api_key='test',manual_turns=True,on_transcript_delta=lambda r,d,t:previews.append((r,t)))
        tutor._running=True; tutor._activity_open=True
        responses=[NS(server_content=NS(interim_input_transcription=NS(text=text))) for text in ['I went','I went to the park']]
        responses.append(NS(server_content=NS(input_transcription=NS(text='I went to the park.',finished=True))))
        class Session:
            async def receive(self):
                for item in responses: yield item
                tutor._running=False
        await tutor._receive_loop(Session())
        self.assertEqual(previews[-1],('user','I went to the park.'))
        self.assertEqual(tutor.transcript_history,[])
        tutor.stop()
        self.assertEqual(tutor.transcript_history,[{'role':'user','text':'I went to the park.'}])

    async def test_coaching_receives_level_and_russian_guidance(self):
        generate=AsyncMock(return_value=NS(text='{"corrections":[],"upgrades":[],"takeaway":""}'))
        await web_app.analyze_user_turn_coaching('Объясни по-русски',NS(aio=NS(models=NS(generate_content=generate))),'B1+')
        prompt=generate.call_args.kwargs['contents']
        self.assertIn('selected target is B1+',prompt)
        self.assertIn('entirely Russian clarification',prompt)

    async def test_caption_session_is_scoped_and_cancelled_on_new_turn(self):
        events=[]
        captions=LiveCaptions(lambda *a,**kw:events.append((a,kw)))
        cancelled=[]
        async def run(turn_id,queue):
            try: await asyncio.Event().wait()
            finally: cancelled.append(turn_id)
        with patch.object(captions,'_run',side_effect=run):
            await captions.begin(1);await asyncio.sleep(0)
            await captions.begin(2);await asyncio.sleep(0)
            self.assertEqual(cancelled,[1])
            await captions.close()
            self.assertEqual(cancelled,[1,2])


if __name__=='__main__': unittest.main()
