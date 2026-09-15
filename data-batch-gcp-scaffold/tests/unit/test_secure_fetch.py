import socket

import pytest
from worker.secure_fetch import fetch_html, fetch_media, public_addresses


class Response:
    def __init__(self, status=200, headers=None, data=b"\xff\xd8\xffimage"):
        self.status = status
        self.headers = headers or {"Content-Type": "image/jpeg"}
        self.data = data

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, amount):
        return self.data[:amount]


class Connection:
    def __init__(self, host, port, address, timeout, responses, calls):
        calls.append((host, port, address, timeout))
        self.response = responses.pop(0)

    def request(self, method, path, headers):
        self.request_value = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        pass


def harness(responses, addresses=None):
    calls = []
    resolutions = []

    def resolver(host, port):
        resolutions.append((host, port))
        return tuple(addresses or ["93.184.216.34"])

    def factory(host, port, address, timeout):
        return Connection(host, port, address, timeout, responses, calls)

    return resolver, factory, calls, resolutions


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
    ],
)
def test_literal_private_reserved_and_mapped_addresses_are_rejected(address):
    with pytest.raises(ValueError, match="non-public"):
        public_addresses(address, 443)


def test_mixed_public_and_private_dns_answer_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="non-public"):
        public_addresses("example.test", 443)


def test_connection_uses_validated_address_and_original_host():
    resolver, factory, calls, resolutions = harness([Response()])
    result = fetch_media(
        "https://media.example.test/a.jpg?x=1",
        max_bytes=100,
        resolver=resolver,
        connection_factory=factory,
    )
    assert result.content_type == "image/jpeg"
    assert calls[0][0:3] == ("media.example.test", 443, "93.184.216.34")
    assert resolutions == [("media.example.test", 443)]


def test_redirect_is_resolved_and_validated_independently():
    resolver, factory, calls, resolutions = harness(
        [
            Response(302, {"Location": "https://cdn.example.test/a.png"}),
            Response(200, {"Content-Type": "image/png"}, b"\x89PNG\r\n\x1a\nrest"),
        ]
    )
    result = fetch_media(
        "https://media.example.test/a",
        max_bytes=100,
        resolver=resolver,
        connection_factory=factory,
    )
    assert result.final_url == "https://cdn.example.test/a.png"
    assert resolutions == [
        ("media.example.test", 443),
        ("cdn.example.test", 443),
    ]
    assert [call[0] for call in calls] == ["media.example.test", "cdn.example.test"]


def test_redirect_to_private_is_rejected_before_connection():
    responses = [Response(302, {"Location": "https://127.0.0.1/metadata"})]
    calls = []

    def factory(host, port, address, timeout):
        return Connection(host, port, address, timeout, responses, calls)

    with pytest.raises(ValueError, match="non-public"):
        fetch_media(
            "https://example.test/a",
            max_bytes=100,
            resolver=lambda host, port: (
                public_addresses(host, port)
                if host == "127.0.0.1"
                else ("93.184.216.34",)
            ),
            connection_factory=factory,
        )
    assert len(calls) == 1


def test_retry_re_resolves_and_rejects_dns_change():
    answers = iter(
        [("93.184.216.34",), ValueError("source host resolves to a non-public address")]
    )

    def resolver(_host, _port):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def factory(*_args):
        raise OSError("connect failed")

    with pytest.raises(ValueError, match="non-public"):
        fetch_media(
            "https://example.test/a.jpg",
            max_bytes=100,
            resolver=resolver,
            connection_factory=factory,
        )


def test_signature_and_bounded_read_are_enforced():
    resolver, factory, _, _ = harness(
        [Response(200, {"Content-Type": "image/jpeg"}, b"not jpeg")]
    )
    with pytest.raises(ValueError, match="signature"):
        fetch_media(
            "https://example.test/a.jpg",
            max_bytes=100,
            resolver=resolver,
            connection_factory=factory,
        )


def test_html_fetch_uses_same_pinned_redirect_transport():
    resolver, factory, calls, _ = harness(
        [Response(200, {"Content-Type": "text/html"}, b"<html>ok</html>")]
    )
    html, final_url = fetch_html(
        "https://justpaste.it/a",
        resolver=resolver,
        connection_factory=factory,
    )
    assert html == "<html>ok</html>"
    assert final_url == "https://justpaste.it/a"
    assert calls[0][2] == "93.184.216.34"


def test_provider_redirect_cannot_escape_host_allowlist():
    resolver, factory, calls, _ = harness([Response(302, {'Location': 'https://other.test/media'})])
    with pytest.raises(ValueError, match='not allowed'):
        fetch_html('https://hotscope.tv/video/abc', allowed_hosts={'hotscope.tv'},
                   resolver=resolver, connection_factory=factory)
    assert len(calls) == 1


def test_incomplete_response_cannot_be_accepted_as_complete():
    resolver, factory, _, _ = harness([Response(headers={'Content-Type': 'image/jpeg', 'Content-Length': '50'})])
    with pytest.raises(RuntimeError, match='incomplete'):
        fetch_media('https://example.test/media', max_bytes=100,
                    resolver=resolver, connection_factory=factory)
