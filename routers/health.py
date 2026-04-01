from fastapi import APIRouter

router = APIRouter(tags=["System"])

@router.get("/")
def read_root():
    return {
        "name": "Chess Coach API",
        "version": "1.0",
        "endpoints": {
            "health": "/health"
        }
    }

@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "message": "API is running"
    }
