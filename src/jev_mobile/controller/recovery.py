"""Loop and repeated-state protection."""

from collections import Counter


class LoopGuard:
    def __init__(self, max_same_state_count: int) -> None:
        self.max_same_state_count = max_same_state_count
        self.fingerprints: Counter[str] = Counter()
        self.action_pairs: list[tuple[str, str]] = []

    def observe(self, fingerprint: str) -> bool:
        self.fingerprints[fingerprint] += 1
        return self.fingerprints[fingerprint] > self.max_same_state_count

    def action(self, fingerprint: str, action_id: str) -> bool:
        self.action_pairs.append((fingerprint, action_id))
        return len(self.action_pairs) >= 4 and self.action_pairs[-1] == self.action_pairs[-3] and self.action_pairs[-2] == self.action_pairs[-4]
