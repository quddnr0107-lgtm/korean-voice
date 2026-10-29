import unittest
from namespace_guard import validate_snapshot,HEADER
class NamespaceGuard(unittest.TestCase):
 def snapshot(self,**kw):
  x=dict(uid=1001,expected_uid=1001,interfaces=[(1,'lo')],routes='',namespace='net:[2]',host_namespace='net:[1]');x.update(kw);return validate_snapshot(**x)
 def test_empty_route_file(self):self.assertEqual(self.snapshot()['ipv4_routes'],0)
 def test_header_only(self):self.assertEqual(self.snapshot(routes='\t'.join(HEADER)+'\n')['ipv4_routes'],0)
 def test_blank_lines(self):self.assertEqual(self.snapshot(routes='\n \n')['ipv4_routes'],0)
 def test_uid_mismatch(self):
  with self.assertRaises(ValueError):self.snapshot(uid=1002)
 def test_root(self):
  with self.assertRaises(ValueError):self.snapshot(uid=0,expected_uid=0)
 def test_same_namespace(self):
  with self.assertRaises(ValueError):self.snapshot(namespace='net:[1]')
 def test_invalid_namespace(self):
  with self.assertRaises(ValueError):self.snapshot(namespace='')
 def test_extra_interface(self):
  with self.assertRaises(ValueError):self.snapshot(interfaces=[(1,'lo'),(2,'eth0')])
 def test_missing_interface(self):
  with self.assertRaises(ValueError):self.snapshot(interfaces=[])
 def test_real_route(self):
  with self.assertRaises(ValueError):self.snapshot(routes='\t'.join(HEADER)+'\neth0\t00000000\t00000000\t0001\t0\t0\t0\t00000000\t0\t0\t0\n')
 def test_headerless_route(self):
  with self.assertRaises(ValueError):self.snapshot(routes='eth0 00000000 00000000')
 def test_malformed_header(self):
  with self.assertRaises(ValueError):self.snapshot(routes='Iface Destination')

 def test_ipv6_unreachable_placeholder(self):
  self.snapshot(ipv6_routes='0'*32+' 00 '+'0'*32+' 00 '+'0'*32+' ffffffff 00000001 00000000 00200200 lo')
 def test_ipv6_up_route(self):
  with self.assertRaises(ValueError):self.snapshot(ipv6_routes='0'*32+' 00 '+'0'*32+' 00 '+'0'*32+' 00000000 00000001 00000000 00000001 lo')
 def test_ipv6_external_interface(self):
  with self.assertRaises(ValueError):self.snapshot(ipv6_routes='0'*32+' 00 '+'0'*32+' 00 '+'0'*32+' ffffffff 00000001 00000000 00200200 eth0')
 def test_ipv6_malformed(self):
  with self.assertRaises(ValueError):self.snapshot(ipv6_routes='bad')
