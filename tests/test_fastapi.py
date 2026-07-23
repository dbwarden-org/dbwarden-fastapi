def test_import():
    from dbwarden_fastapi import setup
    assert callable(setup)
