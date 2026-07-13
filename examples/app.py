from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hello from test app!\n")
    def log_message(self, *args):
        pass

HTTPServer(("0.0.0.0", 8501), Handler).serve_forever()