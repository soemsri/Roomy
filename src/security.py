import os
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
import models
from dotenv import load_dotenv

load_dotenv()

# Ensure we have an encryption key in .env
MASTER_KEY = os.getenv("MASTER_ENCRYPTION_KEY")
if not MASTER_KEY:
    # Generate one if not exists (This should ideally be done once and kept secret)
    MASTER_KEY = Fernet.generate_key().decode()
    with open(".env", "a") as f:
        f.write(f"\nMASTER_ENCRYPTION_KEY={MASTER_KEY}\n")
    print("Generated new MASTER_ENCRYPTION_KEY and saved to .env")

cipher_suite = Fernet(MASTER_KEY.encode())

def encrypt_value(plain_text: str) -> str:
    if not plain_text: return ""
    return cipher_suite.encrypt(plain_text.encode()).decode()

def decrypt_value(encrypted_text: str) -> str:
    if not encrypted_text: return ""
    try:
        return cipher_suite.decrypt(encrypted_text.encode()).decode()
    except Exception as e:
        print(f"Decryption error: {e}")
        return ""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password: return False
    return pwd_context.verify(plain_password, hashed_password)

def get_system_config(db: Session, key: str, default=None) -> str:
    """
    Get configuration from database (encrypted).
    """
    config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if config:
        return decrypt_value(config.value)
    
    # Optional: Fallback to environment variable during transition
    return os.getenv(key, default)

def set_system_config(db: Session, key: str, value: str, description: str = None):
    """
    Save configuration to database (encrypted).
    """
    encrypted_val = encrypt_value(value)
    config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if config:
        config.value = encrypted_val
        if description: config.description = description
    else:
        config = models.SystemConfig(key=key, value=encrypted_val, description=description)
        db.add(config)
    db.commit()
