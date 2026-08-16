from fastapi import FastAPI, Depends, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import Customer, Job


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


@app.get("/customers/{customer_id}")
def customer_detail(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(
        Customer.id == customer_id
    ).first()

    if customer is None:
        return {"error": "Zákazník neexistuje"}

    jobs = db.query(Job).filter(
        Job.customer_id == customer_id
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="customer.html",
        context={
            "customer": customer,
            "jobs": jobs
        }
    )


@app.post("/jobs")
def create_job(
    title: str = Form(...),
    description: str = Form(""),
    status: str = Form("Nová"),
    customer_id: int = Form(...),
    db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(
        Customer.id == customer_id
    ).first()

    if customer is None:
        return {"error": "Zákazník neexistuje"}

    new_job = Job(
        title=title,
        description=description,
        status=status,
        customer_id=customer_id
    )

    db.add(new_job)
    db.commit()

    return RedirectResponse(
        url=f"/customers/{customer_id}",
        status_code=303
    )


@app.post("/jobs/{job_id}/status")
def update_job_status(
    job_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(
        Job.id == job_id
    ).first()

    if job is None:
        return {"error": "Zákazka neexistuje"}

    job.status = status

    db.commit()

    return RedirectResponse(
        url=f"/customers/{job.customer_id}",
        status_code=303
    )


@app.get("/customers")
def get_customers(db: Session = Depends(get_db)):
    customers = db.query(Customer).all()

    return customers


@app.get("/jobs")
def get_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).all()

    return jobs