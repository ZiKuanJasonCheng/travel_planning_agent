from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from api import router


def get_application() -> FastAPI:

    @asynccontextmanager
    async def lifespan(_app: FastAPI):

        yield
        from db.connection import dispose_engine
        dispose_engine()

    
    app = FastAPI(title="Trip Planning App with Agents", lifespan=lifespan)  #**settings.fastapi_kwargs

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True
    )


    app.include_router(router, tags=["trip"])

    return app


app = get_application()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=9988)