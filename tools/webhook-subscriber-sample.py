#!/usr/bin/env python3
"""webhook-subscriber-sample.py — drop-in reference subscriber for the
Windy Drops webhook substrate (WD-21).

Usage:
    pip install fastapi uvicorn
    export WINDY_DROPS_WEBHOOK_SECRET=<the same secret you POST'd at subscribe>
    python webhook-subscriber-sample.py

Then in another shell:
    curl -X POST https://api.windydrops.com/api/v1/webhooks/subscribe \\
      -H "Authorization: Bearer $YOUR_JWT" \\
      -H "Content-Type: application/json" \\
      -d "{
        \\"callback_url\\": \\"https://your-host/webhooks/drops\\",
        \\"event_types\\": [\\"drop.published\\"],
        \\"secret\\": \\"$WINDY_DROPS_WEBHOOK_SECRET\\"
      }"

The script logs every event to stdout. Wire to your own pipeline as desired.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
SECRET = os.environ.get("WINDY_DROPS_WEBHOOK_SECRET", "")


@app.post("/webhooks/drops")
async def receive(
    request: Request,
    signature: str = Header(..., alias="x-windy-drops-signature"),
):
    if not SECRET:
        raise HTTPException(500, "WINDY_DROPS_WEBHOOK_SECRET env not set")
    body = await request.body()
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid signature")

    import json
    event = json.loads(body)
    ts = datetime.now(UTC).isoformat()
    print(
        f"[{ts}] {event.get('event_type', 'unknown')} "
        f"drop_id={event.get('drop_id', '?')} version={event.get('version', '?')}",
        file=sys.stdout,
        flush=True,
    )
    return {"received": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8901, log_level="info")
