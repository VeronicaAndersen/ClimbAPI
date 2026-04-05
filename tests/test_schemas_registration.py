"""Unit tests for schema/registration.py — level constraints and approval flag."""
import pytest
from pydantic import ValidationError

from schema.registration import (
    RegistrationCreate,
    RegistrationLevelUpdate,
    RegistrationApprovalUpdate,
)


class TestRegistrationCreate:
    def test_valid_level_1(self):
        r = RegistrationCreate(level=1)
        assert r.level == 1

    def test_valid_level_7(self):
        r = RegistrationCreate(level=7)
        assert r.level == 7

    def test_all_valid_levels(self):
        for lvl in range(1, 8):
            r = RegistrationCreate(level=lvl)
            assert r.level == lvl

    def test_level_zero_raises(self):
        with pytest.raises(ValidationError):
            RegistrationCreate(level=0)

    def test_level_eight_raises(self):
        with pytest.raises(ValidationError):
            RegistrationCreate(level=8)

    def test_negative_level_raises(self):
        with pytest.raises(ValidationError):
            RegistrationCreate(level=-1)

    def test_missing_level_raises(self):
        with pytest.raises(ValidationError):
            RegistrationCreate()


class TestRegistrationLevelUpdate:
    def test_valid_level_3(self):
        r = RegistrationLevelUpdate(level=3)
        assert r.level == 3

    def test_valid_boundary_1(self):
        r = RegistrationLevelUpdate(level=1)
        assert r.level == 1

    def test_valid_boundary_7(self):
        r = RegistrationLevelUpdate(level=7)
        assert r.level == 7

    def test_level_zero_raises(self):
        with pytest.raises(ValidationError):
            RegistrationLevelUpdate(level=0)

    def test_level_eight_raises(self):
        with pytest.raises(ValidationError):
            RegistrationLevelUpdate(level=8)

    def test_missing_level_raises(self):
        with pytest.raises(ValidationError):
            RegistrationLevelUpdate()


class TestRegistrationApprovalUpdate:
    def test_approve(self):
        r = RegistrationApprovalUpdate(approved=True)
        assert r.approved is True

    def test_reject(self):
        r = RegistrationApprovalUpdate(approved=False)
        assert r.approved is False

    def test_missing_approved_raises(self):
        with pytest.raises(ValidationError):
            RegistrationApprovalUpdate()
