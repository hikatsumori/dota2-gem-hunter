
"""
Telegram notifier for Dota 2 Steam Market Rare Socket Scanner.
"""

import logging
from decimal import Decimal

import httpx

from src.analyzer.price_analyzer import AlertMatch
from src.config import settings
from src.steam.models import Gem, GemType, Item, Listing

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends rare gem alerts to Telegram."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id

        if not self._token or not self._chat_id:
            raise ValueError(
                "Telegram is not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            )

        self._url = (
            f"https://api.telegram.org/bot{self._token}/sendMessage"
        )

        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "TelegramNotifier":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    @staticmethod
    def _format_price(price: Decimal) -> str:
        return f"${price:.2f}"

    @staticmethod
    def _format_gems(gems: list[Gem]) -> str:
        if not gems:
            return "Не обнаружены"

        parts = []

        for gem in gems:
            gem_type = gem.gem_type.value
            name = gem.gem_name

            socket = (
                f", слот {gem.socket_index}"
                if gem.socket_index is not None
                else ""
            )

            rare = " ⭐" if gem.is_rare else ""

            parts.append(
                f"• {gem_type}: {name}{socket}{rare}"
            )

        return "\n".join(parts)

    def _build_message(
        self,
        alert_match: AlertMatch,
        listing: Listing,
        item: Item,
        gems: list[Gem],
    ) -> str:

        market_link = (
            listing.listing_url
            or (
                "https://steamcommunity.com/market/listings/570/"
                f"{item.market_hash_name}"
            )
        )

        return (
            "🔔 <b>Найден редкий гем!</b>\n\n"
            f"<b>Предмет:</b> {item.name}\n"
            f"<b>Цена:</b> {self._format_price(alert_match.current_price)}\n"
            f"<b>Средняя цена:</b> "
            f"{self._format_price(alert_match.average_price)}\n"
            f"<b>Скидка:</b> {alert_match.discount_pct:.2f}%\n\n"
            f"<b>Гемы:</b>\n{self._format_gems(gems)}\n\n"
            f"<b>Правило:</b> {alert_match.rule_name}\n\n"
            f'<a href="{market_link}">Открыть на Steam Market</a>'
        )

    async def send_alert(
        self,
        alert_match: AlertMatch,
        listing: Listing,
        item: Item,
        gems: list[Gem],
    ) -> None:

        message = self._build_message(
            alert_match,
            listing,
            item,
            gems,
        )

        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        response = await self._client.post(
            self._url,
            json=payload,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {result}"
            )

        logger.info(
            "Telegram alert sent: %s | discount: %s%%",
            item.name,
            alert_match.discount_pct,
        )
