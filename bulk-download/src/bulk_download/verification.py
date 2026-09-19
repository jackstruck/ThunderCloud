"""Actionable verification failures without exposing download URLs or credentials."""


class VerificationError(RuntimeError):
    code = 'verification_failure'

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f'verification_failure ({stage}): {message}')
