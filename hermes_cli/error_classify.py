"""Pure failure classifier for hermes.decision logging.

Maps an upstream (http, message) pair to a stable ``(class, next_action)`` pair. Deterministic
and side-effect free — it reads nothing, logs nothing, and imports no runtime state, so it is safe
to call from any error seam and trivial to unit test.

Classes: ``quota`` | ``transient`` | ``cred_dead`` | ``format_error`` | ``unknown``.
Next actions are advisory hints for the decision log (not enforced here).
"""


def classify_failure(http: int, message: str):
    msg = (message or "").lower()
    if http == 400 and ("string too long" in msg or "maximum length" in msg):
        return "format_error", "sanitize_ids_retry_same_provider"
    if http == 401 or "invalid access token" in msg or "invalid_api_key" in msg:
        return "cred_dead", "skip_provider_notify_user"
    if http == 402 or "credits" in msg:
        return "quota", "next_provider"
    if http == 429 and ("depleted" in msg or "none eligible" in msg):
        return "quota", "next_provider"
    if http == 429 and ("cooldown" in msg or "recovers" in msg):
        return "transient", "wait_cooldown_or_next"
    return "unknown", "retry_then_next"
