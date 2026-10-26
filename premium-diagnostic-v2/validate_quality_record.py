"""LOCAL PROPOSAL ONLY: exact numeric/boolean diagnostic record, no model/key work."""
import math
FIELDS={'chunk_index','attempts','sample_count','sample_rate','duration_s','all_finite','tail_hum','too_short'}
def validate_record(record):
 if type(record) is not dict or set(record)!=FIELDS:raise ValueError('diagnostic schema')
 for key in ('chunk_index','attempts','sample_count','sample_rate'):
  if type(record[key]) is not int:raise ValueError('diagnostic integer')
 for key in ('all_finite','tail_hum','too_short'):
  if type(record[key]) is not bool:raise ValueError('diagnostic boolean')
 if not 0<=record['chunk_index']<150 or record['attempts'] not in (1,2):raise ValueError('diagnostic bounds')
 n,sr=record['sample_count'],record['sample_rate']
 if not 0<=n<=2**31-1 or not 1<=sr<=192000:raise ValueError('diagnostic bounds')
 duration=record['duration_s']
 if type(duration) not in (int,float) or not math.isfinite(duration) or not math.isclose(duration,n/sr,rel_tol=1e-10,abs_tol=1e-10):raise ValueError('diagnostic duration')
 if record['too_short']!=(n<=.15*sr):raise ValueError('diagnostic predicate')
 if record['tail_hum'] and record['attempts']!=2:raise ValueError('hum loop evidence')
 if record['all_finite'] and not record['tail_hum'] and not record['too_short']:raise ValueError('no failed predicate')
 return {k:record[k] for k in sorted(FIELDS)}
