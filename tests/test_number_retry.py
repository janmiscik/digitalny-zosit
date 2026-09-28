"""Testy pre invoice_utils.commit_with_number_retry()."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from invoice_utils import commit_with_number_retry

Base = declarative_base()


class Doc(Base):
    __tablename__ = "docs"

    id = Column(Integer, primary_key=True)
    number = Column(String, unique=True)
    required = Column(String, nullable=False)
    items = relationship("DocItem", cascade="all, delete-orphan")


class DocItem(Base):
    __tablename__ = "doc_items"

    id = Column(Integer, primary_key=True)
    doc_id = Column(Integer, ForeignKey("docs.id"))
    text = Column(String)


@pytest.fixture
def db():

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    session = sessionmaker(bind=engine)()

    yield session

    session.close()


def test_retry_saves_document_with_items_after_number_collision(db):

    db.add(Doc(number="1", required="x"))
    db.commit()

    doc = Doc(number="1", required="x")
    doc.items.append(DocItem(text="a"))
    doc.items.append(DocItem(text="b"))
    db.add(doc)

    def regenerate():
        doc.number = "2"

    commit_with_number_retry(db, regenerate, doc)

    saved = db.query(Doc).filter(Doc.number == "2").one()

    assert [item.text for item in saved.items] == ["a", "b"]


def test_non_unique_integrity_error_is_not_retried(db):

    doc = Doc(number="1", required=None)
    db.add(doc)

    calls = []

    def regenerate():
        calls.append(1)

    with pytest.raises(IntegrityError):
        commit_with_number_retry(db, regenerate, doc)

    assert calls == []


def test_gives_up_after_max_attempts(db):

    db.add(Doc(number="1", required="x"))
    db.commit()

    doc = Doc(number="1", required="x")
    db.add(doc)

    with pytest.raises(IntegrityError):
        commit_with_number_retry(db, lambda: None, doc)
