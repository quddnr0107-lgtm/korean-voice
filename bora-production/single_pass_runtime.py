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
        locations = [dict(file=Path(frame.filename).name, line=frame.lineno)
            for frame in traceback.extract_tb(exc.__traceback__)
            if Path(frame.filename).parent == runtime.HERE]
        print(json.dumps(dict(status='failed', error_type=type(exc).__name__,
            locations=locations[-6:])), flush=True)
        sys.exit(1)
