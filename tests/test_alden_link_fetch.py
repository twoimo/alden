"""Public-network transport contracts using fake DNS, sockets and HTTP only."""
import socket
import ssl
import sys
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_link_fetch as fetch

def address(ip='8.8.8.8',port=443):return [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',(ip,port))]
class Response:
    def __init__(self,body=b'<p>73</p>',status=200,headers=None):self.body=body;self.status=status;self.headers=headers or {}
    def getheader(self,name):return self.headers.get(name)
    def read(self,n):value,self.body=self.body[:n],self.body[n:];return value
class Connection:
    def __init__(self):self.closed=False
    def close(self):self.closed=True

class LinkFetchTests(unittest.TestCase):
    def run_fetch(self,url='https://example.com/page',response=None,**kwargs):
        self.connection=Connection();self.opened=[]
        def opener(*args):self.opened.append(args);return self.connection,response or Response()
        return fetch.fetch_bytes(url,time.monotonic()+2,resolver=lambda host,port,timeout:address(port=port),opener=opener,**kwargs)
    def test_public_bytes_are_bounded_and_connection_closed(self):
        self.assertEqual(self.run_fetch(),(b'<p>73</p>',9));self.assertTrue(self.connection.closed)
    def test_local_credential_control_and_nonstandard_port_urls_never_connect(self):
        for url in ['http://127.0.0.1/','https://localhost/','http://host.local/','https://user:pass@example.com/',
                    'https://example.com:8000/','https://example.com/\r\nheader','https://example.com\\@localhost/',
                    'https://example.com/'+('x'*2048),'http://[::ffff:127.0.0.1]/']:
            with self.subTest(url=url):self.assertIsNone(self.run_fetch(url));self.assertEqual(self.opened,[])
    def test_mixed_public_private_dns_cannot_use_public_candidate(self):
        with patch.object(fetch,'open_pinned',side_effect=AssertionError('connected')):
            result=fetch.fetch_bytes('https://example.com/',time.monotonic()+2,
                resolver=lambda *args:address()+address('10.0.0.1'),opener=fetch.open_pinned)
        self.assertIsNone(result)
    def test_dns_shape_and_candidate_cap_are_validated(self):
        for records in [[(0,0,0,'',('8.8.8.8',443))],address()*33,[],address(port=80)]:
            with self.subTest(records=len(records)),self.assertRaises(OSError):
                fetch.fetch_bytes('https://example.com/',time.monotonic()+2,resolver=lambda *args:records,
                    opener=lambda *args:(_ for _ in ()).throw(AssertionError('opened')))
    def test_status_must_be_integer_2xx(self):
        for status in [None,True,'200',403,500]:
            with self.subTest(status=status),self.assertRaises(ValueError):self.run_fetch(response=Response(status=status))
            self.assertTrue(self.connection.closed)
    def test_compressed_body_is_not_silently_decompressed(self):
        with self.assertRaisesRegex(ValueError,'encoding'):self.run_fetch(response=Response(headers={'Content-Encoding':'gzip'}))
        self.assertTrue(self.connection.closed)
    def test_reader_overflow_and_deadline_are_enforced(self):
        with self.assertRaises(ValueError):self.run_fetch(response=Response(body=b'x'*1_000_001))
        self.assertTrue(self.connection.closed)
        with self.assertRaises(TimeoutError):fetch.read_body(Response(),time.monotonic()-1)
    def test_cancellation_propagates_and_closes_owned_connection(self):
        class Cancelled(RuntimeError):pass
        count=0
        def cancel():
            nonlocal count
            count+=1
            if count==4:raise Cancelled()
        with self.assertRaises(Cancelled):self.run_fetch(cancel=cancel)
        self.assertTrue(self.connection.closed)
    def test_default_redirect_policy_remains_kakao_daum_only(self):
        with self.assertRaisesRegex(ValueError,'link redirect not allowed'):
            self.run_fetch(response=Response(status=301,headers={'Location':'https://other.example/page'}))
        self.assertTrue(self.connection.closed)
    def test_allowed_redirect_revalidates_dns_and_records_hops(self):
        connections=[];hosts=[];trace=[]
        def opener(parsed,host,*args):
            hosts.append(host);connection=Connection();connections.append(connection)
            return connection,Response(status=302,headers={'Location':'https://next.example/page'}) if len(hosts)==1 else Response()
        result=fetch.fetch_bytes('https://example.com/',time.monotonic()+2,resolver=lambda *args:address(),opener=opener,
            allow_redirect=lambda *args:True,trace=trace)
        self.assertEqual(result,(b'<p>73</p>',9));self.assertEqual(hosts,['example.com','next.example']);self.assertTrue(all(c.closed for c in connections))
        self.assertEqual([r['status'] for r in trace],[302,200])
    def test_redirect_to_private_or_excess_hops_is_rejected(self):
        for location,remaining in [('http://127.0.0.1/',1),('https://next.example/',0)]:
            if remaining==0:
                with self.assertRaises(ValueError):self.run_fetch(response=Response(status=302,headers={'Location':location}),redirects_left=remaining,allow_redirect=lambda *args:True)
            else:self.assertIsNone(self.run_fetch(response=Response(status=302,headers={'Location':location}),allow_redirect=lambda *args:True))
            self.assertTrue(self.connection.closed)
    def test_unicode_path_is_encoded_preserving_existing_escapes(self):
        class FakeConnection(Connection):
            def __init__(self,*args,**kwargs):super().__init__();self.sock=None
            def request(self,method,target,headers):self.target=target;self.headers=headers
            def getresponse(self):return Response()
        with patch.object(fetch,'_PinnedHTTPSConnection',FakeConnection):
            c,_=fetch.open_pinned(urlsplit('https://example.com/\uD55C%20?q=\uAE00'),'example.com',443,address(),time.monotonic()+2)
        self.assertEqual(c.target,'/%ED%95%9C%20?q=%EA%B8%80');self.assertEqual(c.headers['Host'],'example.com')
    def test_tls_connects_numeric_address_and_checks_original_hostname(self):
        class Sock:
            def settimeout(self,value):pass
            def connect(self,value):self.connected=value
            def close(self):pass
        sock=Sock();context=ssl.create_default_context()
        c=fetch._PinnedHTTPSConnection('8.8.8.8',443,hostname='example.com',family=socket.AF_INET,
            protocol=socket.IPPROTO_TCP,sockaddr=('8.8.8.8',443),deadline=time.monotonic()+2,context=context)
        class Wrapper:
            def wrap_socket(self,sock,server_hostname):self.hostname=server_hostname;return sock
        wrapper=Wrapper();c._context=wrapper
        with patch.object(fetch.socket,'socket',return_value=sock):c.connect()
        self.assertEqual(sock.connected,('8.8.8.8',443));self.assertEqual(wrapper.hostname,'example.com')
        self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)

if __name__=='__main__':unittest.main()
