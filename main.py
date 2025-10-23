from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.router import api_router
from db.config import lifespan_context


app = FastAPI(title="Grepp API (FastAPI Edition)", lifespan=lifespan_context)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # ["https://veronicas-awesome-frontend.com"],
    allow_credentials=True,
    allow_methods=["GET","POST","PUT","DELETE","PATCH"],
    allow_headers=["*"],
)



app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
