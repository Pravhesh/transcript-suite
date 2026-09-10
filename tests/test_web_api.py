"""
FastAPI endpoints and Web UI asset test.
"""

from fastapi.testclient import TestClient
from transcript_suite.web.app import app

def test_web_endpoints():
    client = TestClient(app)

    # 1. Test root UI
    res = client.get("/")
    assert res.status_code == 200
    assert "Transcript Suite" in res.text
    assert "Foggy Woodland" in res.text
    print("✓ Root Web UI endpoint served successfully.")

    # 2. Test VRAM telemetry
    res_vram = client.get("/api/vram")
    assert res_vram.status_code == 200
    data = res_vram.json()
    assert "total_gb" in data
    print(f"✓ /api/vram returned telemetry: {data}")

    # 3. Test static assets
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    assert "foggy-woodland" in res_css.text
    print("✓ Static CSS stylesheet loaded successfully.")

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    print("✓ Static JS script loaded successfully.")

if __name__ == "__main__":
    test_web_endpoints()
    print("\nAll Web API tests passed!")
