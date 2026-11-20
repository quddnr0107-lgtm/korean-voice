"""Fixed single-pass entry point with sterile failure logging."""
from pathlib import Path
import json
import sys
import traceback

import runtime


if __name__ == '__main__':
    try:
        runtime.main(profile='single_pass')
    except BaseException as exc:
        if '--diagnose-blocked-receipt' in sys.argv:
            lesson_id = next((lesson for lesson in ('MS-018', 'MS-022', 'MS-034')
                              if '--lesson-id' in sys.argv
                              and sys.argv[sys.argv.index('--lesson-id')+1:][:1] == [lesson]), None)
            category = 'diagnostic_failed'
            if lesson_id == 'MS-034' and type(exc).__name__ == 'AssemblyError':
                gate = exc.args
                if gate == ('long_silence_pcm',):
                    part = getattr(exc, 'part_silence_diagnostic', None)
                    if type(part) is dict:
                        origin = 'preexisting' if part['pre_run_frames'] >= 150 else 'codec_induced'
                        position = 'boundary' if part['gap_overlap_samples'] > 0 else 'within_unit'
                        category = 'silence_' + origin + '_' + position
                    else:
                        category = 'silence_unit'
                elif gate in (('part_decoded_sample_mismatch',), ('clipped_pcm',),
                              ('nonfinite_pcm',), ('silent_pcm',)):
                    category = gate[0]
            print(json.dumps(dict(status='receipt_diagnostic', lesson_id=lesson_id,
                                  category=category)), flush=True)
            sys.exit(1)
        locations = [dict(file=Path(frame.filename).name, line=frame.lineno)
            for frame in traceback.extract_tb(exc.__traceback__)
            if Path(frame.filename).parent == runtime.HERE]
        failure = dict(status='failed', error_type=type(exc).__name__, locations=locations[-6:])
        # Report only known fixed technical gate codes and a numeric position.
        if (type(exc).__name__ == 'AssemblyError'
                and exc.args in (('long_silence_pcm',), ('part_decoded_sample_mismatch',),
                                 ('clipped_pcm',), ('nonfinite_pcm',), ('silent_pcm',))):
            failure['gate'] = exc.args[0]
            if type(getattr(exc, 'unit_index', None)) is int:
                failure['unit_index'] = exc.unit_index
        if failure.get('gate') == 'long_silence_pcm':
            summary = getattr(exc, 'silence_aggregate', None)
            allowed = (
                'pre_gate_max_run_frames_20ms',
                'post_gate_max_run_frames_20ms',
                'post_strict_compactable_max_run_frames_20ms',
                'post_gate_low_peak_max_run_frames_20ms',
                'post_gate_quiet_frames_20ms',
                'post_pcm_frames_20ms',
                'removed_samples',
            )
            if (type(summary) is dict and set(summary) == set(allowed)
                    and all(type(summary[k]) is int and 0 <= summary[k] <= 10**9 for k in allowed)):
                # Only aggregate counters from the offline protected assembler.
                failure['silence_aggregate'] = {k: summary[k] for k in allowed}
        print(json.dumps(failure), flush=True)
        sys.exit(1)
