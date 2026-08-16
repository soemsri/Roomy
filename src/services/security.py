import os
import logging
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# Ensure we have an encryption key in .env
MASTER_KEY = os.getenv("MASTER_ENCRYPTION_KEY")
if not MASTER_KEY:
    # Generate one if not exists (This should ideally be done once and kept secret)
    MASTER_KEY = Fernet.generate_key().decode()
    with open(".env", "a") as f:
        f.write(f"\nMASTER_ENCRYPTION_KEY={MASTER_KEY}\n")
    logger.info("Generated new MASTER_ENCRYPTION_KEY and saved to .env")

cipher_suite = Fernet(MASTER_KEY.encode())

def encrypt_value(plain_text: str) -> str:
    """
    Encrypts a plain text string using the Fernet symmetric encryption key (MASTER_ENCRYPTION_KEY).
    
    Args:
        plain_text (str): The raw string to be encrypted (e.g., citizen_id, passwords).
        
    Returns:
        str: The base64 encrypted cipher text, or an empty string if plain_text is empty or falsy.
    """
    if not plain_text: return ""
    return cipher_suite.encrypt(plain_text.encode()).decode()

def decrypt_value(encrypted_text: str) -> str:
    """
    Decrypts a base64 ciphertext string using the Fernet symmetric encryption key.
    
    Args:
        encrypted_text (str): The encrypted base64 ciphertext.
        
    Returns:
        str: The decrypted raw string, or an empty string if decryption fails or argument is invalid.
    """
    if not isinstance(encrypted_text, str) or not encrypted_text: return ""
    try:
        return cipher_suite.decrypt(encrypted_text.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    """
    Hashes a plain text password using the secure PBKDF2-SHA256 hashing scheme.
    
    Args:
        password (str): The plain text password to hash.
        
    Returns:
        str: The secure, cryptographically hashed password string.
    """
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain text password against a hashed password using PBKDF2-SHA256.
    
    Args:
        plain_password (str): The raw, unhashed password submitted by the user.
        hashed_password (str): The hashed password stored in the database.
        
    Returns:
        bool: True if the passwords match, False otherwise.
    """
    if not hashed_password: return False
    return pwd_context.verify(plain_password, hashed_password)

def get_system_config(db: Session, key: str, default=None) -> str:
    """
    Retrieves a system configuration setting from the database and decrypts it.
    If not found in the database or decryption fails/is empty, falls back to retrieving from environment variables or default.
    """
    import models
    config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if config and config.value:
        dec = decrypt_value(config.value)
        if dec:
            return dec
    
    # Fallback to environment variable or default
    val = os.getenv(key)
    if val:
        return val
    return default

def set_system_config(db: Session, key: str, value: str, description: str = None):
    """
    Saves a system configuration setting to the database, encrypting its value first.
    If the key already exists, updates its value and description. Otherwise, inserts a new record.
    
    Args:
        db (Session): The active SQLAlchemy database session.
        key (str): The configuration key name (e.g., 'LINE_NOTIFY_TOKEN').
        value (str): The plain text value to encrypt and store.
        description (str, optional): A brief explanation of the key's purpose.
    """
    import models
    encrypted_val = encrypt_value(value)
    config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if config:
        config.value = encrypted_val
        if description: config.description = description
    else:
        config = models.SystemConfig(key=key, value=encrypted_val, description=description)
        db.add(config)
    db.commit()

def verify_google_id_token(id_token: str) -> dict:
    """
    Verifies a Google OAuth2 JWT ID token by making a secure HTTP request to Google's official tokeninfo endpoint.
    
    Args:
        id_token (str): The Google ID Token (JWT) sent by the browser.
        
    Returns:
        dict: The parsed and verified token claims (e.g., email, name, picture).
        
    Raises:
        ValueError: If token validation fails or Google API returns an error.
    """
    import requests
    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Error connecting to Google token verification: {e}")
    raise ValueError("Invalid Google ID Token")
