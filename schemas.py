from pydantic import BaseModel


class CustomerCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    note: str | None = None


class JobCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "Nová"
    customer_id: int