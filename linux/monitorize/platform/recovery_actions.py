"""Immutable user intent and structured results for asynchronous recovery."""
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class RecoveryRequest:
    operation: str
    generation: int
    enabled: bool | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self):
        if self.operation not in ('boot_startup','source_repair','maintenance_resume','host_recovery','clear_tokens','cleanup_displays','reset_sunshine','reboot','logout'):
            raise ValueError('Unsupported recovery operation.')
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError('Invalid recovery generation.')
        if self.enabled is not None and type(self.enabled) is not bool:
            raise ValueError('Startup intent must be a boolean.')
        if self.operation == 'boot_startup' and self.enabled is None:
            raise ValueError('Boot startup requires an explicit enable/disable intent.')

    def result(self, values):
        result = dict(values)
        outcome = ('cancelled' if result.get('cancelled') else
                   'success' if result.get('success') else
                   'outcome_unknown' if result.get('outcome_unknown') else
                   'partial' if result.get('partial') or result.get('cleanup_pending') else 'failed')
        return {**result, 'outcome': outcome, 'operation': self.operation,
                'request_id': self.request_id, 'requested_enabled': self.enabled}
