"""Tests for Resource and ResourceSet."""

from hpc_runner.core.resources import Resource, ResourceSet


class TestResource:
    """Tests for Resource class."""

    def test_resource_creation(self):
        """Test creating a resource."""
        r = Resource("gpu", 2)
        assert r.name == "gpu"
        assert r.value == 2

    def test_resource_with_string_value(self):
        """Test resource with string value."""
        r = Resource("mem", "16G")
        assert r.name == "mem"
        assert r.value == "16G"


class TestResourceSet:
    """Tests for ResourceSet class."""

    def test_empty_resource_set(self):
        """Test empty resource set."""
        rs = ResourceSet()
        assert len(rs) == 0
        assert not rs  # Falsy when empty

    def test_add_resources(self):
        """Test adding resources."""
        rs = ResourceSet()
        rs.add("gpu", 2)
        rs.add("license", 1)

        assert len(rs) == 2
        assert rs  # Truthy when not empty

    def test_add_chaining(self):
        """Test that add() returns self for chaining."""
        rs = ResourceSet()
        result = rs.add("gpu", 1).add("mem", "8G")

        assert result is rs
        assert len(rs) == 2

    def test_get_resource(self):
        """Test getting a resource by name."""
        rs = ResourceSet()
        rs.add("gpu", 2)
        rs.add("license", 1)

        gpu = rs.get("gpu")
        assert gpu is not None
        assert gpu.value == 2

        missing = rs.get("missing")
        assert missing is None

    def test_iteration(self):
        """Test iterating over resources."""
        rs = ResourceSet()
        rs.add("gpu", 2)
        rs.add("license", 1)

        names = [r.name for r in rs]
        assert "gpu" in names
        assert "license" in names
