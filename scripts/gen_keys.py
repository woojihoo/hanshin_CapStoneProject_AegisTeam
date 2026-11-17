"""운영용 키 생성. 출력을 .env 에 붙여 넣는다:  python scripts/gen_keys.py > .env"""
import secrets

print(f"WM_MASTER_KEY={secrets.token_hex(32)}")
print(f"WM_TOKEN_HMAC_KEY={secrets.token_hex(32)}")
print(f"WM_DB_ENC_KEY={secrets.token_hex(32)}")
print(f"WM_ADMIN_API_KEY={secrets.token_urlsafe(24)}")
