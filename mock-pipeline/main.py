import os

from fastapi import FastAPI

SERVICE_NAME = "mock-pipeline"

app = FastAPI(title=SERVICE_NAME)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
