"""Fail-closed, keyless verification of a newly created offline network namespace."""
import argparse
import json
import os
import re
import socket
import sys
import runpy
from pathlib import Path

HEADER = ['Iface','Destination','Gateway','Flags','RefCnt','Use','Metric','Mask','MTU','Window','IRTT']
def validate_snapshot(uid, expected_uid, interfaces, routes, namespace, host_namespace, ipv6_routes=""):
    if type(uid) is not int or uid != expected_uid or uid == 0:
        raise ValueError('namespace UID mismatch')
    if not re.fullmatch(r'net:\[[0-9]+\]', namespace) or not re.fullmatch(r'net:\[[0-9]+\]',host_namespace) or namespace == host_namespace:
        raise ValueError('network namespace was not isolated')
    if len(interfaces) != 1 or interfaces[0][1] != 'lo':
        raise ValueError('non-loopback network interface')
    lines = [line.split() for line in routes.splitlines() if line.strip()]
    # Some hosted kernels return an empty route file, others include its header.
    # Neither is a route. Every actual route or unknown line remains an error.
    if lines not in ([], [HEADER]):
        raise ValueError('namespace IPv4 routes present or malformed')
    for line in ipv6_routes.splitlines():
        if not line.strip():continue
        fields=line.split()
        if len(fields)!=10 or fields[-1]!='lo' or not re.fullmatch('[0-9a-fA-F]{8}',fields[8]) or int(fields[8],16)&1:
            raise ValueError('usable IPv6 route or malformed route')
    return {'status':'offline_namespace_verified','uid':uid,'interfaces':['lo'],'ipv4_routes':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--uid',required=True,type=int);p.add_argument('--host-netns',required=True);p.add_argument('--render');a=p.parse_args()
    result=validate_snapshot(os.getuid(),a.uid,socket.if_nameindex(),Path('/proc/net/route').read_text(),os.readlink('/proc/self/ns/net'),a.host_netns,Path('/proc/net/ipv6_route').read_text())
    print(json.dumps(result),flush=True)
    if a.render is not None:
        from lecture_crypto import namespace_for
        namespace_for(a.render)
        target=str(Path(__file__).with_name('render_sealed.py'));sys.argv=[target,a.render]
        runpy.run_path(target,run_name='__main__')
if __name__=='__main__':main()
