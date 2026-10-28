"""gx3gen 本地 HTTP 服务（Python 标准库 http.server）。

- ``create_server(host, port)`` 只做绑定，不启动事件循环；``port=0`` 时实际端口取
  ``server.server_address[1]``，便于测试在后台线程里 ``serve_forever()``。
- ``main(host, port)`` 打印访问地址并阻塞运行。
- 静态资源：``/`` -> web/index.html，``/static/*`` -> web/ 下白名单后缀文件。
- JSON 接口全部转给 :mod:`gx3gen.app.api`，错误统一为 ``{"error": "..."}``。
"""
from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from . import api

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEB_ROOT = REPO_ROOT / "web"
MAX_BODY = 64 * 1024 * 1024  # 64MB，足够上传大程序 CSV

STATIC_SUFFIXES = {
    ".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".map", ".woff2",
}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


class Gx3HTTPServer(ThreadingHTTPServer):
    """带 web 根目录与静默日志开关的线程化 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int],
                 RequestHandlerClass: Any, web_root: Optional[Path] = None) -> None:
        self.web_root = Path(web_root or DEFAULT_WEB_ROOT)
        self.quiet = False
        super().__init__(server_address, RequestHandlerClass)

    def handle_error(self, request: Any, client_address: Any) -> None:  # type: ignore[override]
        """浏览器关闭 keep-alive 连接是正常现象，不打印无意义的连接重置堆栈。"""
        exc_type, exc, _tb = sys.exc_info()
        if exc_type is not None and issubclass(
                exc_type, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class Gx3RequestHandler(BaseHTTPRequestHandler):
    server_version = "gx3gen/0.1"
    protocol_version = "HTTP/1.1"

    # -- HTTP 方法 ---------------------------------------------------------
    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    # -- 请求主流程 --------------------------------------------------------
    def _handle(self, method: str) -> None:
        head_only = method == "HEAD"
        try:
            parsed = urlsplit(self.path)
            raw_segments = [segment for segment in parsed.path.split("/") if segment]
            segments = [unquote(segment) for segment in raw_segments]
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            body = self._read_body()
            if body is None:
                return  # 已经在 _read_body 里返回过错误响应
            if segments and segments[0] == "api":
                status, headers, payload = api.dispatch(method, segments, query, body)
            else:
                status, headers, payload = self._serve_static(segments, method)
        except Exception as exc:  # 兜底：任何未捕获异常都返回 JSON 500
            traceback.print_exc()
            status = 500
            headers = {"Content-Type": "application/json; charset=utf-8"}
            payload = json.dumps({"error": "服务器内部错误：%s" % exc},
                                 ensure_ascii=False).encode("utf-8")
        self._send(status, headers, payload, head_only)

    def _read_body(self) -> Optional[bytes]:
        transfer = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer:
            self._reject_body(411, "暂不支持分块上传，请使用普通 POST")
            return None
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return b""
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            self._reject_body(400, "Content-Length 不合法")
            return None
        if length < 0:
            self._reject_body(400, "Content-Length 不合法")
            return None
        if length > MAX_BODY:
            self._reject_body(413, "请求体过大（上限 %d MB）" % (MAX_BODY // (1024 * 1024)))
            return None
        if length == 0:
            return b""
        try:
            return self.rfile.read(length)
        except OSError:
            return b""

    def _reject_body(self, status: int, message: str) -> None:
        """请求体无法安全读取时返回错误并关闭连接，避免残留字节串到下一个请求。"""
        self.close_connection = True
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._send(status, {"Content-Type": "application/json; charset=utf-8",
                            "Connection": "close"}, body, False)

    # -- 响应 --------------------------------------------------------------
    def _send(self, status: int, headers: Dict[str, str], payload: bytes,
              head_only: bool) -> None:
        try:
            headers = dict(headers or {})
            headers.pop("Content-Length", None)
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/octet-stream"
            headers.setdefault("Cache-Control", "no-store")
            headers["X-Content-Type-Options"] = "nosniff"
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload or b"")))
            self.end_headers()
            if not head_only and payload:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- 静态文件 ----------------------------------------------------------
    def _serve_static(self, segments: List[str], method: str
                      ) -> Tuple[int, Dict[str, str], bytes]:
        if method not in ("GET", "HEAD"):
            return 405, {"Content-Type": "application/json; charset=utf-8"}, \
                json.dumps({"error": "静态资源只支持 GET 请求"}, ensure_ascii=False).encode("utf-8")
        web_root = Path(getattr(self.server, "web_root", DEFAULT_WEB_ROOT))
        if not segments or segments == ["index.html"]:
            target = web_root / "index.html"
        elif segments == ["favicon.ico"]:
            return 204, {"Content-Type": "image/x-icon"}, b""
        elif segments[0] == "static":
            sub = segments[1:]
            if not sub:
                return self._not_found("缺少静态文件名")
            for segment in sub:
                if segment in (".", "..") or "/" in segment or "\\" in segment or ":" in segment:
                    return self._not_found("非法的静态资源路径")
            relative = "/".join(sub)
            target = web_root / relative
            if target.suffix.lower() not in STATIC_SUFFIXES:
                return self._not_found("不支持的静态资源类型")
        else:
            return self._not_found("页面不存在")
        try:
            root = web_root.resolve()
            resolved = target.resolve()
        except OSError:
            return self._not_found("页面不存在")
        if resolved != root and root not in resolved.parents:
            return self._not_found("非法的静态资源路径")
        if not resolved.is_file():
            return self._not_found("静态资源不存在：%s" % target.name)
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            return 500, {"Content-Type": "text/plain; charset=utf-8"}, \
                ("读取静态资源失败：%s" % exc).encode("utf-8")
        content_type = STATIC_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
        return 200, {"Content-Type": content_type, "Cache-Control": "no-cache"}, data

    @staticmethod
    def _not_found(message: str) -> Tuple[int, Dict[str, str], bytes]:
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, \
            (message or "未找到").encode("utf-8")

    # -- 日志 --------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write("[gx3gen] %s - %s\n" % (self.address_string(), fmt % args))


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def create_server(host: str = "127.0.0.1", port: int = 8765) -> "Gx3HTTPServer":
    """创建（绑定）HTTP 服务但不启动事件循环。port=0 表示由系统分配端口。"""
    server = Gx3HTTPServer((host, int(port)), Gx3RequestHandler, web_root=DEFAULT_WEB_ROOT)
    return server


def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    """启动本地服务并阻塞运行。"""
    try:
        server = create_server(host, port)
    except OSError as exc:
        raise SystemExit("无法启动本地服务（%s:%s）：%s" % (host, port, exc))
    actual_host, actual_port = server.server_address[0], server.server_address[1]
    print("gx3gen 本地软件已启动：打开 http://%s:%d" % (actual_host, actual_port))
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n已停止本地服务。")
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    main()
