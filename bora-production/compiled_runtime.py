"""Fixed compiled-pilot entry point with the same sterile failure logging."""
from pathlib import Path
import json
import sys
import traceback

import runtime


if __name__ == '__main__':
    try:
        runtime.main(profile='compiled')
    except BaseException as exc:
        locations = [dict(file=Path(f.filename).name, line=f.lineno)
            for f in traceback.extract_tb(exc.__traceback__)
            if Path(f.filename).parent == runtime.HERE]
        print(json.dumps(dict(status='failed', error_type=type(exc).__name__,
            locations=locations[-6:])), flush=True)
        sys.exit(1)
