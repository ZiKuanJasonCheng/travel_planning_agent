from fastapi import APIRouter
from api import apis

router = APIRouter()
router.include_router(apis.router)