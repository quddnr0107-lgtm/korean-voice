"""Independent synthetic regression tests for cache QA and child process scope.
No model inference, actual media, operational keys, or network is used.
"""
import importlib.util,json,os,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

class ReviewContract(unittest.TestCase):
    def setup_engine(self,root,*,bad_cache=False):
        path=Path(__file__).with_name('audio_engine.py')
        with patch.dict(os.environ,{'LECTURE_WORKDIR':str(root),'LECTURE_MODEL_ROOT':str(root/'unused-model')}):
            spec=importlib.util.spec_from_file_location('synthetic_review_engine',path);a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
        for d in ['cache','audio','metadata','progress']:(root/d).mkdir()
        a.np=np;a.sr=1000;y=np.ones(100 if bad_cache else 1000,dtype=np.float32)*.1
        def write(path,*args,**kwargs):Path(path).write_bytes(b'synthetic-wav')
        a.sf=types.SimpleNamespace(read=lambda *args,**kwargs:(y.copy(),1000),write=write)
        class TTS:
            def _infer(self,*args,**kwargs):raise RuntimeError('synthetic cache regeneration reached')
        a.tts=TTS();a.style=None
        text='합성 검증을 위한 가상의 문장입니다.';speed,gain=a.policy(text)
        key=a.sha(a.encoded({'text':text,'recipe':a.RECIPE,'speed':speed,'gain':gain}));wav=root/'cache'/f'{key}.wav';wav.write_bytes(b'synthetic-cache-wave')
        cm={'text':text,'key':key,'attempts':1,'metrics':a.metrics(y),'wav_sha256':a.sha(wav.read_bytes()),'recipe':a.RECIPE}
        if bad_cache:cm.update(text='다른 가상 문장',key='0'*64,recipe={'id':'wrong-recipe'})
        wav.with_suffix('.json').write_text(json.dumps(cm))
        lesson={'id':'TB-001','title':'가상 시험','chunks':[{'text':text,'spoken_text':text}]}
        def run(cmd,**kwargs):Path(cmd[-1]).write_bytes(b'synthetic-mp3')
        return a,lesson,run,(y*.86/.1).astype('<f4').tobytes()
    def test_source_chunk_bound_leaves_pause_headroom(self):
        with tempfile.TemporaryDirectory() as td:
            a,lesson,run,decoded=self.setup_engine(Path(td))
            m={'finite':True,'clipped_samples':0,'duration_s':55,'max_silence_s':0,'rms':.1}
            self.assertEqual(a.quality_flags(m,'가'*100),[])
            m['duration_s']=55.0001;self.assertIn('unusually_long',a.quality_flags(m,'가'*100))
    def test_invalid_cached_chunk_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            a,lesson,run,decoded=self.setup_engine(Path(td),bad_cache=True)
            with patch.object(a.subprocess,'run',side_effect=run),patch.object(a.subprocess,'check_output',return_value=decoded):
                # Regeneration may fail in the stub, but invalid cached audio must
                # not be accepted as a completed valid output.
                with self.assertRaises((ValueError,RuntimeError,AssertionError)):a.render_lesson(lesson)
    def test_ffmpeg_children_have_explicit_clean_environment(self):
        with tempfile.TemporaryDirectory() as td:
            a,lesson,run,decoded=self.setup_engine(Path(td))
            with patch.dict(os.environ,{'GH_TOKEN':'synthetic-token-never-forward','UNRELATED_SECRET':'synthetic-secret'}),patch.object(a.subprocess,'run',side_effect=run) as p,patch.object(a.subprocess,'check_output',return_value=decoded) as q:
                a.render_lesson(lesson)
            for call in [p.call_args,q.call_args]:
                self.assertIn('env',call.kwargs)
                self.assertNotIn('GH_TOKEN',call.kwargs['env'])
                self.assertNotIn('UNRELATED_SECRET',call.kwargs['env'])
if __name__=='__main__':unittest.main()
