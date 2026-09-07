def test_tools_is_a_real_package_not_a_namespace_package():
    # A bare directory named "tools" imports fine as an implicit namespace
    # package, so asserting on import alone proves nothing. __file__ is None
    # for a namespace package and set for a real one.
    import tools

    assert tools.__file__ is not None
