"""Every ORM model in one place, for Alembic's autogenerate.

SQLAlchemy only knows about a table once its class has been imported, and the
models now live beside the concern that owns them (conversations, billing,
integrations, admin) rather than in one directory. This module imports all of
them so `alembic/env.py` has a single thing to import and no migration silently
misses a table.
"""

from app.core.admin.admin_audit_log_model import AdminAuditLog
from app.core.admin.agent_admin_model import AgentAdmin
from app.core.billing.budget_block_event_model import BudgetBlockEvent
from app.core.billing.enterprise_budget_model import EnterpriseBudget
from app.core.billing.usage_ledger_model import LlmUsageLedger
from app.core.billing.user_token_model import UserToken
from app.core.conversations.conversation_model import Conversation
from app.core.conversations.feedback_model import Feedback
from app.core.conversations.message_model import Message
from app.core.integrations.integration_api_key_model import IntegrationApiKey
from app.core.integrations.integration_request_log_model import IntegrationRequestLog

__all__ = [
    "AdminAuditLog",
    "AgentAdmin",
    "BudgetBlockEvent",
    "Conversation",
    "EnterpriseBudget",
    "Feedback",
    "IntegrationApiKey",
    "IntegrationRequestLog",
    "LlmUsageLedger",
    "Message",
    "UserToken",
]
