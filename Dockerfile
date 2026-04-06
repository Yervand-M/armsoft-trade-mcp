FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Patch MCP's TransportSecurityMiddleware so it accepts requests from any host.
# Without this, Railway's reverse proxy causes 421 Misdirected Request errors
# because the Host header contains the public domain, not 'localhost'.
RUN python3 -c "
import os, site, glob

found = False
for sp in site.getsitepackages():
    for path in glob.glob(os.path.join(sp, 'mcp', '**', 'transport_security.py'), recursive=True):
        print('Patching:', path)
        with open(path, 'w') as f:
            f.write('class TransportSecurityMiddleware:\n')
            f.write('    def __init__(self, app, *args, **kwargs):\n')
            f.write('        self.app = app\n')
            f.write('    async def __call__(self, scope, receive, send):\n')
            f.write('        await self.app(scope, receive, send)\n')
        found = True

if not found:
    print('transport_security.py not found - no patch needed')
"

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]