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

    # Slack incoming webhooks read "text"; Discord webhooks read "content".
    # Sending both in one payload means the same URL works for either
    # platform without the form owner needing to specify which one it is.
    payload = json.dumps({"text": message, "content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
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
