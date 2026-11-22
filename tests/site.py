"""Test infrastructure: ek nakli website (har mushkil haalat ke saath) aur ek nakli local LLM server.
Ye sirf tests ke liye hai; asli system seeds.toml ki asli sites crawl karta hai."""
from __future__ import annotations

import http.server
import json
import threading
import time

# code -> (title, sentence, query that must find it, 2nd query)
LANGS = {
    "ur": ("اردو صفحہ", "کریولر ویب پیجز کو پڑھتا ہے اور معلومات جمع کرتا ہے۔", "کریولر معلومات"),
    "ar": ("صفحة عربية", "محرك البحث يقوم بفهرسة صفحات الويب وجمع المعلومات.", "فهرسة صفحات"),
    "hi": ("हिंदी पेज", "खोज इंजन वेब पेज को इंडेक्स करता है और जानकारी जमा करता है।", "खोज इंजन जानकारी"),
    "ru": ("Русская страница", "Поисковый робот обходит страницы сайтов и собирает данные.", "поисковый робот данные"),
    "zh": ("中文页面", "异步编程是一种并发模型，事件循环在单个线程上协作地调度协程。", "异步编程"),
    "ja": ("日本語ページ", "検索エンジンはウェブページをインデックスして情報を収集します。", "検索エンジン"),
    "fr": ("Page française", "Le robot d'indexation respecte le fichier robots.txt et patiente entre les requêtes. Un résumé du café naïf.", "resume cafe naif"),
    "de": ("Deutsche Seite", "Der Webcrawler befolgt die Regeln und durchsucht Straßen und Größen sorgfältig.", "Webcrawler Straßen"),
    "th": ("หน้าภาษาไทย", "โปรแกรมค้นหาจัดทำดัชนีหน้าเว็บและรวบรวมข้อมูลจากอินเทอร์เน็ต", "ค้นหาจัดทำดัชนี"),
    "ko": ("한국어 페이지", "검색 엔진은 웹 페이지를 색인하고 정보를 수집합니다.", "검색 엔진 정보"),
    "es": ("Página española", "El rastreador de búsqueda respeta el archivo robots.txt y espera entre solicitudes.", "rastreador busqueda"),
    "mixed": ("混合页面", "使用Python开发网络爬虫非常方便，可以快速抓取网页数据并保存到数据库。", "Python 爬虫"),
}


def body_for(sentence: str, n: int = 14) -> str:
    return "".join(f"<p>{i}. {sentence}</p>" for i in range(1, n + 1))


def html_page(title: str, body: str, extra: str = "") -> str:
    return (f"<html><head><meta charset='utf-8'><title>{title}</title></head><body><nav><a href='/'>Home</a> menu menu</nav>"
            f"<article><h1>{title}</h1>{body}</article>{extra}<footer>cookie banner junk</footer></body></html>")


class Site:
    """with Site() as s:  s.url -> http://127.0.0.1:port"""

    def __init__(self) -> None:
        self.stats: dict[str, int] = {}
        self.state = {"version": "alphaversion"}
        self.paths_hit: list[str] = []
        self._srv: http.server.ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ server
    def __enter__(self) -> "Site":
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, body: bytes | str, ctype="text/html; charset=utf-8", code=200, headers=None):
                b = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(b)

            def do_GET(self):
                p = self.path.split("?")[0]
                site.paths_hit.append(self.path)
                site.stats[p] = site.stats.get(p, 0) + 1
                base = f"http://127.0.0.1:{self.server.server_port}"
                if p == "/robots.txt":
                    return self._send(f"User-agent: *\nDisallow: /private\nSitemap: {base}/sitemap.xml\n", "text/plain")
                if p == "/sitemap.xml":
                    return self._send(
                        f"<?xml version='1.0'?><sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                        f"<sitemap><loc>{base}/sitemap-lang.xml</loc></sitemap></sitemapindex>", "application/xml")
                if p == "/sitemap-lang.xml":
                    locs = "".join(f"<url><loc>{base}/ml/{c}</loc></url>" for c in LANGS)
                    return self._send(f"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{locs}</urlset>",
                                      "application/xml")
                if p.startswith("/ml/") and p[4:] in LANGS:
                    title, sentence, _ = LANGS[p[4:]]
                    return self._send(html_page(title, body_for(sentence)))
                if p == "/":
                    links = "".join(f"<a href='{h}'>{h}</a> " for h in (
                        "/etag-page", "/changing", "/hang", "/binary", "/huge", "/missing", "/redirect-me", "/private/x"))
                    ext = f"<a href='http://localhost:{self.server.server_port}/ext'>external</a>"
                    return self._send(html_page("Home", body_for("Welcome to the test homepage of this synthetic site.", 10), links + ext))
                if p == "/etag-page":
                    if self.headers.get("If-None-Match") == '"v1"':
                        site.stats["304"] = site.stats.get("304", 0) + 1
                        return self._send(b"", code=304, headers={"ETag": '"v1"'})
                    return self._send(html_page("Etag page", body_for("The etagtopic page explains conditional requests and caching."),
                                                ""), headers={"ETag": '"v1"'})
                if p == "/changing":
                    return self._send(html_page("Changing page", body_for(f"This page currently says {site.state['version']} for everyone.")))
                if p == "/hang":
                    time.sleep(4)
                    return self._send(html_page("Hang", body_for("never seen")))
                if p == "/binary":
                    return self._send(b"\x89PNG\r\n" + b"0" * 500, "image/png")
                if p == "/huge":
                    return self._send("<html><body>" + "<p>x</p>" * 60000 + "</body></html>")
                if p == "/redirect-me":
                    return self._send(b"", code=301, headers={"Location": "/etag-page2"})
                if p == "/etag-page2":
                    return self._send(html_page("Redirected", body_for("The redirected page talks about redirectword handling.")))
                if p == "/wiki/Quantum_computing":
                    return self._send(html_page("Quantum computing", body_for(
                        "Quantum computing uses qubits and superposition to perform calculations that classical computers cannot do efficiently.")))
                if p.startswith("/private"):
                    return self._send(html_page("Secret", body_for("secret must never be indexed")))
                self._send("not found", "text/plain", 404)

        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_port}"  # type: ignore[union-attr]


class FakeLLM:
    """llama.cpp jaisa OpenAI-compatible server. mode: good | crash | dead(use closed port)."""

    def __init__(self, answer: str, mode: str = "good", direct: str | None = None) -> None:
        """direct: prompt mein SOURCES na ho to ye jawab (seedha-LLM raaste ki jaanch)."""
        self.answer, self.mode, self.direct = answer, mode, direct
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                b = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _chunk(self, data: bytes):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                toks = (outer.direct if (outer.direct is not None and b"SOURCES:" not in body) else outer.answer).split(" ")
                if outer.mode == "crash":
                    toks = toks[:2]
                for t in toks:
                    self._chunk(("data: " + json.dumps({"choices": [{"delta": {"content": t + " "}}]}) + "\n\n").encode())
                if outer.mode == "crash":
                    self.close_connection = True  # chunked body adhoora chhor kar connection toro
                    return
                self._chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.srv.server_port}"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
