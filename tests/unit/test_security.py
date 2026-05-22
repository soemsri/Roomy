import pytest
from unittest.mock import MagicMock
import security
import models

def test_password_hashing():
    password = "MySuperSecretPassword123"
    hashed = security.hash_password(password)
    
    assert hashed != password
    assert security.verify_password(password, hashed) is True
    assert security.verify_password("wrong_password", hashed) is False
    assert security.verify_password(password, "") is False
    assert security.verify_password(password, None) is False

def test_encryption_decryption():
    plain_text = "Thasapol Saetang 12345"
    encrypted = security.encrypt_value(plain_text)
    
    assert encrypted != plain_text
    assert security.decrypt_value(encrypted) == plain_text
    
    # Test empty or none inputs
    assert security.encrypt_value("") == ""
    assert security.encrypt_value(None) == ""
    assert security.decrypt_value("") == ""
    assert security.decrypt_value(None) == ""

def test_decrypt_invalid_value():
    invalid_encrypted = "this_is_not_a_valid_fernet_token"
    assert security.decrypt_value(invalid_encrypted) == ""

def test_system_config_crud(db_session):
    # Test set config
    key = "TEST_CONFIG_KEY"
    val = "SuperSecureAPISecretToken"
    desc = "This is a unit test config key"
    
    security.set_system_config(db_session, key, val, desc)
    
    # Verify in DB
    db_config = db_session.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    assert db_config is not None
    assert db_config.description == desc
    assert db_config.value != val  # should be encrypted
    
    # Test get config
    retrieved_val = security.get_system_config(db_session, key)
    assert retrieved_val == val
    
    # Test overwrite config
    new_val = "AnotherSecret"
    security.set_system_config(db_session, key, new_val)
    assert security.get_system_config(db_session, key) == new_val
    
    # Test fallback to environment or default
    assert security.get_system_config(db_session, "NON_EXISTENT_KEY", default="my_fallback") == "my_fallback"
