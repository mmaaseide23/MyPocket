"""Create a static, inert fictional review artifact without a browser or server."""

import base64
import re
from datetime import UTC, datetime
from html import escape

from tests.offline_preview import DEMO, ROOT, STATIC, fictional_snapshot


def render_preview() -> str:
    snapshot = fictional_snapshot(datetime(2026, 11, 1, 12, tzinfo=UTC))
    html = (DEMO / "index.html").read_text()
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<link\b[^>]*>", "", html)
    html = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.S)
    html = html.replace('id="snapshot" hidden', 'id="snapshot"')
    html = re.sub(r"<(button|input)\b", r"<\1 disabled", html)

    def insert(identifier: str, content: str):
        nonlocal html
        pattern = rf'(<[^>]+\bid="{re.escape(identifier)}"[^>]*>).*?(</[^>]+>)'
        html, count = re.subn(pattern, lambda match: match[1] + content + match[2], html, flags=re.S)
        assert count == 1, identifier

    insert("state", "Design preview")
    insert("status", "Static fictional preview · all actions are disabled. No device storage is used by this file.")
    insert("net-worth", "$85,900")
    insert("cash", "$16,800")
    insert("investments", "$70,000")
    insert("debt", "$900")
    insert("captured-at", "Snapshot captured Nov 1, 2026 · 12:00 UTC")
    insert("connection", "Server unavailable · showing your saved copy. Changes to access cannot be checked until you reconnect.")
    insert("accounts", "".join(
        f'<li class="saved-account"><span>{escape(row["label"])}</span><strong>${row["balance"]:,.0f}</strong></li>'
        for row in snapshot["accounts"]
    ))
    insert("sources", "".join(
        f'<li><p class="saved-source-title">{escape(row["label"])}</p>'
        f'<p class="saved-small saved-muted">Last successful check: {escape(row["last_successful_check_at"])}</p>'
        '<p class="saved-small saved-muted">Provider data as of: unknown · timestamp not supplied</p></li>'
        for row in snapshot["sources"]
    ))
    insert("saved-at", "Saved to this device Nov 1, 2026 · 12:02 UTC")
    insert("expires-at", "Demo snapshot expires Nov 2, 2026 · 12:02 UTC")
    css = (STATIC / "styles.css").read_text() + (DEMO / "offline.css").read_text()
    sky = base64.b64encode((STATIC / "images/sky-daylight.jpg").read_bytes()).decode()
    css = css.replace("/static/images/sky-daylight.jpg", "data:image/jpeg;base64," + sky)
    icon = base64.b64encode((STATIC / "icon.svg").read_bytes()).decode()
    html = html.replace("/offline-demo/icon.svg", "data:image/svg+xml;base64," + icon)
    return html.replace("</head>", "<style>" + css + "</style></head>")


if __name__ == "__main__":
    destination = ROOT / "review/offline-preview.html"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(render_preview())
    print(destination)
