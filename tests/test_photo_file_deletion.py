"""Fyzický súbor fotky sa maže až po úspešnom commite, nie pri rollbacku."""

import os
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import uploads_utils
from database import Base
from models import Customer, Job, JobPhoto


@pytest.fixture
def db_and_calls(monkeypatch):

    calls = []
    monkeypatch.setattr(uploads_utils, "delete_job_photo", lambda filename: calls.append(filename))

    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    customer = Customer(name="Z")
    session.add(customer)
    session.flush()
    job = Job(title="J", status="Nová", customer_id=customer.id)
    session.add(job)
    session.flush()
    photo = JobPhoto(job_id=job.id, filename="foto1.jpg", uploaded_at=datetime.now(timezone.utc))
    session.add(photo)
    session.commit()

    yield session, photo, calls

    session.close()


def test_file_is_deleted_only_after_commit(db_and_calls):

    session, photo, calls = db_and_calls

    session.delete(photo)
    session.flush()

    assert calls == []          # transakcia ešte nie je potvrdená

    session.commit()

    assert calls == ["foto1.jpg"]


def test_rollback_keeps_the_file_and_the_row(db_and_calls):

    session, photo, calls = db_and_calls

    session.delete(photo)
    session.flush()
    session.rollback()

    assert calls == []
    assert session.query(JobPhoto).count() == 1

    # po rollbacku sa starý záznam nesmie "vybaviť" pri nasledujúcom commite
    session.commit()

    assert calls == []


def test_deleting_whole_job_removes_all_its_files_after_commit(db_and_calls):

    session, photo, calls = db_and_calls

    session.add(JobPhoto(job_id=photo.job_id, filename="foto2.jpg", uploaded_at=datetime.now(timezone.utc)))
    session.commit()

    session.delete(session.get(Job, photo.job_id))
    session.commit()

    assert sorted(calls) == ["foto1.jpg", "foto2.jpg"]
