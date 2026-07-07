#!/usr/bin/env python3
"""Mock HTTP server returning canned Polly DescribeVoices JSON."""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler

RESPONSE_BODY = json.dumps({
    "Voices": [
        {
            "Gender": "Female",
            "Id": "Joanna",
            "LanguageCode": "en-US",
            "LanguageName": "US English",
            "Name": "Joanna",
            "SupportedEngines": ["neural", "standard"],
        },
        {
            "Gender": "Male",
            "Id": "Matthew",
            "LanguageCode": "en-US",
            "LanguageName": "US English",
            "Name": "Matthew",
            "SupportedEngines": ["neural", "standard"],
        },
        {
            "Gender": "Female",
            "Id": "Amy",
            "LanguageCode": "en-GB",
            "LanguageName": "British English",
            "Name": "Amy",
            "SupportedEngines": ["neural"],
        },
    ]
}).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read and discard request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            self.rfile.read(content_length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(RESPONSE_BODY)))
        self.end_headers()
        self.wfile.write(RESPONSE_BODY)

    def do_GET(self):
        self.do_POST()

    def log_message(self, format, *args):
        # Suppress all request logging for speed
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8888), Handler)
    print("Mock server listening on http://127.0.0.1:8888")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
