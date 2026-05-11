#!/usr/bin/env python3
"""Dashboard HTTP server with stop endpoint for GA."""
import http.server, os, sys

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/stop-ga':
            with open('ga_stop.flag', 'w') as f:
                f.write('stop')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'GA stop requested')
            print("[Dashboard] GA stop flag created", flush=True)
        elif self.path == '/start-ga':
            try: os.remove('ga_stop.flag')
            except: pass
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'GA stop flag cleared')
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/ga-status':
            running = not os.path.exists('ga_stop.flag')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(f'{{"running":{str(running).lower()}}}'.encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # suppress request logging

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8085
    server = http.server.HTTPServer(('127.0.0.1', port), Handler)
    print(f"Dashboard: http://127.0.0.1:{port}/dashboard.html", flush=True)
    server.serve_forever()
