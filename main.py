from fastapi import FastAPI, Depends, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import Customer
from schemas import CustomerCreate


Base.metadata.create_all(bind=engine)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    customers = db.query(Customer).all()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "customers": customers
        }
    )


@app.post("/customers")
def create_customer(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db)
):
    new_customer = Customer(
        name=name,
        phone=phone,
        email=email,
        address=address,
        note=note
    )

    db.add(new_customer)
    db.commit()

    return RedirectResponse(
        url="/",
        status_code=303
    )


@app.get("/customers")
def get_customers(db: Session = Depends(get_db)):
    customers = db.query(Customer).all()

    return customers