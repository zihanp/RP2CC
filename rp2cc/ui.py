"""Chinese, local-only workbench for the same compiler used by the CLI."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

from rp2cc.driver import compile_source
from tools.math_frontend import translate

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 16_384
EXAMPLES = {
    'jump': ('递推与双道取整', ROOT / 'examples/jump.rpm'),
    'affine': ('只做仿射递推', ROOT / 'examples/affine-only.rpm'),
    'pio': ('PIO 示例中的数学部分', ROOT / 'examples/pio-math/pairs.rpm'),
}


def compare(source_text: str) -> dict:
    """Return two C lowerings, using the actual parser, optimizer and checker."""
    if not source_text.strip():
        raise ValueError('请输入 .rpm 源码。')
    if len(source_text.encode('utf-8')) > MAX_INPUT:
        raise ValueError('工作台只接受不超过 16 KiB 的示例。')
    ir, _ = translate(source_text)
    _, baseline, _ = compile_source(ir, 'baseline')
    certificate, optimized, stats = compile_source(ir, 'optimized')
    return {
        'ir': ir, 'baseline': baseline['kernels.c'].decode('ascii'),
        'optimized': optimized['kernels.c'].decode('ascii'),
        'certificate': certificate, 'stats': stats,
    }


class Handler(BaseHTTPRequestHandler):
    def send_body(self, body: bytes, media_type: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', media_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=200):
        self.send_body(json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8', status)

    def do_GET(self):
        if self.path == '/':
            self.send_body(Path(__file__).with_name('ui.html').read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/api/examples':
            self.send_json({key: {'title': title, 'source': p.read_text(encoding='utf-8')}
                            for key, (title, p) in EXAMPLES.items()})
        else:
            self.send_json({'error': '页面不存在。'}, 404)

    def do_POST(self):
        if self.path != '/api/compile':
            return self.send_json({'error': '接口不存在。'}, 404)
        expected_host = f'127.0.0.1:{self.server.server_port}'
        if (self.headers.get('Host') != expected_host or
                self.headers.get('Origin') not in (None, 'http://' + expected_host) or
                self.headers.get_content_type() != 'text/plain'):
            return self.send_json({'error': '请从本地工作台提交源代码。'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_INPUT:
                return self.send_json({'error': '请输入不超过 16 KiB 的 .rpm 源码。'}, 413)
            source = self.rfile.read(length).decode('utf-8')
            return self.send_json(compare(source))
        except (ValueError, SyntaxError, RecursionError) as exc:
            return self.send_json({'error': f'无法编译：{exc}'}, 422)

    def log_message(self, *args):
        pass  # source code and requests are not logged or written to disk


def main():
    p = argparse.ArgumentParser(description='RP2CC 中文编译工作台（只在本机运行）')
    p.add_argument('--port', type=int, default=8750, help='本地端口（默认 8750）')
    args = p.parse_args()
    if not 0 <= args.port <= 65535:
        p.error('端口必须在 0–65535 之间')
    with HTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'用浏览器打开 http://127.0.0.1:{server.server_port} ，按 Ctrl+C 关闭。', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
