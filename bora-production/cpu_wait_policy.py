"""Select only the measured CPU wait-hint bundle before native libraries load.

All observed model names must exactly match the measured AMD EPYC 7763 model.
Other or unknown CPUs retain the original environment. A preexisting parent
wait policy is preserved unchanged; only metadata records it as inherited.
This helper never clears, blanks or overrides a parent policy. No speed guarantee
is implied.
"""
import json
import os
from pathlib import Path


TARGET_MODEL = 'AMD EPYC 7763 64-Core Processor'
WAIT_HINTS = {'OMP_WAIT_POLICY': 'PASSIVE', 'GOMP_SPINCOUNT': '0', 'KMP_BLOCKTIME': '0'}
PRIVATE_KEYS = ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')


def cpu_models(cpuinfo):
    models = set()
    for line in cpuinfo.splitlines():
        name, separator, value = line.partition(':')
        if separator and name.strip() == 'model name':
            models.add(value.strip())
    return models


def environment_lines(cpuinfo, environ):
    if any(name in environ for name in PRIVATE_KEYS):
        raise RuntimeError('cpu_wait_selector_requires_keyless_environment')
    if any(name in environ for name in WAIT_HINTS):
        return 'inherited', 'BORA_CPU_WAIT_POLICY=inherited\n'
    policy = 'passive' if cpu_models(cpuinfo) == {TARGET_MODEL} else 'default'
    exports = dict(WAIT_HINTS) if policy == 'passive' else {}
    exports['BORA_CPU_WAIT_POLICY'] = policy
    return policy, ''.join(name+'='+value+'\n' for name, value in exports.items())


def main():
    # Missing CPU information safely selects the unchanged default environment.
    try:
        cpuinfo = Path('/proc/cpuinfo').read_text()
    except OSError:
        cpuinfo = ''
    policy, lines = environment_lines(cpuinfo, os.environ)
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write(lines)
    print(json.dumps({'cpu_wait_policy': policy, 'cpu_models': sorted(cpu_models(cpuinfo)),
                      'performance_guaranteed': False}))


if __name__ == '__main__':
    main()
