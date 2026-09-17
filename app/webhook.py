"""
Slack/Discord webhook notifications for new submissions — stdlib only
(urllib), best-effort. A failed webhook send never blocks the actual
submission; it's fired via FastAPI BackgroundTasks after the response
already went back to the submitter, so it can't add latency either.
"""
import json
import urllib.error
import urllib.request


def send_webhook_notification(webhook_url: str, message: str) -> bool:
    if not webhook_url:
        return False

    # ntfy.sh (and self-hosted ntfy instances) expect the notification as
    # a raw plain-text POST body, not JSON — Slack/Discord expect JSON.
    # Detected by hostname since ntfy.sh is the overwhelmingly common case;
    # a self-hosted ntfy instance under a different domain would need the
    # JSON path below, which ntfy will just display as a literal string.
    is_ntfy = "ntfy.sh" in webhook_url

    if is_ntfy:
        data = message.encode("utf-8")
        headers = {"Content-Type": "text/plain; charset=utf-8"}
    else:
        # Slack incoming webhooks read "text"; Discord webhooks read
        # "content". Sending both in one payload means the same URL works
        # for either platform without the form owner needing to specify
        # which one it is.
        data = json.dumps({"text": message, "content": message}).encode("utf-8")
        headers = {"Content-Type": "application/json"}

    req = urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 — any failure just degrades silently
        print(f"[webhook] Failed to notify {webhook_url}: {exc}")
        return False


def build_submission_message(project_title: str, submitter_name, is_anonymous: bool,
                              suggestion_text, nomination_reason, nominee_names: list) -> str:
    who = submitter_name if (submitter_name and not is_anonymous) else "Someone (anonymous)"
    lines = [f"📬 New submission on \"{project_title}\" — from {who}"]

    if suggestion_text:
        preview = suggestion_text if len(suggestion_text) <= 200 else suggestion_text[:200].rstrip() + "…"
        lines.append(f"💡 {preview}")

    if nominee_names:
        lines.append(f"🏆 Nominated: {', '.join(nominee_names)}")
        if nomination_reason:
            preview = nomination_reason if len(nomination_reason) <= 200 else nomination_reason[:200].rstrip() + "…"
            lines.append(f"   Reason: {preview}")

    return "\n".join(lines)


def build_new_form_message(project_title: str, owner_name: str) -> str:
    return f"✨ New form created: \"{project_title}\" — by {owner_name}"
