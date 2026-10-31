"""Registration, login, roles and account lockout.

SRS: SPMS-F-001 (register), F-002 (login + JWT), F-003 (role-based access), F-004 (lockout after 5 failed logins).
Also covers SR-002 (bcrypt password hashing) and SR-003 (JWT with 30-minute expiry).
"""
import datetime as dt
import hashlib
import os
import re
import secrets

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend.db import Base, get_db
from backend.ratelimit import rate_limit

SECRET_KEY = os.getenv("SPMS_SECRET_KEY", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
TOKEN_MINUTES = 30
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
DEFAULT_BCRYPT_ROUNDS = 12  # SR-002: cost >= 12 in every real deployment
BCRYPT_ROUNDS = int(os.getenv("SPMS_BCRYPT_ROUNDS", str(DEFAULT_BCRYPT_ROUNDS)))  # tests use 4 to run faster
REFRESH_DAYS = 7

DRIVER, ATTENDANT, ADMIN = "DRIVER", "ATTENDANT", "ADMIN"


def utcnow() -> dt.datetime:
    """Current UTC time without tzinfo (SQLite stores naive datetimes)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(15), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default=DRIVER)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class RefreshToken(Base):
    """SR-003: long-lived refresh tokens, stored only as SHA-256 hashes, rotated on every use."""
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class RevokedToken(Base):
    """SR-003: access tokens (by their jti) revoked at logout, until they would have expired anyway."""
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


# ---------- schemas ----------
class RegisterIn(BaseModel):
    name: str
    email: str
    phone: str
    password: str

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = v.strip()
        if len(v) < 2 or len(v) > 100:
            raise ValueError("name must have 2 to 100 characters")
        if any(ch in v for ch in "<>"):  # SR-004: no markup in free text
            raise ValueError("name must not contain < or >")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email address")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        v = v.strip()
        if not re.fullmatch(r"\d{10}", v):
            raise ValueError("phone must be 10 digits")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v):
        if len(v) < 6:
            raise ValueError("password must have at least 6 characters")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str


# ---------- helpers ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "jti": secrets.token_hex(16),  # lets a single token be revoked at logout
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_refresh_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(RefreshToken(user_id=user.id, token_hash=_hash_refresh(token),
                        expires_at=utcnow() + dt.timedelta(days=REFRESH_DAYS)))
    return token


def token_pair(db: Session, user: User) -> dict:
    refresh = issue_refresh_token(db, user)
    db.commit()
    return {"access_token": create_token(user), "refresh_token": refresh, "token_type": "bearer",
            "role": user.role, "name": user.name}


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    """Dependency: returns the logged-in user from the Bearer token, or 401."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if payload.get("jti") and db.get(RevokedToken, payload["jti"]) is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked (logged out)")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


def require_roles(*roles: str):
    """Dependency factory: allow only the given roles (SPMS-F-003)."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


def seed_default_users(db: Session) -> None:
    """Create the demo admin and attendant accounts if they do not exist yet."""
    defaults = [
        ("System Admin", "admin@spms.local", "9000000001", "admin123", ADMIN),
        ("Gate Attendant", "attendant@spms.local", "9000000002", "attend123", ATTENDANT),
    ]
    for name, email, phone, password, role in defaults:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(User(name=name, email=email, phone=phone, password_hash=hash_password(password), role=role))
    db.commit()


# ---------- routes ----------
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201,
             dependencies=[Depends(rate_limit("register", 10))])
def register(data: RegisterIn, db: Session = Depends(get_db)):
    """SPMS-F-001: register a new driver account."""
    if db.scalar(select(User).where(User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")
    if db.scalar(select(User).where(User.phone == data.phone)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Phone number is already registered")
    user = User(name=data.name, email=data.email, phone=data.phone,
                password_hash=hash_password(data.password), role=DRIVER)
    db.add(user)
    db.commit()
    return UserOut(id=user.id, name=user.name, email=user.email, role=user.role)


@router.post("/login", dependencies=[Depends(rate_limit("login", 10))])
def login(data: LoginIn, db: Session = Depends(get_db)):
    """SPMS-F-002 + F-004: log in and get a JWT; lock the account after 5 failed attempts."""
    user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Account locked after too many failed logins. Try again later.")
    if user is None or not verify_password(data.password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    return token_pair(db, user)


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=100)


class LogoutIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=100)


@router.post("/refresh", dependencies=[Depends(rate_limit("refresh", 30))])
def refresh(data: RefreshIn, db: Session = Depends(get_db)):
    """SR-003: swap a refresh token for a new access + refresh token. Each refresh token works once;
    re-using an old one revokes all of the user's refresh tokens (possible theft)."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(data.refresh_token)))
    if row is None or row.expires_at < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    if row.revoked:
        db.execute(update(RefreshToken).where(RefreshToken.user_id == row.user_id).values(revoked=True))
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token already used - please log in again")
    row.revoked = True
    user = db.get(User, row.user_id)
    return token_pair(db, user)


@router.post("/logout", status_code=204)
def logout(data: LogoutIn | None = None, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """SR-003: revoke the current access token and (if given) the refresh token."""
    payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    if payload.get("jti"):
        db.add(RevokedToken(jti=payload["jti"],
                            expires_at=dt.datetime.fromtimestamp(payload["exp"], dt.timezone.utc).replace(tzinfo=None)))
    if data and data.refresh_token:
        db.execute(update(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(data.refresh_token),
                                              RefreshToken.user_id == user.id).values(revoked=True))
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """Return the logged-in user's details."""
    return UserOut(id=user.id, name=user.name, email=user.email, role=user.role)


@router.get("/admin-check")
def admin_check(user: User = Depends(require_roles(ADMIN))):
    """Small endpoint used to demonstrate and test role-based access (F-003)."""
    return {"ok": True, "role": user.role}
