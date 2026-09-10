# -*- coding: utf-8 -*-
"""本地离线浏览服务器：把 /benfen 直接映射到 site/benfen/index.html，
不做 301 加尾斜杠，保持和原站一致的 URL，Nuxt 客户端路由才能正常匹配。"""
import os, sys, webbrowser, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'site')
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def translate_path(self, path):
        p = path.split('?', 1)[0].split('#', 1)[0]
        rel = p.lstrip('/')
        full = os.path.join(ROOT, rel.replace('/', os.sep))
        # 无扩展名且存在同名目录 -> 直接给 index.html，避免 301
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, 'index.html')):
            return os.path.join(full, 'index.html')
        return super().translate_path(path)

    def send_head(self):
        # 未知路径回落到站内 404 页面渲染，行为贴近原站
        return super().send_head()

    def log_message(self, *a):
        pass

if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    url = 'http://127.0.0.1:%d/' % PORT
    print('段永平投资问答录 · 本地存档')
    print('服务地址:', url)
    print('按 Ctrl+C 停止')
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
