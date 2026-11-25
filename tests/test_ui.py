from http.client import HTTPConnection
from http.server import HTTPServer
import json
from threading import Thread
import unittest

from rp2cc.ui import Handler, MAX_INPUT


class WorkbenchTests(unittest.TestCase):
    def test_local_server(self):
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            server.timeout = .2
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                def request(method, route, body=None, headers=None):
                    conn.request(method, route, body=body, headers=headers or {})
                    reply = conn.getresponse()
                    return reply.status, reply.read()

                status, page = request('GET', '/')
                self.assertEqual(status, 200)
                self.assertIn('数学内核工作台', page.decode())
                status, encoded = request('GET', '/api/examples')
                self.assertEqual(status, 200)
                headers = {'Content-Type': 'text/plain; charset=utf-8'}
                source = json.loads(encoded)['jump']['source'].encode()
                status, encoded = request('POST', '/api/compile', source, headers)
                self.assertEqual(status, 200, encoded)
                result = json.loads(encoded)
                self.assertNotEqual(result['baseline'], result['optimized'])
                self.assertIn('rp2cc_affine_iter_ref(v_x, UINT32_C(0x00000101)', result['baseline'])
                self.assertIn('0x46a65a0d', result['optimized'].lower())
                self.assertEqual(result['certificate']['mode'], 'optimized')
                self.assertEqual(request('POST', '/api/compile', b'import os\n', headers)[0], 422)
                # Probe header-level rejections without sending a body the server will not read.
                # Some TCP stacks reset the connection when unread body bytes arrive at close.
                self.assertEqual(request('POST', '/api/compile', None,
                                         {**headers, 'Content-Length': str(MAX_INPUT + 1)})[0], 413)
                self.assertEqual(request('POST', '/api/compile', None,
                                         {**headers, 'Origin': 'https://example.org'})[0], 403)
            finally:
                conn.close()
                server.shutdown()
                thread.join(timeout=5)
