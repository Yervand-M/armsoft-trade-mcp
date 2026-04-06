"""
Patch MCP's TransportSecurityMiddleware to allow requests from any host.
Run this once after pip install, before starting the server.
"""
import os
import site
import glob

found = False
for sp in site.getsitepackages():
    pattern = os.path.join(sp, "mcp", "**", "transport_security.py")
    for path in glob.glob(pattern, recursive=True):
        print(f"Patching: {path}")
        with open(path, "w") as f:
            f.write("class TransportSecurityMiddleware:\n")
            f.write("    def __init__(self, app, *args, **kwargs):\n")
            f.write("        self.app = app\n")
            f.write("    async def __call__(self, scope, receive, send):\n")
            f.write("        await self.app(scope, receive, send)\n")
        found = True
        print("Done.")

if not found:
    print("transport_security.py not found — no patch needed.")