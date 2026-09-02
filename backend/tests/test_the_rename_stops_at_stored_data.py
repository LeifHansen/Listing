"""Two `quickflip`/`qf` strings are data formats, not branding.

The product is Thryft Shop; the code was written as QuickFlip. Finishing that
rename is a housekeeping task -- except for two strings, where the old name is
not a label anyone reads but a value that is already written down somewhere
this code does not control:

  * `crypto._INFO` is the HKDF info string that derives the token-encryption
    key from SECRET_KEY. Change it and a different key comes out, so every
    marketplace refresh token in the database stops decrypting. It fails
    QUIETLY, by design: `decrypt` returns "" so a page cannot be taken down by
    a bad row, which here means every connected seller is silently signed out
    of eBay, Etsy and Depop at once, with no error and no way back except
    re-authorizing. Nothing in the app would report it.

  * `publish_guard`'s `qf-` prefix is stamped onto live eBay listings as
    `Item.SKU`, with InventoryTrackingMethod=SKU, on every fixed-price create.
    It is how a publish whose response was lost is recognised as ours on the
    next store sync (see listing_sync._index_by_publish_key). eBay holds the
    old value on listings already up; changing the prefix here matches none of
    them, and each one imports again as a second card for an item the seller
    already has.

Both break for accounts that existed before the release and work perfectly in
every test written after it, which is precisely the shape a rename slips
through in. So they are pinned here, with the reason attached, rather than
left to a reviewer noticing a find-and-replace went one file too far.

If either genuinely has to move, it needs a migration -- re-encrypt every
stored token under the new derivation, or index both prefixes during the
sync -- and this test should be updated as part of it, not before.
"""
from backend import config, crypto
from backend.services import listing_sync, publish_guard


# Encrypted with the code as it stands, under the secret below. This is a
# fabricated token, not a seller's: it only has to be a string that round
# trips. Fernet carries its own timestamp and `decrypt` sets no TTL, so this
# stays valid indefinitely -- like the rows in the database it stands in for.
SECRET = "a-known-secret-for-the-pin-test"
STORED_TOKEN = (
    "enc:v1:gAAAAABqk_nru8FTcxZDqdwhNdZgiMNtzz30WsgGCTWjovMqw3pZYqWc0sfqRToO"
    "aWj136FXhytDe3A5vqbSTxUdPCZbZBGuRBV5VTOxAxYBX5RquNvhx-PfcDi_5YgwW-0eBhn"
    "FGz49eFRv9aKpTX77WqhAgzXg3g=="
)
PLAINTEXT = "v^1.1#i^1#f^0#refresh-token-from-before-the-rename"


def _with_secret(monkeypatch, secret: str) -> None:
    monkeypatch.setattr(config, "SECRET_KEY", secret)
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "")
    crypto.reset_cache()


def test_a_token_stored_before_the_rename_still_decrypts(monkeypatch):
    """The whole point: yesterday's ciphertext, today's code."""
    _with_secret(monkeypatch, SECRET)
    try:
        assert crypto.decrypt(STORED_TOKEN) == PLAINTEXT
    finally:
        crypto.reset_cache()


def test_the_key_derivation_is_not_a_place_to_rename(monkeypatch):
    """Names the failure directly, so the pin above cannot be read as flaky.

    Deriving under a renamed info string produces a cipher that returns "" for
    the same stored token -- the silent sign-out described at the top.
    """
    _with_secret(monkeypatch, SECRET)
    try:
        monkeypatch.setattr(crypto, "_INFO", b"thryft/marketplace-refresh-token/v1")
        crypto.reset_cache()
        assert crypto.decrypt(STORED_TOKEN) == ""
    finally:
        crypto.reset_cache()


def test_a_sku_already_on_ebay_is_still_recognised_as_ours():
    """A listing published before the rename, coming back from GetItem.

    The SKU is the literal string eBay is holding. The index has to build the
    same one from the record, or the sync treats the app's own listing as a
    stranger's and imports a duplicate.
    """
    record = {"id": "sess-abc123", "listing": {"title": "A trailer park lamp"}}
    sku_ebay_has = "qf-sess-abc123"

    index = listing_sync._index_by_publish_key([record])

    assert sku_ebay_has in index
    assert index[sku_ebay_has] is record


def test_a_relist_sku_from_before_the_rename_is_recognised_too():
    record = {"id": "sess-abc123", "listing": {"ebay_listing_id": "110011223344"}}

    index = listing_sync._index_by_publish_key([record])

    assert "qf-sess-abc123-r110011223344" in index


def test_the_publish_prefix_is_the_one_ebay_was_given():
    # Belt and braces: the two tests above would also pass if the prefix and
    # the index changed together, which is exactly what a careful rename does.
    assert publish_guard.idempotency_key("sess-abc123") == "qf-sess-abc123"
