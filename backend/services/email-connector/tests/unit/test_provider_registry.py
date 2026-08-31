"""The one place a third mail provider means adding a line rather than
touching sync_orchestrator.py — see app/services/providers/registry.py."""
from app.models import EmailProviderName
from app.services.gmail.provider import GmailProviderClient
from app.services.outlook.client import OutlookProviderClient
from app.services.providers.registry import get_provider_client_class


def test_gmail_resolves_to_the_gmail_provider_client() -> None:
    assert get_provider_client_class(EmailProviderName.gmail) is GmailProviderClient


def test_outlook_resolves_to_the_outlook_provider_client() -> None:
    assert get_provider_client_class(EmailProviderName.outlook) is OutlookProviderClient
