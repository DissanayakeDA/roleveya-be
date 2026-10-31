from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User


def upsert_user(db: Session, attributes: dict[str, str]) -> User:
    """Creates or refreshes the local user row from Cognito attributes."""
    sub = attributes["sub"]
    user = db.scalar(select(User).where(User.cognito_sub == sub))
    if user is None:
        user = User(cognito_sub=sub)
        db.add(user)
    user.email = attributes.get("email", user.email or "").lower()
    user.phone_number = attributes.get("phone_number") or user.phone_number
    user.full_name = attributes.get("name") or user.full_name
    try:
        db.commit()
    except IntegrityError:
        # Two first requests for the same new user raced; the other one won.
        db.rollback()
        user = db.scalar(select(User).where(User.cognito_sub == sub))
        if user is None:
            raise
    return user
