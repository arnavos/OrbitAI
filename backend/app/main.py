from fastapi import FastAPI

app = FastAPI(
    title="ORBIT Local Backend",
    version="0.1.0",
    docs_url=None,  # Disabled for security in desktop environment
    redoc_url=None,
)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "stage": "0.2-scaffold",
        "version": "0.1.0"
    }
