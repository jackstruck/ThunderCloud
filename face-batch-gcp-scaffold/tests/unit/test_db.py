from worker.db import Database


class EmptyCursor:
    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchone(self):
        return None


def test_match_can_return_unknown():
    match = Database._match(EmptyCursor(), [1, 0], top_k=5, threshold=0.6)
    assert match.decision == "unknown"
    assert match.subject_id is None
