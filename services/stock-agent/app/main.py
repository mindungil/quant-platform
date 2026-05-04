from fastapi import FastAPI
from app.api.routes import router
from shared.health import install_health_endpoints

app = FastAPI(title="stock-agent", version="0.1.0")
install_health_endpoints(app, service="stock-agent")
app.include_router(router)
