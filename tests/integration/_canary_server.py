import http.server
import socketserver
import threading


class CanaryHandler(http.server.BaseHTTPRequestHandler):
    requests_received: list[str] = []

    def do_GET(self):
        CanaryHandler.requests_received.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        CanaryHandler.requests_received.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass


class CanaryServer:
    def __init__(self, port=8099):
        self.port = port
        CanaryHandler.requests_received = []
        # Allow reuse of address to prevent "Address already in use" errors on rapid restarts
        socketserver.TCPServer.allow_reuse_address = True
        self.server = socketserver.TCPServer(("127.0.0.1", port), CanaryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def get_requests(self):
        return CanaryHandler.requests_received
