import os
os.environ["SECRET_KEY"]="test-secret"
os.environ["DATABASE_URL"]="sqlite:///:memory:"
from app import app

def test_health():
    client=app.test_client()
    r=client.get("/health")
    assert r.status_code==200
    assert r.json["status"]=="ok"
