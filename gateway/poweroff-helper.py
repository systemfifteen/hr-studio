#!/usr/bin/env python3
# Poweroff Helper - vypne notebook, port 8769
from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/poweroff':
            try:
                logging.info('Poweroff requested via HR Studio admin')
                self.send_response(200)
                self.end_headers()
                subprocess.Popen(['systemctl', 'poweroff'])
            except Exception as e:
                logging.error('Poweroff failed: %s', e)
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a): pass

print('Poweroff helper listening on 127.0.0.1:8769')
HTTPServer(('127.0.0.1', 8769), Handler).serve_forever()
