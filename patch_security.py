"""
Patch MCP's TransportSecurityMiddleware to allow requests from any host.
Appends an override to the existing file so other classes (like
TransportSecuritySettings) are preserved intact.
"""
import os
import site
import glob

OVERRIDE = '''
# --- Proxy-compatibility patch ---
# Replace __call__ with a passthrough so requests from any Host are accepted.
# Required when running behind Railway's reverse proxy.
async def _passthrough_call(self, scope, receive, send):
    await self.app(scope, receive, send)

TransportSecurityMiddleware.__call__ = _passthrough_call
# --- End patch ---
'''

found = False

# Try the known path first
known_path = "/usr/local/lib/python3.11/site-packages/mcp/server/transport_security.py"
if os.path.exists(known_path):
    paths = [known_path]
else:
    paths = []
    for sp in site.getsitepackages():
        paths.extend(glob.glob(
            os.path.join(sp, "mcp", "**", "transport_security.py"),
            recursive=True
        ))

for path in paths:
    print(f"Patching: {path}")
    with open(path, "r") as f:
        content = f.read()
    with open(path, "w") as f:
        f.write(content)
        f.write(OVERRIDE)
    found = True
    print("Done.")

if not found:
    print("transport_security.py not found — no patch needed.")