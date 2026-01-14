from __future__ import annotations

from mangum import Mangum

from raja.server.app import app

handler = Mangum(app)
