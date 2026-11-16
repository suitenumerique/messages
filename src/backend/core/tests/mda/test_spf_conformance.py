"""The openspf RFC 7208 conformance suite, run against ``core.mda.spf``.

``resources/spf/rfc7208-tests.yml`` is the suite verbatim (release 2014.04,
from sdgathman/pyspf, BSD licensed: see ``rfc7208-tests.LICENSE``). The zone
semantics below follow the reference driver (pyspf ``test/testspf.py``):

- an ``SPF`` record is also served as ``TXT`` unless the name lists a TXT;
- ``TXT: NONE`` means "no TXT record";
- a bare ``TIMEOUT`` entry times out queries for every type not listed
  before it; a ``TYPE: TIMEOUT`` entry times out that type;
- an unknown name is NXDOMAIN, except ``error.*`` names, which time out;
- a CNAME is followed one level.

``explanation`` is not checked: we never fetch ``exp`` (see the module).
The suite expects the RFC's 10-term lookup limit, so it runs with that.
"""

import ipaddress
from pathlib import Path

import dns.reversename
import pytest
import yaml

from core.mda import spf

SUITE = Path(__file__).parent.parent / "resources" / "spf" / "rfc7208-tests.yml"


def _load_scenarios():
    with SUITE.open("rb") as handle:
        return list(yaml.safe_load_all(handle))


def _zone(zonedata):
    zone = {}
    for name, entries in (zonedata or {}).items():
        records = []
        generated_txt = []
        has_txt = False
        for entry in entries:
            if entry == "TIMEOUT":
                records.append(("TIMEOUT", None))
                continue
            ((rtype, value),) = entry.items()
            if rtype in ("TXT", "SPF") and isinstance(value, str):
                value = [value]
            if rtype == "TXT":
                has_txt = True
            elif rtype == "SPF":
                generated_txt.append(("TXT", value))
            if value != ["NONE"]:
                records.append((rtype, value))
        if not has_txt:
            records.extend(generated_txt)
        zone[name.lower().rstrip(".")] = records
    return zone


class SuiteLookup(spf.SPFLookup):
    """An :class:`spf.SPFLookup` answering from a scenario's zone data."""

    def __init__(self, zone):
        super().__init__()
        self.zone = zone

    def _records(self, name, rtype, follow_cname=True):
        """(values, nxdomain) for one query, or raise SPFTempError."""
        name = name.lower().rstrip(".")
        if any(len(label) > 63 for label in name.split(".")) or not name:
            return [], False
        if name not in self.zone:
            if name.startswith("error."):
                raise spf.SPFTempError(f"{name} times out")
            return [], True
        values = []
        seen_type = False
        for record_type, value in self.zone[name]:
            if record_type == "TIMEOUT":
                if not seen_type:
                    raise spf.SPFTempError(f"{name} times out")
                break
            if record_type == rtype:
                seen_type = True
                if value == "TIMEOUT":
                    raise spf.SPFTempError(f"{name}/{rtype} times out")
                values.append(value)
            elif record_type == "CNAME" and follow_cname:
                return self._records(value, rtype, follow_cname=False)
        return values, False

    def txt(self, name):
        values, nxdomain = self._records(name, "TXT")
        return ["".join(chunks) for chunks in values], nxdomain

    def addresses(self, name, version):
        values, _ = self._records(name, "A" if version == 4 else "AAAA")
        return [str(ipaddress.ip_address(v)) for v in values]

    def mx_hosts(self, name):
        values, _ = self._records(name, "MX")
        return [str(exchange).rstrip(".") for _, exchange in values]

    def ptr(self, ip):
        reverse = dns.reversename.from_address(ip).to_text(omit_final_dot=True)
        values, _ = self._records(reverse, "PTR")
        return [str(v).rstrip(".") for v in values]


def _cases():
    for scenario in _load_scenarios():
        zone = _zone(scenario.get("zonedata"))
        for test_id, test in scenario["tests"].items():
            yield pytest.param(zone, test, id=test_id)


@pytest.mark.parametrize(("zone", "test"), _cases())
def test_rfc7208_conformance(zone, test, monkeypatch):
    """Each scenario yields one of the results the suite accepts."""
    # The suite tests the RFC's limit; production allows 30 (see spf.py).
    monkeypatch.setattr(spf, "MAX_DNS_TERMS", 10)
    result = spf.evaluate_spf(
        test["host"],
        test.get("mailfrom") or "",
        test.get("helo") or "",
        lookup=SuiteLookup(zone),
    )
    expected = test["result"]
    accepted = expected if isinstance(expected, list) else [expected]
    assert result.result in accepted, (
        f"{test.get('spec')}: got {result.result} ({result.reason}), "
        f"expected {accepted}. {test.get('description', '')}"
    )
