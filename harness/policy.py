"""Output policy: which party may read which artifact.

The policy string lives in the measured tinfoil-config.yml, so changing who may
see results changes the enclave measurement both parties verify.

Format: ``bo:results+receipt,mo:receipt``. Grants are ``results`` and
``receipt``. ``identity`` and ``manifest`` are always readable by both parties.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbe.canonical import PARTIES, normalize_party

GRANTS = ("results", "receipt")
DEFAULT_POLICY = "bo:results+receipt,mo:receipt"


@dataclass(frozen=True)
class OutputPolicy:
    grants: dict[str, frozenset[str]] = field(default_factory=dict)
    source: str = DEFAULT_POLICY

    @classmethod
    def parse(cls, text: str | None) -> "OutputPolicy":
        text = (text or DEFAULT_POLICY).strip()
        grants: dict[str, frozenset[str]] = {party: frozenset() for party in PARTIES}
        for clause in filter(None, (c.strip() for c in text.split(","))):
            if ":" not in clause:
                raise ValueError(f"policy clause {clause!r} must look like party:grant+grant")
            party_text, grant_text = clause.split(":", 1)
            party = normalize_party(party_text)
            wanted = frozenset(filter(None, (g.strip() for g in grant_text.split("+"))))
            unknown = wanted - set(GRANTS)
            if unknown:
                raise ValueError(f"unknown grants {sorted(unknown)} in policy clause {clause!r}")
            grants[party] = grants[party] | wanted
        return cls(grants=grants, source=text)

    def allows(self, party: str, grant: str) -> bool:
        return grant in self.grants.get(normalize_party(party), frozenset())

    def as_dict(self) -> dict[str, list[str]]:
        return {party: sorted(g) for party, g in self.grants.items()}
