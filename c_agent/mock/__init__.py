"""Local mock servers for end-to-end testing of the C P2C sniper agent.

Two servers mimic the real app.send.tg surface without credentials or Cloudflare:

- ``ws_server``   — Engine.IO v4 / Socket.IO order feed (path
  ``/socket.io/?EIO=4&transport=websocket``).
- ``take_server`` — HTTP ``take`` endpoint that simulates the order-claim race.

See ``README.md`` for how to run and the available env vars.
"""
