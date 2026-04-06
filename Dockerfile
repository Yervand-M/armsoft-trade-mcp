FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Patch MCP's TransportSecurityMiddleware so it accepts requests from any host.
# Without this, Railway's reverse proxy causes 421 Misdirected Request errors.
COPY patch_security.py .
RUN python3 patch_security.py

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]