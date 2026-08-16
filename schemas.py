from datetime import date

from pydantic import BaseModel


# =========================================
# CUSTOMER
# =========================================

class CustomerCreate(BaseModel):

    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    note: str | None = None


class CustomerUpdate(BaseModel):

    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    note: str | None = None


class CustomerRead(BaseModel):

    id: int
    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    note: str | None = None

    class Config:
        from_attributes = True


# =========================================
# JOB
# =========================================

class JobCreate(BaseModel):

    title: str
    description: str | None = None
    status: str = "Nová"
    customer_id: int
    due_date: date | None = None


class JobUpdate(BaseModel):

    title: str
    description: str | None = None
    status: str = "Nová"
    due_date: date | None = None


class JobRead(BaseModel):

    id: int
    title: str
    description: str | None = None
    status: str
    customer_id: int
    due_date: date | None = None

    class Config:
        from_attributes = True