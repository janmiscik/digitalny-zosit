from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict


# =========================================
# JOB STATUS
# =========================================

class JobStatus(str, Enum):

    NEW = "Nová"
    AGREED = "Dohodnutá"
    IN_PROGRESS = "Prebieha"
    DONE = "Hotová"


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

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# JOB
# =========================================

class JobCreate(BaseModel):

    title: str
    description: str | None = None
    status: JobStatus = JobStatus.NEW
    customer_id: int
    due_date: date | None = None


class JobUpdate(BaseModel):

    title: str
    description: str | None = None
    status: JobStatus = JobStatus.NEW
    due_date: date | None = None


class JobRead(BaseModel):

    id: int
    title: str
    description: str | None = None
    status: JobStatus
    customer_id: int
    due_date: date | None = None

    model_config = ConfigDict(
        from_attributes=True
    )