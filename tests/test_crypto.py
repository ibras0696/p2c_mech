from app.core.crypto import SecretCipher, generate_encryption_key


def test_secret_cipher_roundtrip() -> None:
    cipher = SecretCipher(generate_encryption_key())

    encrypted = cipher.encrypt("secret-value")

    assert encrypted != "secret-value"
    assert cipher.decrypt(encrypted) == "secret-value"
