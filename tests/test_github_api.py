import http.client

import pytest

from github_api import GitHubApi, GitHubApiError, Response


class Transport:
    """Scripted transport: each call pops the next response (or raises it if it is an exception)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, follow_redirects):
        self.calls.append({"url": url, "headers": dict(headers), "follow_redirects": follow_redirects})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def json_response(body: str, status: int = 200) -> Response:
    return Response(status, {"Content-Type": "application/json"}, body.encode())


def api(transport):
    return GitHubApi("secret-token", transport=transport, sleep=lambda seconds: None)


def test_get_json_sends_headers():
    transport = Transport(json_response('{"ok": true}'))
    assert api(transport).get_json("repos/o/r", {"a": 1}) == {"ok": True}
    call = transport.calls[0]
    assert call["url"] == "https://api.github.com/repos/o/r?a=1"
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["headers"]["X-GitHub-Api-Version"] == "2022-11-28"


def test_list_artifacts_paginates():
    first = '{"total_count": 101, "artifacts": [' + ",".join(f'{{"id": {i}}}' for i in range(100)) + "]}"
    transport = Transport(json_response(first), json_response('{"total_count": 101, "artifacts": [{"id": 100}]}'))
    artifacts = api(transport).list_artifacts("o/r", "nightly-dashboard-data")
    assert [artifact["id"] for artifact in artifacts] == list(range(101))
    assert "page=2" in transport.calls[1]["url"] and "name=nightly-dashboard-data" in transport.calls[1]["url"]


def test_retries_server_errors_and_network_errors():
    transport = Transport(json_response("", 502), http.client.RemoteDisconnected("closed"), json_response('{"ok": 1}'))
    assert api(transport).get_json("x") == {"ok": 1}
    assert len(transport.calls) == 3


def test_gives_up_after_retries():
    transport = Transport(json_response("", 503), json_response("", 503), json_response("", 503))
    with pytest.raises(GitHubApiError) as error:
        api(transport).get_json("x")
    assert error.value.status == 503


def test_no_retry_on_client_errors():
    transport = Transport(json_response('{"message": "Not Found"}', 404))
    with pytest.raises(GitHubApiError) as error:
        api(transport).get_json("x")
    assert error.value.status == 404 and len(transport.calls) == 1


def test_invalid_json():
    with pytest.raises(GitHubApiError):
        api(Transport(json_response("not json"))).get_json("x")


def test_download_follows_redirect_without_authorization():
    transport = Transport(Response(302, {"Location": "https://blob.example.com/file.zip?sig=1"}, b""), Response(200, {}, b"ZIPDATA"))
    assert api(transport).download_artifact_zip("o/r", 5) == b"ZIPDATA"
    first, second = transport.calls
    assert first["url"] == "https://api.github.com/repos/o/r/actions/artifacts/5/zip" and first["follow_redirects"] is False
    assert first["headers"]["Authorization"] == "Bearer secret-token"
    assert second["url"] == "https://blob.example.com/file.zip?sig=1"
    assert "Authorization" not in second["headers"]


def test_download_direct_body_and_errors():
    assert api(Transport(Response(200, {}, b"Z"))).download_artifact_zip("o/r", 1) == b"Z"
    with pytest.raises(GitHubApiError) as error:
        api(Transport(Response(403, {}, b"forbidden"))).download_artifact_zip("o/r", 1)
    assert error.value.status == 403
    with pytest.raises(GitHubApiError):
        api(Transport(Response(302, {}, b""))).download_artifact_zip("o/r", 1)


def test_anonymous_requests_have_no_authorization():
    transport = Transport(json_response("{}"))
    GitHubApi(None, transport=transport).get_json("x")
    assert "Authorization" not in transport.calls[0]["headers"]
