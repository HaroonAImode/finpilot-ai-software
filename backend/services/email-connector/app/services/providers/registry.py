"""Maps EmailProviderName -> its MailProviderClient implementation.

Used only by SyncOrchestrator (see base.py's module docstring for why the
thinner call sites — auth, attachment_fetch, token refresh — branch
directly instead of going through this). Adding a third provider to the
sync engine means adding one line here, not touching sync_orchestrator.py.
"""
from app.models import EmailProviderName
from app.services.gmail.provider import GmailProviderClient
from app.services.outlook.client import OutlookProviderClient

_CLIENTS = {
    EmailProviderName.gmail: GmailProviderClient,
    EmailProviderName.outlook: OutlookProviderClient,
}


def get_provider_client_class(provider: EmailProviderName):
    return _CLIENTS[provider]
