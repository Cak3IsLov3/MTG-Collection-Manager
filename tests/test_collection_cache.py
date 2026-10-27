"""The collection page is kept until the database changes, and the browser may keep its copy too."""

from app.services import collection


def test_the_collection_page_is_built_once_until_something_changes(client, monkeypatch):
    built = []
    real = collection.collection_rows
    monkeypatch.setattr(collection, "collection_rows", lambda *args, **kwargs: built.append(1) or real(*args, **kwargs))

    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    first = client.get("/collection")
    assert first.status_code == 200 and "Lim-Dûl&#39;s Vault" in first.text and len(built) == 2
    assert client.get("/collection").text == first.text and len(built) == 2  # kept: no second build

    client.post("/api/collection", json={"name": "Sol Ring"})  # a commit: the page is out of date
    changed = client.get("/collection")
    assert "Sol Ring" in changed.text and len(built) > 2
    now = len(built)
    assert client.get("/collection", params={"msg": "Added Sol Ring"}).text != changed.text  # a message is part of the page
    assert len(built) > now


def test_the_browser_is_told_when_its_copy_is_still_good(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    page = client.get("/collection")
    etag = page.headers["etag"]
    assert etag.startswith('W/"') and page.headers["cache-control"] == "no-cache"
    same = client.get("/collection", headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.content == b"" and same.headers["etag"] == etag
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    newer = client.get("/collection", headers={"If-None-Match": etag})
    assert newer.status_code == 200 and newer.headers["etag"] != etag and "Lim-D" in newer.text
