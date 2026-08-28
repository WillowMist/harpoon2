def test_smoke_arithmetic():
    """Trivial smoke test to prove pytest can collect and run from the project root.

    No DB access, no Django model imports, no env-var reading — runs in any environment.
    Real tests with DB access live alongside this file and use `@pytest.mark.django_db`.
    """
    assert 1 + 1 == 2
