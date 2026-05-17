from __future__ import annotations

import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import httpx

from app.config import Settings
from app.services.types import NormalizedJobStatus


class AlertManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._alert_cache: dict[str, datetime] = {}
        self._dedupe_minutes = 30

    def _build_key(self, item: NormalizedJobStatus) -> str:
        end_part = item.ended_at.isoformat() if item.ended_at else ""
        return f"{item.source}:{item.job_name}:{item.status}:{end_part}"

    def _should_alert(self, item: NormalizedJobStatus) -> bool:
        if item.status not in {"failed", "warning"}:
            return False

        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=self._dedupe_minutes)
        stale_keys = [key for key, ts in self._alert_cache.items() if ts < cutoff]
        for key in stale_keys:
            self._alert_cache.pop(key, None)

        key = self._build_key(item)
        previous = self._alert_cache.get(key)
        if previous and now - previous < timedelta(minutes=self._dedupe_minutes):
            return False

        self._alert_cache[key] = now
        return True

    async def send_alerts(self, items: list[NormalizedJobStatus]) -> int:
        if not self.settings.alert_enabled:
            return 0

        alert_items = [item for item in items if self._should_alert(item)]
        if not alert_items:
            return 0

        message = self._render_alert_message(alert_items)
        if self.settings.alert_mode == "telegram":
            await self._send_telegram(message)
        else:
            self._send_email(message)
        return len(alert_items)

    def _render_alert_message(self, items: list[NormalizedJobStatus]) -> str:
        lines = ["Backup job alert (failed/warning):"]
        for item in items:
            ended = item.ended_at.isoformat() if item.ended_at else "-"
            lines.append(f"- [{item.source}] {item.job_name}: {item.status} (ended: {ended})")
            if item.message:
                lines.append(f"  message: {item.message}")
        return "\n".join(lines)

    async def _send_telegram(self, message: str):
        token = self.settings.telegram_bot_token.strip()
        chat_id = self.settings.telegram_chat_id.strip()
        if not token or not chat_id:
            return
        url = f"{self.settings.telegram_api_base.rstrip('/')}/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

    def _send_email(self, message: str):
        if not self.settings.smtp_host or not self.settings.alert_email_to:
            return

        mime = MIMEText(message, _charset="utf-8")
        mime["Subject"] = "Backup Job Alert"
        mime["From"] = self.settings.smtp_from_email or self.settings.smtp_username
        mime["To"] = self.settings.alert_email_to

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
            if self.settings.smtp_use_tls:
                smtp.starttls()
            if self.settings.smtp_username:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(mime)
