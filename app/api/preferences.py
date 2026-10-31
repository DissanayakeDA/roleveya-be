from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Preference, User
from app.schemas.preferences import PreferencesIn, PreferencesOut

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("", response_model=PreferencesOut)
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = db.get(Preference, user.id)
    return prefs if prefs is not None else PreferencesOut()


@router.put("", response_model=PreferencesOut)
def save_preferences(body: PreferencesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = db.get(Preference, user.id)
    if prefs is None:
        prefs = Preference(user_id=user.id)
        db.add(prefs)
    for field, value in body.model_dump(mode="json").items():
        setattr(prefs, field, value)
    db.commit()
    db.refresh(prefs)
    return prefs
