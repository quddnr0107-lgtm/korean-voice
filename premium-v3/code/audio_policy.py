"""Track-specific total bounds and measured PCM/decoded timeline consistency."""
import math
LIMITS={'easy':900.0,'source':1800.0}
def duration_ok(track,duration):
 if track not in LIMITS or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not 20<float(duration)<LIMITS[track]:raise ValueError('track duration bound')
 return True

def validate_timeline(track,segments,decoded):
 duration_ok(track,decoded)
 if not segments or len(segments)>=150:raise ValueError('timeline count')
 previous=0.0;total=0.0
 for i,s in enumerate(segments):
  a,b=s['start_s'],s['end_s']
  if s['index']!=i or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (a,b)) or b-a<=.15:raise ValueError('chunk duration')
  expected=0.0 if i==0 else previous+.48
  if abs(a-expected)>.0001:raise ValueError('measured gap')
  total+=b-a;previous=b
 expected=total+.48*(len(segments)-1)
 if abs(previous-expected)>.001 or abs(decoded-expected)>.15:raise ValueError('PCM decoded duration mismatch')
 return {'pcm_timeline_s':round(expected,5),'decoded_duration_s':decoded,'decode_delta_s':round(decoded-expected,6),'duration_limit_s':LIMITS[track]}
