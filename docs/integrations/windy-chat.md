# Windy Chat trending feed integration (WD-32)

windy-chat surfaces drops in a "Drops" tab inside the chat client. Drops appear in the trending feed with an inline Integrate button; URLs pasted in chat rooms unfurl into rich cards.

## Three integration surfaces

### 1. Webhook subscriber (server side)

windy-chat (or any service that wants to push notifications to chat users) subscribes to drop-lifecycle events from the registry. One-time setup:

```bash
curl -X POST https://api.windydrops.com/api/v1/webhooks/subscribe \
  -H "Authorization: Bearer $CHAT_OPERATOR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "callback_url": "https://chat.windychat.ai/api/v1/webhooks/drops",
    "event_types": [
      "drop.published",
      "drop.installed",
      "drop.tipped",
      "drop.forked"
    ],
    "secret": "<random 32-char string from kit-army-config/secrets/>"
  }'
```

Save the `id` returned in the response — it's the subscription handle.

#### Receiver shape (windy-chat side)

```python
# windy-chat/onboarding-api/src/routes/webhooks/drops.py (sketch)
import hashlib
import hmac
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter()
SECRET = os.environ["WINDY_DROPS_WEBHOOK_SECRET"]  # the same secret we POST'd above

@router.post("/api/v1/webhooks/drops")
async def drops_webhook(
    request: Request,
    signature: str = Header(..., alias="x-windy-drops-signature"),
):
    body = await request.body()
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid signature")

    event = json.loads(body)
    et = event["event_type"]
    if et == "drop.published":
        # Fan out a Synapse account-data update to subscribers' Drops tab.
        await push_drop_to_trending_feed(
            drop_id=event["drop_id"],
            version=event["version"],
            type=event["type"],
            signer_passport=event.get("signer_passport"),
        )
    elif et == "drop.tipped":
        await notify_author(event["author_handle"], event["amount_cents"])
    # ...
    return {"received": True}
```

### 2. Chat client "Drops" tab (windy-pro + windy-pro-mobile)

The client (whether desktop Electron in windy-pro or React Native in windy-pro-mobile) gains a new top-level navigation entry. The tab fetches:

```
GET https://api.windydrops.com/api/v1/drops/trending?limit=30
GET https://api.windydrops.com/api/v1/drops?lang=<user-locale>
```

…and renders the cards using the same shape as windydrops.com/browse (WD-26). Tapping a card opens the drop detail page; tapping **Integrate** fires:

```
POST https://api.windydrops.com/api/v1/me/library/install
Authorization: Bearer <Pro JWT or Eternitas EPT>
Content-Type: application/json

{ "drop_id": "kit-oc5-echo-hq" }
```

The 402 (paid drops) / 410 (withdrawn) / 409 (already installed) responses are surfaced inline. Successful installs flip the card to "✓ Installed" with an Uninstall affordance.

### 3. In-chat URL unfurl

windy-chat's existing OpenGraph unfurl service (lives in onboarding-api or push-gateway depending on which side wires it) automatically picks up `https://windydrops.com/d/<id>` URLs because WD-24 ships full OG metadata. No new code needed; the rich card just works.

To turn this on intentionally, the unfurl whitelist should include `windydrops.com` and `drops.windydrops.com` (the latter for direct bundle previews).

## Trust signals in the chat UI

Trending cards in chat should render the signer_passport's integrity_band when present:

| `signer_integrity_band` | Card treatment |
|---|---|
| `exceptional` / `good` | ✓ verified, green tint |
| `fair` | ✓ verified (neutral) |
| `poor` / `critical` | ⚠️ flagged; muted by default, opt-in to reveal |
| (none — unsigned) | Card shows author display name only, no badge |

The `signer_integrity_band` field is on every `DropSummary` returned by the registry — no extra Eternitas API call needed in the client.

## Notification fan-out (out of scope, mentioned for completeness)

For users who follow an author (per WD-25), `drop.published` events from a followed author can become a push notification via the existing windy-chat push-gateway:

```
windy-registry → webhook → windy-chat:onboarding → push-gateway → APNs/FCM
```

The push payload reuses the existing notification shape; only the source plumbing is new.

## What needs to land in windy-chat to ship

| Change | File (approximate) | Owner |
|---|---|---|
| Subscriber webhook endpoint | `windy-chat/onboarding-api/src/routes/webhooks/drops.py` | windy-chat maintainer |
| Push-gateway → Drops tab fanout | `windy-chat/push-gateway/src/handlers/drops.js` | windy-chat maintainer |
| Drops tab navigation entry | `windy-pro/src/client/desktop/renderer/nav.js` + mobile counterpart | windy-pro maintainer |
| Integrate-button → registry POST | `windy-pro/src/client/web/components/DropCard.tsx` (new) | windy-pro maintainer |
| Drops tab list view | same area as above | windy-pro maintainer |
| Webhook secret in chat env | `windy-chat/.env.production` | deploy step |

## Order of operations (suggested)

1. **Subscribe**: one curl from a maintainer's shell, captures the subscription id.
2. **Implement receiver**: short — verifies HMAC, logs events to chat's existing telemetry.
3. **Drops tab UI**: cribs from `windy-drops-site/dist/browse/` (vanilla JS reference impl).
4. **Wire Integrate button**: needs the user's Pro JWT, which the chat client already has.
5. **Push fanout**: follower-aware push (depends on WD-25 follow graph).

Each is a self-contained PR; nothing blocks anything else after step 1.

## Strand reference

WD-32 of `sneakyfree/windy-drops/docs/DNA_STRAND_MASTER_PLAN.md`. The windy-chat repo changes happen in `sneakyfree/windy-chat` and `sneakyfree/windy-pro` — they're not in this registry repo, so this strand's scope is the **contract + integration spec**. The actual code changes land in those repos in a follow-up session.
