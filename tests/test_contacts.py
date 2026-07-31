"""The contact directory behind "search my WhatsApp contacts".

Two things are under test: the provider-agnostic contract (`ContactDirectory`) and the
one implementation that knows Evolution exists. The server and the GUI depend on the
contract, never on Evolution — swapping the backend must not touch them.
"""

from coworker.connectors.contacts import Contact, search_contacts
from coworker.connectors.whatsapp import EvolutionContactDirectory


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _directory(payload, *, status_code=200, capture=None, boom=None):
    def post(url, headers=None, json=None, timeout=None):
        if capture is not None:
            capture.append({"url": url, "headers": headers, "json": json})
        if boom is not None:
            raise boom
        return FakeResponse(payload, status_code)

    return EvolutionContactDirectory(
        "http://evo:8080/", "KEY", "openworker", owner_number="5511777777777", post=post
    )


CONTACTS = [
    {"id": "5511999999999@s.whatsapp.net", "pushName": "Ana Silva"},
    {"id": "5511888888888@s.whatsapp.net", "pushName": "Bruno"},
    {"id": "120363000000@g.us", "pushName": "Família"},  # group: not a person
    {"id": "5511777777777@s.whatsapp.net", "pushName": "Eu"},  # the owner
]


def test_search_matches_name_or_number_and_skips_groups_and_self():
    directory = _directory(CONTACTS)

    by_name = directory.search("ana")
    assert [(c.number, c.name) for c in by_name] == [("5511999999999", "Ana Silva")]

    by_number = directory.search("8888")
    assert [c.number for c in by_number] == ["5511888888888"]

    everyone = directory.search("")
    assert [c.number for c in everyone] == ["5511999999999", "5511888888888"]
    assert all("@" not in c.number for c in everyone)


def test_search_is_case_insensitive_and_ignores_punctuation_in_queries():
    directory = _directory(CONTACTS)
    assert [c.name for c in directory.search("ANA")] == ["Ana Silva"]
    # A pasted, formatted number still finds its contact.
    assert [c.number for c in directory.search("+55 11 99999-9999")] == [
        "5511999999999"
    ]


def test_search_honors_the_limit():
    many = [
        {"id": f"55119999999{i:02d}@s.whatsapp.net", "pushName": f"P{i}"}
        for i in range(30)
    ]
    assert len(_directory(many).search("", limit=5)) == 5


def test_search_reads_alternative_payload_shapes():
    # Evolution versions differ: a bare list, or wrapped under a key; name in
    # `pushName`, `name`, or absent entirely.
    wrapped = {"contacts": [{"remoteJid": "5511999999999@s.whatsapp.net", "name": "Ana"}]}
    assert [(c.number, c.name) for c in _directory(wrapped).search("")] == [
        ("5511999999999", "Ana")
    ]
    nameless = [{"id": "5511999999999@s.whatsapp.net"}]
    got = _directory(nameless).search("")
    assert got[0].number == "5511999999999" and got[0].name is None


def test_search_posts_where_the_evolution_contract_says():
    seen: list[dict] = []
    _directory(CONTACTS, capture=seen).search("ana")
    assert seen[0]["url"] == "http://evo:8080/chat/findContacts/openworker"
    assert seen[0]["headers"]["apikey"] == "KEY"


def test_network_failure_and_http_error_surface_as_empty_not_a_crash():
    assert _directory(CONTACTS, boom=RuntimeError("down")).search("ana") == []
    assert _directory({"error": "unauthorized"}, status_code=401).search("ana") == []


def test_available_reports_whether_search_can_work():
    assert _directory(CONTACTS).available() is True
    assert (
        EvolutionContactDirectory("", "", "openworker", post=lambda **kw: None).available()
        is False
    )


def test_search_contacts_helper_marks_who_is_already_allowed():
    # The shared helper the route uses: directory + allow-list → rows the UI can render
    # without duplicating anyone already authorized.
    directory = _directory(CONTACTS)
    rows = search_contacts(directory, "", allowed={"5511888888888"})
    assert [(r["number"], r["allowed"]) for r in rows] == [
        ("5511999999999", False),
        ("5511888888888", True),
    ]
    assert rows[0]["display"] == "+55 11 99999-9999"  # formatted for the UI


def test_search_contacts_without_a_directory_is_an_empty_result():
    assert search_contacts(None, "ana", allowed=set()) == []


def test_contact_is_hashable_and_comparable():
    a = Contact(number="5511999999999", name="Ana")
    assert a == Contact(number="5511999999999", name="Ana")
    assert len({a, Contact(number="5511999999999", name="Ana")}) == 1
