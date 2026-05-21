"""sandbox_host.py — WD-23. Build the sandbox HTML host page for a drop.

The host page lives on api.windydrops.com (the registry origin). The iframe
inside loads the drop's bundle from drops.windydrops.com (separate origin),
sandbox="allow-scripts" (no allow-same-origin), CSP-locked.

Mock data per drop type matches what each consumer surface would inject in
production. The drop's render.js receives postMessage events:
  - {type: "mock-data", payload: <type-specific>}
and replies:
  - {type: "ready"} | {type: "rendered"} | {type: "error", error: ...}

Reference: ADR-053 §"Live preview & sandboxing".
"""

from __future__ import annotations

import json
from typing import Any

# Per-type default mocks. preview_mock_data on the drop itself overrides.
DEFAULT_MOCKS: dict[str, dict[str, Any]] = {
    "control-panel-template": {
        "vitals": {
            "schema": "windy.vitals.v1",
            "sampled_at": "2026-05-21T15:30:00.000Z",
            "source": "mock",
            "host": {
                "hostname": "demo.local", "model": "Mock Machine",
                "platform": "darwin", "release": "22.6.0", "arch": "x64",
                "ip": "127.0.0.1", "uptime_seconds": 86400, "location": None,
            },
            "cpu": {"model": "Mock CPU", "cores": 4,
                    "avg_utilization_pct": 42.0, "core_utilization_pct": [40, 45, 38, 45],
                    "temperature_c": None},
            "gpu": None,
            "memory": {"total_bytes": 16_000_000_000, "available_bytes": 8_000_000_000,
                       "used_pct": 50.0},
            "disk": {"total_bytes": 500_000_000_000, "used_bytes": 100_000_000_000,
                     "used_pct": 20.0},
            "network": {"total_tx_bytes_per_sec": 100_000, "total_rx_bytes_per_sec": 200_000},
            "load": [0.5, 0.7, 0.6],
            "processes": {"all": 250, "running": None, "sleeping": None},
            "thermal": None,
        },
        "fleet": {
            "schema": "windy.fleet.v1",
            "fetched_at": "2026-05-21T15:30:00.000Z",
            "user_id": "wid_mock",
            "this_machine": {
                "is_user_device": True, "can_self_report": True,
                "vitals_url": "ipc://system-info",
            },
            "agents": [],
        },
    },
    "skill": {
        "invocation": {
            "user_id": "wid_mock", "prompt": "Sample user prompt",
            "context_documents": [], "tools_available": [],
        },
    },
    "theme": {
        "sample_dom": "<div class='card'><h2>Sample heading</h2><p>Body text.</p></div>",
    },
    "voice-pack": {
        "audio_sample_url": "https://drops.windydrops.com/_mocks/voice-sample.wav",
        "lipsync_coords": [],
    },
    "workflow": {
        "trigger": {"type": "cron", "schedule": "0 7 * * *"},
        "context": {"now": "2026-05-21T07:00:00Z"},
    },
    "tool": {
        "args": {}, "user_id": "wid_mock",
    },
}


def build_preview_html(
    *,
    drop_id: str,
    version: str,
    drop_type: str,
    public_bundle_domain: str,
    mock_data: dict[str, Any] | None = None,
) -> str:
    """Build the sandbox host HTML for /api/v1/drops/{id}/preview."""
    payload = mock_data if mock_data is not None else DEFAULT_MOCKS.get(drop_type, {})

    # Iframe loads the drop's render.html from drops.windydrops.com (a
    # separate origin from the api host). sandbox="allow-scripts" — no
    # allow-same-origin = parent DOM unreachable.
    iframe_src = f"https://{public_bundle_domain}/{drop_id}/{version}/render.html"
    payload_json = json.dumps(payload, separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Preview: {_escape(drop_id)} @ {_escape(version)}</title>
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; img-src https://{public_bundle_domain} data:; script-src 'unsafe-inline'; frame-src https://{public_bundle_domain}; style-src 'unsafe-inline'; connect-src 'none';">
<style>
  body {{ margin: 0; padding: 0; background: #0a0e14; }}
  iframe {{ width: 100vw; height: 100vh; border: 0; display: block; }}
</style>
</head>
<body>
<iframe id="drop-frame"
        src="{iframe_src}"
        sandbox="allow-scripts"
        referrerpolicy="no-referrer"
        loading="lazy"></iframe>
<script>
(function() {{
  // Sandbox host postMessage protocol per ADR-053 §"Sandbox security model (v1)".
  const TARGET_ORIGIN = "https://{public_bundle_domain}";
  const mockPayload = {payload_json};
  const frame = document.getElementById("drop-frame");

  function send(msg) {{
    if (frame && frame.contentWindow) {{
      frame.contentWindow.postMessage(msg, TARGET_ORIGIN);
    }}
  }}

  window.addEventListener("message", function(event) {{
    if (event.origin !== TARGET_ORIGIN) return;  // parent DOM lockdown
    const msg = event.data || {{}};
    if (msg.type === "ready") {{
      send({{ type: "mock-data", payload: mockPayload }});
    }}
    // "rendered" + "error" events are informational; the parent could
    // bubble them to the embedding page (windydrops.com/d/{drop_id})
    // when that wraps this iframe.
  }});
}})();
</script>
</body>
</html>"""


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
